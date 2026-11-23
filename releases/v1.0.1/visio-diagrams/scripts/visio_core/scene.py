"""Resolve routes and text frames, then inspect the scene used by both exporters."""
from itertools import combinations
import math

from . import local_layout, plan_routes
from .containers import hierarchy, obstacles, related
from .contracts import reject
from .edge_labels import measure_labels, place_labels
from .errors import GenerationError
from .geometry import EPS, NORMALS, crosses_box, in_page, intersects, overlaps_box_side
from .routing import route_auto
from .text_layout import fit_node_text, fit_text, source_text, text_frame


def route_legacy(work):
    if work.get("localLayout"):
        return local_layout.solve(work, position_labels=False), "legacy-local"
    if any(edge.get("routeGroup") for edge in work["edges"]):
        return plan_routes.plan(work), "legacy-groups"
    return work, "legacy-ports"


def check_router_capacity(work):
    nodes, edges = len(work["nodes"]), len(work["edges"])
    if nodes > 40 or edges > 80:
        reject("ROUTER_CAPACITY", "The bounded automatic router supports up to 40 nodes and 80 edges",
               "$.nodes" if nodes > 40 else "$.edges", nodeCount=nodes, edgeCount=edges,
               maxNodes=40, maxEdges=80)


def route_scene(work, font, *, compatible, measured=None):
    if compatible:
        work, name = route_legacy(work)
        nodes = {node["id"]: node for node in work["nodes"]}
        for edge in work["edges"]:
            for end, default in (("from", "bottom"), ("to", "top")):
                edge.setdefault(end + "Port", default)
            if not edge.get("routePoints"):
                edge["routePoints"] = [
                    plan_routes.point(nodes[edge[end]], edge[end + "Port"], edge.get(end + "Fraction", .5))
                    for end in ("from", "to")
                ]
                edge["_legacySegment"] = True
        prepare_text(work, font, compatible=True)
        work["_labelOwnership"] = place_labels(work, font, text_obstacles(work), measure_labels(work["edges"], font),
                                               compatible=True)
        return work, name

    prepare_text(work, font, compatible=False)
    blockers = text_obstacles(work)
    nodes = {node["id"]: node for node in work["nodes"]}
    fixed = []
    for edge in work["edges"]:
        if edge.get("fixedRoute"):
            resolved = dict(edge, routePoints=edge["fixedRoute"])
            route_checks(resolved, nodes, blockers, work["page"])
            fixed.append(resolved)
    check_track_contacts(fixed)
    if measured is None:
        measured = measure_labels(work["edges"], font)
    route_blockers = blockers + [dict(edge["label"], id=None) for edge in work["edges"] if edge.get("label")]
    best = best_quality = None
    failures = []
    successful_states = attempts_tried = 0
    for attempt in range(4):
        first_edges = ()
        if attempt == 3:
            if best is not None and best["_routingValidation"]["crossingEdgePairs"]:
                # Try reserving the later connector before the available paths are narrowed.
                first_edges = {second for _, second in best["_routingValidation"]["crossingEdgePairs"]}
            elif best is None:
                first_edges = {failure.diagnostic.get("details", {}).get("edgeId") for failure in failures}
                first_edges.discard(None)
            if not first_edges:
                break
        attempts_tried += 1
        try:
            candidate = route_auto(work, route_blockers, measured, attempt % 3, first_edges=first_edges)
            labels = place_labels(candidate, font, blockers, measured, compatible=False)
        except GenerationError as error:
            if error.diagnostic["code"] not in {"LABEL_NO_SPACE", "PORT_CAPACITY", "ROUTE_UNAVAILABLE",
                                                "ROUTE_SEARCH_LIMIT"}:
                raise
            failures.append(error)
            continue
        receipt = candidate["_routingValidation"]
        successful_states += receipt["searchStates"]
        quality = route_quality(candidate, measured, labels)
        if best is None or quality < best_quality:
            best, best_quality = candidate, quality
            best["_labelOwnership"] = labels
            best["_routingValidation"]["selectedAttempt"] = attempt + 1
        if quality[:4] == (0, 0, 0, 0):
            break
    if best is None:
        # A label diagnostic identifies an edge from a complete route set.
        error = next((error for error in failures if error.diagnostic["code"] == "LABEL_NO_SPACE"), failures[0])
        error.diagnostic["attemptFailures"] = [dict(failure.diagnostic) for failure in failures]
        raise error
    best["_routingValidation"].update(attemptsTried=attempts_tried, successfulSearchStates=successful_states,
                                      attemptFailures=[str(error) for error in failures],
                                      attemptDiagnostics=[error.diagnostic for error in failures])
    return best, "orthogonal-grid"


def label_excess(edge, measured):
    variants = measured.get(edge["id"])
    if not variants:
        return 0
    readable = 1 + min(item["text"].count("\n") for item in variants)
    return max(0, edge["text"].count("\n") - readable)


def route_quality(scene, measured, labels):
    receipt = scene["_routingValidation"]
    return (receipt["crossings"],
            sum(item["otherCloser"] for edge_id, item in labels.items() if edge_id in measured),
            sum(label_excess(edge, measured) for edge in scene["edges"]),
            len(receipt["detourEdges"]), receipt["totalBends"], receipt["routeLengthIn"])


