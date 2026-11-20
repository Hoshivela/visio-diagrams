"""Local placement against locked leaf rectangles in an intent candidate."""
from itertools import combinations, product
from math import inf

from .errors import GenerationError
from .geometry import EPS, intersects


def check_locked_overlap(anchors):
    for first, second in combinations(anchors, 2):
        if intersects(first, second):
            locks = [dict(node=n["id"], position=dict(x=n["x"], y=n["y"]),
                          size=dict(width=n["w"], height=n["h"])) for n in (first, second)]
            raise GenerationError("INTENT_OVERLAP", "Rectangles overlap at their locked positions",
                                  "object " + first["id"], other=second["id"], locks=locks)


def fit_among_anchors(leaves, anchors, page, title_floor, pairs, parallel, horizontal, along, across):
    """Place unlocked leaves in available space, preserving settled rank constraints.

    Contact with an obstacle face or a page edge bounds each free interval.
    Try the rank's original point first, then the nearest of those boundaries
    with normal spacing; permit touching when space is tight. Unspecified page
    dimensions remain free to grow in the caller.
    """
    settled = {n["id"]: n for n in anchors}
    u, du, v, dv = ("x", "w", "y", "h") if horizontal else ("y", "h", "x", "w")
    peers = [(a, b) for row in parallel for a, b in combinations(row, 2)]
    movable = sorted((n for n in leaves.values() if n["id"] not in settled),
                     key=lambda n: (n[u], n[v], n["id"]))
    for n in movable:
        lower = {"x": 0., "y": title_floor}
        upper = {"x": page.get("width", inf) - n["w"], "y": page.get("height", inf) - n["h"]}
        key = n["id"]
        for a, b in pairs:
            if a == key and b in settled:
                upper[u] = min(upper[u], settled[b][u] - n[du])
            if b == key and a in settled:
                lower[u] = max(lower[u], settled[a][u] + settled[a][du])
        for a, b in peers:
            if a == key and b in settled:
                other = settled[b]
                upper[v] = min(upper[v], other[v] - n[dv])
            elif b == key and a in settled:
                other = settled[a]
                lower[v] = max(lower[v], other[v] + other[dv])
            else:
                continue
            aligned = other[u] + (other[du] - n[du]) / 2
            lower[u], upper[u] = max(lower[u], aligned), min(upper[u], aligned)
        if all(lower[k] <= upper[k] + EPS for k in lower):
            def clamp(axis, value):
                return max(lower[axis], min(upper[axis], value))

            origins = {k: clamp(k, n[k]) for k in lower}
            if not any(intersects(dict(n, **origins), box) for box in settled.values()):
                n.update(origins)
            else:
                for gap_u, gap_v in ((along, across), (0., 0.)):
                    coords = {}
                    for axis, size, gap in ((u, du, gap_u), (v, dv, gap_v)):
                        positions = {origins[axis], lower[axis]}
                        if upper[axis] != inf:
                            positions.add(upper[axis])
                        for box in settled.values():
                            positions.add(clamp(axis, box[axis] - gap - n[size]))
                            positions.add(clamp(axis, box[axis] + box[size] + gap))
                        coords[axis] = positions
                    options = ((x, y) for x, y in product(coords["x"], coords["y"])
                               if not any(intersects(dict(n, x=x, y=y), box) for box in settled.values()))
                    best = min(options, default=None,
                               key=lambda p: ((p[0]-n["x"])**2 + (p[1]-n["y"])**2, p))
                    if best is not None:
                        n.update(x=best[0], y=best[1])
                        break
        # If no local slot works, leave this candidate to the ordinary geometry
        # and rank checks, which report the exact failed constraint.
        settled[key] = n
