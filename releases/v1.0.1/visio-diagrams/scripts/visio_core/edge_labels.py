"""Fit relation labels beside their own connector using measured text frames."""
from dataclasses import dataclass
import math

from .errors import GenerationError
from .geometry import EPS, bounds, box_distance, crosses_box, in_page, intersects
from .text_layout import fit_text, source_text, text_variants


@dataclass
class Placement:
    box: dict
    text: dict
    score: float
    segment: int


def measure_labels(edges, font):
    return {edge["id"]: measure_label(edge, font) for edge in edges if edge.get("text") and not edge.get("label")}


def measure_label(edge, font):
    points = edge.get("fontSize", 10)
    caps = (3.55, 3.15, 2.65, 2.20, 1.80, 1.55, 1.25, 1.05, .85, .72, .55, .45)
    return text_variants(font, edge["text"], points, edge.get("bold", False), caps, f"edge {edge['id']}.text")


def route_tracks(edges):
    return [(edge["id"], a, b) for edge in edges for a, b in zip(edge["routePoints"], edge["routePoints"][1:])]


def label_fits(box, page, blockers, tracks):
    return (in_page(box, page) and not any(intersects(box, other) for other in blockers)
            and not any(crosses_box(a, b, box) for _, a, b in tracks))


def frames(points, variants, blockers, *, near_only):
    """Sample both sides of every segment, keeping the projected anchor on it."""
    pairs = list(enumerate(zip(points, points[1:])))
    pairs.sort(key=lambda pair: -math.dist(*pair[1]))
    for index, (a, b) in pairs:
        axis = 0 if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 1
        start, end = sorted((a[axis], b[axis]))
        length = end - start
        if length <= EPS:
            continue
        for measured in variants:
            w, h = measured["width"] + .02, measured["height"] + .02
            width, depth = (w, h) if axis == 0 else (h, w)
            gap = max(.04, min(.12, .32 * measured["lineHeight"]))
            for distance in (gap, .16) if near_only else (gap, .16, .30, .46):
                for side in (-1, 1):
                    anchors = [start + length * fraction for fraction in (.5, .35, .65, .15, .85)]
                    if not near_only and a[1 - axis] == b[1 - axis]:
                        # Try the space immediately before and after obstacles along long runs.
                        mid = a[1 - axis] + side * (distance + depth / 2)
                        for box in blockers:
                            extents = bounds(box)
                            along_min, along_max = extents[axis::2]
                            across_min, across_max = extents[1 - axis::2]
                            if across_min < mid + depth / 2 and across_max > mid - depth / 2:
                                anchors.extend((along_min - width / 2 - .02, along_max + width / 2 + .02))
                    for anchor in anchors:
                        if not start + EPS < anchor < end - EPS:
                            continue
                        cross = a[1 - axis] + (b[1 - axis] - a[1 - axis]) * (anchor - a[axis]) / (b[axis] - a[axis])
                        cross += side * (distance + depth / 2)
                        cx, cy = (anchor, cross) if axis == 0 else (cross, anchor)
                        box = {"x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h}
                        projection = min(end, anchor + width / 2) - max(start, anchor - width / 2)
                        yield index, box, measured, projection / width, abs((anchor - start) / length - .5)


def label_support(edge_id, points, variants, page, blockers, tracks):
    """Estimate a route's label space before all connectors are placed."""
    own = [(edge_id, a, b) for a, b in zip(points, points[1:])]
    lines = own + tracks
    support = (2, 0)
    readable = 1 + min(item["text"].count("\n") for item in variants)
    for index, box, measured, coverage, _ in frames(points, variants, blockers, near_only=True):
        if not label_fits(box, page, blockers, lines):
            continue
        gap = box_distance(box, *own[index][1:])
        neighbors = min((box_distance(box, a, b) for _, a, b in tracks), default=math.inf)
        clean = neighbors >= gap + .35 * measured["lineHeight"] and coverage >= .65
        extra = max(0, measured["text"].count("\n") - readable)
        support = min(support, (int(not clean), extra))
        if support == (0, 0):
            break
    return support


def placements(edge, variants, page, blockers, tracks):
    points = edge["routePoints"]
    own = list(zip(points, points[1:]))
    lengths = [math.dist(a, b) for a, b in own]
    longest = max(lengths)
    others = [(a, b) for edge_id, a, b in tracks if edge_id != edge["id"]]
    original_lines = source_text(edge["text"]).count("\n")
    seen = set()
    candidates = []
    for index, box, measured, coverage, eccentricity in frames(points, variants, blockers, near_only=False):
        key = (index, *(round(value, 8) for value in box.values()), measured["text"])
        if key in seen or not label_fits(box, page, blockers, tracks):
            continue
        seen.add(key)
        gap = box_distance(box, *own[index])
        nearest = min((box_distance(box, a, b) for a, b in others), default=math.inf)
        separation = .55 * measured["lineHeight"]
        interference = max(0, gap + separation - nearest)
        added_lines = measured["text"].count("\n") - original_lines
        score = (gap + 2 * interference + 2 * (nearest < gap - EPS) + .18 * (1 - coverage) + .24 * added_lines
                 + .13 * eccentricity + .18 * (1 - lengths[index] / longest))
        candidates.append(Placement(box, measured, score, index))
    return sorted(candidates, key=lambda choice: round(choice.score, 8))


def place_labels(scene, font, blockers, measured, *, compatible):
    """Reserve fixed frames, then assign the best compatible set within a search budget."""
    page = scene["page"]
    labelled = [edge for edge in scene["edges"] if edge.get("text")]
    blockers = list(blockers)
    # A visual group's outline is permeable to connectors. Keep the label's
    # text frame clear of its ink on both sides (about two points per side).
    for group in scene.get("groups", ()):
        x, y, w, h = (group[k] for k in ("x", "y", "w", "h"))
        clearance = .03
        for border in (y, y + h):
            blockers.append(dict(x=x-clearance, y=border-clearance, w=w+2*clearance, h=2*clearance))
        for border in (x, x + w):
            blockers.append(dict(x=border-clearance, y=y-clearance, w=2*clearance, h=h+2*clearance))
    tracks = route_tracks(scene["edges"])
    choices = {}
    for edge in labelled:
        if not edge.get("label"):
            continue
        frame = edge["label"]
        path = f"edge {edge['id']}.text"
        fit_text(font, edge, frame, path, base_size=10)
        if not label_fits(frame, page, blockers, tracks):
            raise GenerationError("LABEL_COLLISION", "Fixed label frame exceeds the page or touches diagram content",
                                  path, rectangle=frame)
        blockers.append(frame)
    for edge in labelled:
        if edge.get("label"):
            continue
        edge_id = edge["id"]
        options = placements(edge, measured[edge_id], page, blockers, tracks)
        if not options:
            label_failure(edge, "No clear label frame near the route")
        choices[edge_id] = options

    # Prefer clear ownership before spending a flexible label's free space.
    order = sorted(choices, key=lambda edge_id: (sum(option.score <= choices[edge_id][0].score + .20
                                                      for option in choices[edge_id]), -choices[edge_id][0].score))
    assigned = {}
    pending = [iter(choices[edge_id]) for edge_id in order]
    index = attempts = 0
    limit = max(16000, len(order) * 30)
    while 0 <= index < len(order) and attempts < limit:
        edge_id = order[index]
        option = next(pending[index], None)
        if option is None:
            pending[index] = iter(choices[edge_id])
            index -= 1
            if index >= 0:
                del assigned[order[index]]
            continue
        attempts += 1
        if any(intersects(option.box, taken.box) for taken in assigned.values()):
            continue
        assigned[edge_id] = option
        index += 1
    if index != len(order):
        crowded = next(edge for edge in labelled if edge["id"] == order[0])
        label_failure(crowded, "The movable label frames compete for space",
                      competingEdges=order, searchLimitReached=attempts == limit)
    for edge in labelled:
        if (option := assigned.get(edge["id"])) is not None:
            original = source_text(edge["text"])
            if option.text["text"] != original:
                edge["sourceText"] = original
            edge["text"] = option.text["text"]
            edge["_lineHeight"] = option.text["lineHeight"]
            frame_key = "_inlineLabel" if compatible and not scene.get("localLayout") else "label"
            edge[frame_key] = option.box
    return {edge["id"]: ownership(edge, tracks, assigned.get(edge["id"])) for edge in labelled}


def label_failure(edge, message, **details):
    points = edge.get("fontSize", 10)
    raise GenerationError("LABEL_NO_SPACE", f"{message} at {points} pt. Allow more page or route space.",
                          f"edge {edge['id']}.text", edgeId=edge["id"], fontSize=points, **details)


def ownership(edge, tracks, option):
    box = edge.get("label") or edge["_inlineLabel"]
    own, others = math.inf, (math.inf, None)
    for edge_id, a, b in tracks:
        distance = box_distance(box, a, b)
        if edge_id == edge["id"]:
            own = min(own, distance)
        elif distance < others[0]:
            others = distance, edge_id
    return {"ownDistanceMm": round(own * 25.4, 3),
            "nearestOtherEdge": others[1], "nearestOtherDistanceMm": round(others[0] * 25.4, 3) if others[1] else None,
            "otherCloser": others[0] < own - EPS, "supportSegment": option.segment if option else None}
