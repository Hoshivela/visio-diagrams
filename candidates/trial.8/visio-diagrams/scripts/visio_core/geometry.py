"""Geometry in page inches, with a top-left origin."""
import math

EPS = 1e-7
NORMALS = {"right": (1, 0), "bottom": (0, 1), "left": (-1, 0), "top": (0, -1)}


def in_page(box, page):
    return (box["x"] >= -EPS and box["y"] >= -EPS and box["x"] + box["w"] <= page["width"] + EPS
            and box["y"] + box["h"] <= page["height"] + EPS)


def intersects(a, b):
    return (min(a["x"] + a["w"], b["x"] + b["w"]) > max(a["x"], b["x"]) + EPS
            and min(a["y"] + a["h"], b["y"] + b["h"]) > max(a["y"], b["y"]) + EPS)


def bounds(box, padding=0):
    return (box["x"] - padding, box["y"] - padding,
            box["x"] + box["w"] + padding, box["y"] + box["h"] + padding)


def overlaps_box_side(a, b, box):
    """Detect positive-length contact with a box border, excluding a single port."""
    left, top, right, bottom = bounds(box)
    if abs(a[0] - b[0]) <= EPS and min(abs(a[0] - left), abs(a[0] - right)) <= EPS:
        return min(max(a[1], b[1]), bottom) - max(min(a[1], b[1]), top) > EPS
    if abs(a[1] - b[1]) <= EPS and min(abs(a[1] - top), abs(a[1] - bottom)) <= EPS:
        return min(max(a[0], b[0]), right) - max(min(a[0], b[0]), left) > EPS
    return False


def crosses_box(a, b, box):
    """Segment clipping against the open rectangle, including legacy diagonal segments."""
    left, top, right, bottom = bounds(box)
    if a[0] == b[0]:
        return (left + EPS < a[0] < right - EPS
                and min(max(a[1], b[1]), bottom) > max(min(a[1], b[1]), top) + EPS)
    if a[1] == b[1]:
        return (top + EPS < a[1] < bottom - EPS
                and min(max(a[0], b[0]), right) > max(min(a[0], b[0]), left) + EPS)
    begin, end = 0.0, 1.0
    for start, finish, low, high in zip(a, b, (left, top), (right, bottom)):
        delta = finish - start
        low, high = low + EPS, high - EPS
        if low >= high:
            return False
        if abs(delta) <= EPS:
            if not low < start < high:
                return False
            continue
        t0, t1 = sorted(((low - start) / delta, (high - start) / delta))
        begin, end = max(begin, t0), min(end, t1)
        if end <= begin:
            return False
    return True


def box_distance(box, a, b):
    """Shortest distance from a label rectangle to a connector segment."""
    left, top, right, bottom = bounds(box)
    if a[0] == b[0] or a[1] == b[1]:
        return math.hypot(max(left - max(a[0], b[0]), min(a[0], b[0]) - right, 0),
                          max(top - max(a[1], b[1]), min(a[1], b[1]) - bottom, 0))
    if crosses_box(a, b, box):
        return 0.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    distances = [math.hypot(max(left - x, x - right, 0), max(top - y, y - bottom, 0)) for x, y in (a, b)]
    for x, y in ((left, top), (left, bottom), (right, top), (right, bottom)):
        fraction = min(1, max(0, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)))
        distances.append(math.hypot(x - a[0] - fraction * dx, y - a[1] - fraction * dy))
    return min(distances)


def proper_crossing(a, b, c, d):
    """True for an orthogonal intersection interior to both segments."""
    if a[0] == b[0]:
        a, b, c, d = c, d, a, b
    return (a[1] == b[1] and c[0] == d[0]
            and min(a[0], b[0]) + EPS < c[0] < max(a[0], b[0]) - EPS
            and min(c[1], d[1]) + EPS < a[1] < max(c[1], d[1]) - EPS)


def bend_count(points):
    axes = [a[0] == b[0] for a, b in zip(points, points[1:])]
    return sum(first != second for first, second in zip(axes, axes[1:]))
