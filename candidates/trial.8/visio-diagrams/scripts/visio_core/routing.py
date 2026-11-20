"""Joint port and orthogonal route selection for the diagram schema."""
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
import heapq
import itertools
import math

from .contracts import SIDES
from .edge_labels import label_support, route_tracks
from .errors import GenerationError
from .geometry import EPS, NORMALS, bend_count, crosses_box, overlaps_box_side, proper_crossing
from .plan_routes import on_track, overlap, point, simplify

CLEARANCE = .12
PORT_SPACING = .12
PREFERRED_PORT_SPACING = .25
BEND_COST = 1.5
STATE_LIMIT = 240000


@dataclass(frozen=True)
class Port:
    side: str
    fraction: float
    xy: tuple
    penalty: float


def center(node):
    return (node["x"] + node["w"] / 2, node["y"] + node["h"] / 2)


def make_port(node, side, fraction=.5, crowding=0):
    return Port(side, fraction, tuple(point(node, side, fraction)), .45 * abs(fraction - .5) + 2 * crowding)


def axial_direction(a, b):
    return tuple(int(d > EPS) - int(d < -EPS) for d in (b[0] - a[0], b[1] - a[1]))


def connector_port(edge, end, nodes):
    return make_port(nodes[edge[end]], edge[end + "Port"], edge.get(end + "Fraction", .5))


def port_options(edge, end, nodes, used, claimed):
    node = nodes[edge[end]]
    peer = nodes[edge["to" if end == "from" else "from"]]
    fixed = edge.get(end + "Port")
    options = []
    for side in (fixed,) if fixed else SIDES:
        key = (node["id"], side)
        occupied = used[key]
        if node["kind"] != "box":
            if not occupied and (fixed or key not in claimed):
                options.append(make_port(node, side))
            continue
        axis = 1 if side in ("left", "right") else 0
        origin, size = (node["y"], node["h"]) if axis else (node["x"], node["w"])
        projected = center(peer)[axis] - origin
        if not occupied:
            positions = [size / 2]
            if node is not peer and PORT_SPACING <= projected <= size - PORT_SPACING:
                positions.append(projected)
        else:
            saved = sorted(fraction * size for fraction in occupied)
            # Legal intervals between reserved ports, with room for distinctly glued points.
            intervals = zip([PORT_SPACING, *(value + PORT_SPACING for value in saved)],
                            [*(value - PORT_SPACING for value in saved), size - PORT_SPACING])
            positions = []
            for index, (low, high) in enumerate(intervals):
                if low <= high + EPS:
                    positions.extend((low, high, (low + high) / 2, min(high, max(low, projected)),
                                      min(high, max(low, size / 2))))
                    extra = PREFERRED_PORT_SPACING - PORT_SPACING
                    if index:
                        positions.append(min(high, low + extra))
                    if index < len(saved):
                        positions.append(max(low, high - extra))
            # Keep compact options on either side of center even when the peer is beyond one end.
            positions = sorted(set(round(value, 10) for value in positions))
            for side_positions in ([p for p in positions if p <= size / 2], [p for p in positions if p > size / 2]):
                closest = sorted(side_positions, key=lambda p: (abs(p - projected), abs(p - size / 2)))
                compact = sorted(side_positions, key=lambda p: (
                    abs(p - size / 2) + 2 * port_crowding(p, saved), p))
                best = dict.fromkeys(closest[:1] + compact[:2])
                options.extend(make_port(node, side, p / size, port_crowding(p, saved)) for p in best)
            continue
        options.extend(make_port(node, side, value / size) for value in dict.fromkeys(positions))
    if not options:
        raise GenerationError("PORT_CAPACITY", f"Node {node['id']!r} has no available connection point",
                              f"edge {edge['id']}.{end}Port", edgeId=edge["id"], nodeId=node["id"], side=fixed)
    return options


def port_crowding(position, occupied):
    return max(0, PREFERRED_PORT_SPACING - min(abs(position - other) for other in occupied))