def text_obstacles(scene):
    boxes = obstacles(scene)
    boxes.extend(dict(group["_textFrame"], id="group title " + group["id"])
                 for group in scene.get("groups", ()))
    if "_title" in scene["page"]:
        boxes.append(dict(scene["page"]["_title"], id="diagram_title"))
    return boxes


def prepare_text(scene, font, *, compatible):
    page = scene["page"]
    scene["fontFamily"] = font.family
    scene["fontAliases"] = font.aliases
    if page.get("title"):
        title = {"text": page["title"], "fontSize": page.get("titleFontSize", 16), "bold": True}
        frame = {"x": .3, "y": .1, "w": page["width"] - .6, "h": page.get("_titleHeight", .45)}
        fit_text(font, title, frame, "$.page.title")
        page["title"] = title["text"]
        if "sourceText" in title:
            page["sourceTitle"] = title["sourceText"]
        page["_title"] = {**frame, **title}
    for node in scene["nodes"]:
        frame = text_frame(node, compat=compatible)
        fit_node_text(font, node, frame)
        if node.get("text"):
            node["_textFrame"] = frame

def scene_checks(scene, original, *, compatible):
    nodes, _, _, ancestors = hierarchy(scene)
    original_nodes = {node["id"]: node for node in original["nodes"]}
    original_edges = {edge["id"]: edge for edge in original["edges"]}
    if set(nodes) != set(original_nodes) or set(original_edges) != {e["id"] for e in scene["edges"]}:
        reject("SCENE_CONTENT_CHANGED", "The scene must preserve every node and edge", "$")
    page = scene["page"]
    authored_groups = original.get("layout", {}).get("groups", []) if not compatible else []
    groups = scene.get("groups", [])
    if authored_groups or groups:
        wanted = {g["id"]: g for g in authored_groups}
        if len(wanted) != len(groups) or set(wanted) != {g["id"] for g in groups}:
            reject("SCENE_CONTENT_CHANGED", "Visual group IDs changed", "$.layout.groups")
        for group in groups:
            path = f"group {group['id']}"
            authored = wanted[group["id"]]
            if (group["members"] != authored["members"] or group["title"] != authored["title"]
                    or group["fontSize"] != authored.get("fontSize", 11)
                    or group["text"].replace("\n", "") != source_text(authored["title"]).replace("\n", "")):
                reject("SCENE_CONTENT_CHANGED", "Visual group membership or title changed", path)
            if not in_page(group, page):
                reject("GROUP_OUTSIDE_PAGE", "Visual group exceeds the page", path)
            for member in [group["_textFrame"], *(nodes[key] for key in group["members"])]:
                for pos, span in (("x", "w"), ("y", "h")):
                    if (member[pos] < group[pos] - EPS
                            or member[pos] + member[span] > group[pos] + group[span] + EPS):
                        reject("GROUP_BOUNDS", "Title or member exceeds its visual group", path,
                               member=member.get("id", "title"))
    if (page.get("sourceTitle", page.get("title", "")) != source_text(original["page"].get("title", ""))
            or page.get("titleFontSize", 16) != original["page"].get("titleFontSize", 16)):
        reject("SCENE_CONTENT_CHANGED", "Page title content or font size changed", "$.page.title")
    for node in nodes.values():
        authored = original_nodes[node["id"]]
        check_content(authored, node, "node", 11, ("kind", "parent", "bold", "dashed", "master"))
        if authored.get("detail"):
            expected = source_text(authored["detail"])
            if (node.get("sourceDetail", node.get("detail")) != expected
                    or expected.replace("\n", "") != node["detail"].replace("\n", "")
                    or authored.get("detailFontSize", 10) != node.get("detailFontSize", 10)):
                reject("SCENE_CONTENT_CHANGED", "Node detail or its font size changed", f"node {node['id']}.detail")
        if authored.get("size") and (node["w"], node["h"]) != (authored["size"]["width"], authored["size"]["height"]):
            reject("SIZE_CONSTRAINT_CHANGED", "Fixed node size was changed", f"node {node['id']}.size")
        fixed = authored.get("position") or (authored if authored.get("fixed") else None)
        if fixed and (node["x"], node["y"]) != (fixed["x"], fixed["y"]):
            reject("POSITION_CONSTRAINT_CHANGED", "Fixed node position was changed", f"node {node['id']}.position")
        if not in_page(node, page):
            reject("NODE_OUTSIDE_PAGE", f"Node {node['id']!r} exceeds the page", f"node {node['id']}")
    for a, b in combinations(nodes.values(), 2):
        if not related(a, b, ancestors) and intersects(a, b):
            reject("NODE_OVERLAP", f"Unrelated nodes {a['id']!r} and {b['id']!r} overlap", f"node {a['id']}", other=b["id"])
    if page.get("_title"):
        for node in nodes.values():
            if intersects(page["_title"], node):
                reject("TITLE_COLLISION", f"Page title overlaps node {node['id']!r}", "$.page.title")
    blockers = text_obstacles(scene)
    for edge in scene["edges"]:
        authored = original_edges[edge["id"]]
        check_content(authored, edge, "edge", 10, ("from", "to", "dashed", "bold", "role"))
        if authored.get("label") and authored["label"] != edge.get("label"):
            reject("LABEL_CONSTRAINT_CHANGED", "Fixed label rectangle was changed", f"edge {edge['id']}.label")
        if not compatible and authored.get("route") and authored["route"] != edge["routePoints"]:
            reject("ROUTE_CONSTRAINT_CHANGED", "Fixed route vertices were changed", f"edge {edge['id']}.route")
        for end in ("from", "to"):
            port = end + "Port"
            if authored.get(port) and authored[port] != edge[port]:
                # Fold changes the approach of its one bridge; caller opted into that re-layout.
                if not (compatible and original.get("localLayout", {}).get("fold")):
                    reject("PORT_CONSTRAINT_CHANGED", "Authored port side changed", f"edge {edge['id']}.{port}")
        route_checks(edge, nodes, blockers, page, check_border=not compatible and not authored.get("route"))
    if not compatible:
        check_track_contacts(scene["edges"])
    return {"status": "PASS", "nodes": len(nodes), "edges": len(scene["edges"]),
            "labels": sum(bool(edge.get("text")) for edge in scene["edges"]),
            "routeVertices": sum(len(edge["routePoints"]) for edge in scene["edges"]),
            "labelOwnership": scene["_labelOwnership"],
            "textFit": "WINDOWS_FONT_METRICS", "scope": "Scene geometry and content"}


def check_track_contacts(edges):
    for a, b in combinations(edges, 2):
        for first, second in ((a, b), (b, a)):
            points = first["routePoints"]
            track = second["routePoints"]
            for point in points:
                if any(plan_routes.on_track(point, start, end) for start, end in zip(track, track[1:])):
                    reject("ROUTE_VERTEX_CONTACT", "A connector vertex touches another connector",
                           f"edge {first['id']}.route", other=second["id"], point=point)


def check_content(original, resolved, kind, font_size, fields):
    text = source_text(original.get("text", ""))
    wrapped = resolved.get("text", "")
    if (resolved.get("sourceText", wrapped) != text or text.replace("\n", "") != wrapped.replace("\n", "")
            or original.get("fontSize", font_size) != resolved.get("fontSize", font_size)
            or any(original.get(key) != resolved.get(key) for key in fields)):
        reject("SCENE_CONTENT_CHANGED", f"{kind.title()} content or a style constraint changed", f"{kind} {resolved['id']}")


def route_checks(edge, nodes, blockers, page, *, check_border=False):
    route = edge["routePoints"]
    name = f"edge {edge['id']}"
    pairs = list(zip(route, route[1:]))
    for end, first, second in (("from", 0, 1), ("to", -1, -2)):
        node, side = nodes[edge[end]], edge[end + "Port"]
        fraction = edge.get(end + "Fraction", .5)
        if node["kind"] in ("ellipse", "diamond", "terminator") and abs(.5 - fraction) > EPS:
            reject("INVALID_AXIAL_PORT", f"Shape {node['kind']!r} requires an axis endpoint", f"{name}.{end}Port")
        expect = plan_routes.point(node, side, fraction)
        if math.dist(route[first], expect) > EPS:
            reject("ROUTE_PORT_MISMATCH", "Route does not meet the selected connection point", f"{name}.{end}Port",
                   expected=expect, actual=route[first])
        normal = NORMALS[side]
        outward = sum((route[second][i] - route[first][i]) * normal[i] for i in range(2))
        if outward <= EPS and not edge.get("_legacySegment"):
            reject("ROUTE_PORT_DIRECTION", "Route must leave the selected port outward", f"{name}.{end}Port")
    for index, (a, b) in enumerate(pairs):
        if math.dist(a, b) <= EPS or any(not (-EPS <= p[0] <= page["width"] + EPS
                                               and -EPS <= p[1] <= page["height"] + EPS) for p in (a, b)):
            reject("INVALID_RESOLVED_ROUTE", "Route has a zero segment or lies outside the page", f"{name}.route[{index}]")
        if not edge.get("_legacySegment") and abs(a[0]-b[0]) > EPS and abs(a[1]-b[1]) > EPS:
            reject("ROUTE_NOT_ORTHOGONAL", "Resolved routes must be orthogonal", f"{name}.route[{index}]")
        for obstacle in blockers:
            if check_border and overlaps_box_side(a, b, obstacle):
                reject("ROUTE_BORDER_OVERLAP", "An automatic route runs along a shape boundary",
                       f"{name}.route[{index}]", obstacle=obstacle["id"])
            if edge.get("_legacySegment") and obstacle["id"] in (edge["from"], edge["to"]):
                continue
            if crosses_box(a, b, obstacle):
                reject("ROUTE_NODE_CROSSING", f"Route crosses {obstacle['id']!r}", f"{name}.route[{index}]",
                       obstacle=obstacle["id"])