class Channels:
    def __init__(self, page, blockers, edge, tracks, vertices):
        self.page = page
        self.blockers = blockers
        self.tracks = tracks
        self.vertices = vertices
        endpoints = (edge["from"], edge["to"])
        self.endpoint_boxes = [box for box in blockers if box["id"] in endpoints]
        self.padded = [box if box["id"] is None or box["id"] in endpoints else
                       dict(box, x=box["x"] - CLEARANCE, y=box["y"] - CLEARANCE,
                            w=box["w"] + 2 * CLEARANCE, h=box["h"] + 2 * CLEARANCE)
                       for box in blockers]
        self.guides = self.lanes()
        # Per-connector cache avoids rechecking a leg shared by several candidates.
        self.clear = lru_cache(maxsize=8192)(self.clear)
        self.on_line = lru_cache(maxsize=8192)(self.on_line)

    def lanes(self):
        axes = [{0, self.page["width"], .25, self.page["width"] - .25},
                {0, self.page["height"], .25, self.page["height"] - .25}]
        for box in self.padded:
            for values, pos, dim in zip(axes, ("x", "y"), ("w", "h")):
                values.update((box[pos], box[pos] + box[dim]))
        for box in self.endpoint_boxes:
            for values, pos, dim in zip(axes, ("x", "y"), ("w", "h")):
                for distance in (CLEARANCE / 2, CLEARANCE):
                    values.update((box[pos] - distance, box[pos] + box[dim] + distance))
        for x, y in self.vertices:
            axes[0].update((x - CLEARANCE, x, x + CLEARANCE))
            axes[1].update((y - CLEARANCE, y, y + CLEARANCE))
        return axes

    def on_line(self, p):
        return any(on_track(p, c, d) for _, c, d in self.tracks)

    def clear(self, a, b):
        if a == b or a[0] != b[0] and a[1] != b[1]:
            return False
        if any(not (-EPS <= x <= self.page["width"] + EPS and -EPS <= y <= self.page["height"] + EPS)
               for x, y in (a, b)):
            return False
        return (not any(crosses_box(a, b, box) for box in self.padded)
                and not any(overlaps_box_side(a, b, box) for box in self.endpoint_boxes)
                and not any(on_track(p, a, b) for p in self.vertices)
                and not any(overlap(a, b, c, d) for _, c, d in self.tracks))

    def paths(self, source, target):
        a, b = source.xy, target.xy
        sa, sb = NORMALS[source.side], NORMALS[target.side]
        if a == b or self.on_line(a) or self.on_line(b):
            return
        axis = 0 if sa[0] else 1
        other = 1 - axis
        if sb[axis] == 0:
            corner = (b[0], a[1]) if axis == 0 else (a[0], b[1])
            yield (a, corner, b)
            return
        if a[other] == b[other]:
            yield (a, b)
            return
        channels = self.guides[axis] | {(a[axis] + b[axis]) / 2}
        for position in (a[axis], b[axis]):
            channels |= {position + offset for offset in (-.50, -.30, -.18, .18, .30, .50)}
        for lane in sorted(channels):
            lane = round(lane, 10)
            if (lane - a[axis]) * sa[axis] <= EPS or (lane - b[axis]) * sb[axis] <= EPS:
                continue
            first = (lane, a[1]) if axis == 0 else (a[0], lane)
            last = (lane, b[1]) if axis == 0 else (b[0], lane)
            yield (a, first, last, b)

    def score(self, points, source, target):
        if (len(points) < 2 or len(set(points)) != len(points)
                or axial_direction(points[0], points[1]) != NORMALS[source.side]
                or axial_direction(points[-1], points[-2]) != NORMALS[target.side]
                or any(self.on_line(p) for p in points)):
            return None
        segments = list(zip(points, points[1:]))
        if not all(self.clear(a, b) for a, b in segments):
            return None
        crossings = sum(proper_crossing(a, b, c, d) for a, b in segments for _, c, d in self.tracks)
        lengths = [math.dist(a, b) for a, b in segments]
        cost = sum(lengths) + BEND_COST * (len(points) - 2)
        if len(lengths) > 1:
            cost += 1.7 * sum(max(0, .30 - length) for length in lengths)
        cost += source.penalty + target.penalty
        return crossings, round(cost, 8)

    def grid_route(self, sources, targets, consume):
        """Bounded multi-source A* for routes that need more than one channel."""
        axes = [set(axis) for axis in self.guides]
        for port in sources + targets:
            for i, value in enumerate(port.xy):
                axes[i].update((value - CLEARANCE, value, value + CLEARANCE))
        coordinates = [sorted({round(x, 10) for x in axis if 0 <= x <= self.page[dim] + EPS})
                       for dim, axis in zip(("width", "height"), axes)]
        indices = [{value: i for i, value in enumerate(axis)} for axis in coordinates]
        starts = {port.xy: port for port in sources if not self.on_line(port.xy)}
        goals = {(port.xy, NORMALS[port.side]): port for port in targets if not self.on_line(port.xy)}
        if not starts or not goals:
            return None

        @lru_cache(maxsize=None)
        def estimate(p):
            return min(abs(p[0] - q[0]) + abs(p[1] - q[1]) for q, _ in goals)

        queue, best, previous = [], {}, {}
        sequence = itertools.count()

        def enqueue(p, direction, cost, parent):
            state = p, direction
            if state not in best or cost < best[state]:
                best[state] = cost
                previous[state] = parent
                heapq.heappush(queue, (cost[0], cost[1] + estimate(p), next(sequence), state, cost))

        for p, port in starts.items():
            enqueue(p, None, (0, port.penalty), None)
        while queue:
            _, _, _, state, cost = heapq.heappop(queue)
            if cost != best[state]:
                continue
            if not consume():
                return None
            p, direction = state
            target = goals.get((p, (-direction[0], -direction[1]))) if direction else None
            if target:
                path = [p]
                while previous[state] is not None:
                    state = previous[state]
                    path.append(state[0])
                points = tuple(simplify(list(reversed(path))))
                source = starts[path[-1]]
                if self.score(points, source, target) is not None:
                    return points, source, target
            i, j = indices[0][p[0]], indices[1][p[1]]
            on_track_at_p = self.on_line(p)
            for dx, dy in NORMALS.values():
                step = dx, dy
                if direction is None and step != NORMALS[starts[p].side]:
                    continue
                if direction == (-dx, -dy) or on_track_at_p and direction != step:
                    continue
                if not (0 <= i + dx < len(coordinates[0]) and 0 <= j + dy < len(coordinates[1])):
                    continue
                q = coordinates[0][i + dx], coordinates[1][j + dy]
                if not self.clear(p, q):
                    continue
                # Charge the crossing on arrival, including arrivals at intermediate grid points.
                after = (q[0] + dx * (2 * EPS), q[1] + dy * (2 * EPS))
                cross = sum(proper_crossing(p, after, a, b) for _, a, b in self.tracks)
                turn = BEND_COST if direction is not None and direction != step else 0
                enqueue(q, step, (cost[0] + cross, cost[1] + math.dist(p, q) + turn), (p, direction))
        return None


def route_edge(edge, channels, sources, targets, variants, consume):
    candidates = []
    edges_tested = 0
    for source, target in itertools.product(sources, targets):
        if edge["from"] == edge["to"] and (source.xy == target.xy or source.side == target.side and
                                          math.dist(source.xy, target.xy) + EPS < PORT_SPACING):
            continue
        for points in channels.paths(source, target):
            edges_tested += 1
            if (score := channels.score(points, source, target)) is not None:
                candidates.append((score, points, source, target))
    candidates.sort(key=lambda item: item[0])

    if not candidates or candidates[0][0][0]:
        grid = channels.grid_route(sources, targets, consume)
        if grid:
            points, source, target = grid
            candidates.append((channels.score(points, source, target), points, source, target))
            candidates.sort(key=lambda item: item[0])
    if not candidates:
        raise GenerationError("ROUTE_UNAVAILABLE", "No legal orthogonal path with the available ports and obstacles",
                              f"edge {edge['id']}.route", edgeId=edge["id"], fromNode=edge["from"], toNode=edge["to"],
                              sourceSides=sorted({port.side for port in sources}),
                              targetSides=sorted({port.side for port in targets}))
    best = candidates[0]
    if variants:
        best = choose_label_support(edge["id"], channels, candidates, variants)
    _, points, source, target = best
    for end, port in (("from", source), ("to", target)):
        edge[end + "Port"] = port.side
        if abs(port.fraction - .5) > EPS:
            edge[end + "Fraction"] = port.fraction
    edge["routePoints"] = [list(p) for p in points]
    return edges_tested


def choose_label_support(edge_id, channels, candidates, variants):
    """Compare similar routes and give crowded labels distinct port choices."""
    best = candidates[0]
    crossings, cost = best[0]
    tested = defaultdict(int)
    best_support = (3, 0)
    for candidate in candidates:
        quality, points, source, target = candidate
        if quality[0] != crossings or quality[1] > cost + 2 * BEND_COST or sum(tested.values()) >= 24:
            break
        pair = (source, target)
        if tested[pair] == 2:
            continue
        tested[pair] += 1
        support = label_support(edge_id, points, variants, channels.page, channels.blockers, channels.tracks)
        if support < best_support:
            best, best_support = candidate, support
        if support == (0, 0):
            break
    return best


def route_auto(scene, blockers, measured, attempt, *, first_edges=()):
    nodes = {node["id"]: node for node in scene["nodes"]}
    edges = [dict(edge) for edge in scene["edges"]]
    work = dict(scene, edges=edges)
    fixed = [edge for edge in edges if edge.get("fixedRoute")]
    free = [edge for edge in edges if not edge.get("fixedRoute")]
    used = defaultdict(list)
    claimed = {(edge[end], edge[end + "Port"]) for edge in edges for end in ("from", "to")
               if edge.get(end + "Port") and nodes[edge[end]]["kind"] != "box"}
    tracks, vertices = [], []

    def reserve(edge):
        tracks.extend(route_tracks([edge]))
        vertices.extend(tuple(p) for p in edge["routePoints"])
        for end in ("from", "to"):
            port = connector_port(edge, end, nodes)
            used[edge[end], port.side].append(port.fraction)

    for edge in fixed:
        edge["routePoints"] = edge["fixedRoute"]
        reserve(edge)

    def priority(edge):
        first, second = nodes[edge["from"]], nodes[edge["to"]]
        a, b = center(first), center(second)
        direction = axial_direction(a, b)
        side = next((name for name in SIDES if NORMALS[name] == direction), None)
        straight = False
        if side:
            opposite = SIDES[(SIDES.index(side) + 2) % 4]
            source, target = make_port(first, side), make_port(second, opposite)
            if edge.get("fromPort", side) == side and edge.get("toPort", opposite) == opposite:
                channels = Channels(scene["page"], blockers, edge, tracks, vertices)
                straight = channels.score((source.xy, target.xy), source, target) is not None
        constrained = sum(end + "Port" in edge for end in ("from", "to"))
        distance = abs(a[0] - b[0]) + abs(a[1] - b[1])
        key = not straight, edge["id"] not in first_edges, -constrained
        if attempt == 2:
            return (*key, b, a)
        return (*key, distance if attempt == 0 else -distance, a, b)

    states = candidates = 0

    def consume():
        nonlocal states
        if states == STATE_LIMIT:
            return False
        states += 1
        return True

    for edge in sorted(free, key=priority):
        channels = Channels(work["page"], blockers, edge, tracks, vertices)
        sources = port_options(edge, "from", nodes, used, claimed)
        targets = port_options(edge, "to", nodes, used, claimed)
        try:
            candidates += route_edge(edge, channels, sources, targets, measured.get(edge["id"]), consume)
        except GenerationError as error:
            if states == STATE_LIMIT:
                error.diagnostic.update(code="ROUTE_SEARCH_LIMIT", searchStates=states,
                                        message="No legal route was found within the routing search budget")
            raise
        reserve(edge)
    crossings = [(first, second) for (first, a, b), (second, c, d) in itertools.combinations(tracks, 2)
                 if first != second and proper_crossing(a, b, c, d)]
    lengths = {edge["id"]: sum(math.dist(a, b) for a, b in zip(edge["routePoints"], edge["routePoints"][1:]))
               for edge in edges}
    detours = []
    for edge in edges:
        points = edge["routePoints"]
        direct = sum(abs(a - b) for a, b in zip(center(nodes[edge["from"]]), center(nodes[edge["to"]])))
        if not edge.get("fixedRoute") and len(points) > 3 and lengths[edge["id"]] > 1.5 * direct + .50:
            detours.append(edge["id"])
    work["_routingValidation"] = {
        "algorithm": "Joint port/channel candidates with bounded orthogonal grid search",
        "plannedEdges": len(edges), "overlaps": 0, "crossings": len(crossings), "crossingEdgePairs": crossings,
        "searchStates": states,
        "gridLimitReached": states == STATE_LIMIT,
        "totalBends": sum(bend_count(edge["routePoints"]) for edge in edges),
        "routeLengthIn": round(sum(lengths.values()), 8), "detourEdges": detours,
        "straightEdges": sum(len(edge["routePoints"]) == 2 for edge in edges),
        "routeCandidatesTested": candidates, "fixedRoutes": len(fixed),
    }
    return work
