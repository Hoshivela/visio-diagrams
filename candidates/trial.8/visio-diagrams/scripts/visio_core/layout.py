"""Select a placement strategy from declared shape and constraint support."""
from collections import Counter, defaultdict
from copy import deepcopy

from .containers import prepare
from .errors import GenerationError
from .routing import PORT_SPACING
from .text_layout import TEXT_MARGIN, shape_text_scale, text_variants, wrap_text


def select_strategy(spec, layout):
    mode, direction, requested = (layout[key] for key in ("mode", "direction", "strategy"))
    nodes = spec["nodes"]
    unsupported = {}
    if mode != "auto":
        unsupported["g4-grid"] = ["requires auto layout"]
        unsupported["layered"] = ["requires auto layout"]
    else:
        unsupported["positioned"] = ["requires fixed leaf positions"]
        if any(node["kind"] == "container" for node in nodes):
            unsupported["g4-grid"] = ["requires flat rectangular nodes"]
            unsupported["layered"] = ["requires flat nodes, use positioned mode for explicit containers"]
        elif any(node["kind"] != "box" for node in nodes):
            unsupported["g4-grid"] = ["requires rectangular nodes"]
        if direction != "right" and mode == "auto":
            unsupported.setdefault("g4-grid", []).append("requires direction=right")
        if any("size" in node for node in nodes):
            unsupported.setdefault("g4-grid", []).append("grid requires sizes to be derived")
    if requested != "auto":
        if requested in unsupported:
            raise GenerationError("STRATEGY_UNSUPPORTED", f"{requested}: {'; '.join(unsupported[requested])}",
                                  "$.layout.strategy", rejected=unsupported)
        return requested, unsupported
    for name in ("g4-grid", "layered", "positioned"):
        if name not in unsupported:
            return name, unsupported
    raise GenerationError("NO_COMPATIBLE_STRATEGY", "No automatic layout supports the requested shape and constraints",
                          "$.layout", rejected=unsupported)


def layers(nodes, edges):
    """Longest-predecessor rank from G4 layout-standardization/1, with exact hints."""
    previous = {node["id"]: set() for node in nodes}
    for edge in edges:
        previous[edge["to"]].add(edge["from"])
    remaining = list(previous)
    ranks = {}
    hints = {node["id"]: node["layer"] for node in nodes if "layer" in node}
    while remaining:
        ready = [node for node in remaining if previous[node] <= ranks.keys()]
        if not ready:
            raise GenerationError("LAYOUT_CYCLE", "Automatic layers require an acyclic graph. Use positioned mode for cycles.",
                                  "$.edges", nodes=remaining)
        for node in ready:
            minimum = max((ranks[parent] + 1 for parent in previous[node]), default=0)
            ranks[node] = hints.get(node, minimum)
            if ranks[node] < minimum:
                raise GenerationError("LAYER_CONFLICT", f"Node {node!r} must be at layer {minimum} or later",
                                      "$.nodes", nodeId=node, requested=hints[node], minimum=minimum)
        remaining = [node for node in remaining if node not in ranks]
    by_layer = defaultdict(list)
    for node in nodes:
        by_layer[ranks[node["id"]]].append(node)
    return by_layer, ranks


# Physical minima leave room for the shape outline and usable connection points.
MIN_NODE = {"box": (.94, .46), "diamond": (1.5, .80), "ellipse": (1.12, .52),
            "terminator": (1.18, .48), "text": (.40, .28)}
NODE_PAD_X = .26
NODE_PAD_Y = .16


def port_loads(edges):
    loads = defaultdict(Counter)
    for edge in edges:
        for end in ("from", "to"):
            loads[edge[end]][edge.get(end + "Port")] += 1
    return loads


def port_minimum(kind, ports):
    min_w, min_h = MIN_NODE[kind]
    if kind == "box":
        free = (ports[None] + 3) // 4
        min_w = max(min_w, (max(ports["top"], ports["bottom"]) + free + 1) * PORT_SPACING)
        min_h = max(min_h, (max(ports["left"], ports["right"]) + free + 1) * PORT_SPACING)
    return min_w, min_h


def size_node(node, font, ports, *, width_limit=None):
    size = node.pop("size", None)
    if size:
        node.update(w=size["width"], h=size["height"])
        return
    kind = node["kind"]
    min_w, min_h = port_minimum(kind, ports)
    text = node.get("text", "")
    if not text:
        node.update(w=min_w, h=min_h)
        return

    points, bold = node.get("fontSize", 11), node.get("bold", False)
    scale_w, scale_h = shape_text_scale(kind)
    # Compare the footprints of several readable line lengths. Width limits
    # grow with the font so large text gets comparable room per character.
    path = f"node {node['id']}.text"
    caps = [max(cap, em * points / 72) for cap, em in ((1.65, 10), (2.25, 14), (2.80, 17), (3.35, 20))]
    if width_limit is not None:
        caps = [min(cap, width_limit * scale_w - NODE_PAD_X) for cap in caps]
    variants = text_variants(font, text, points, bold, caps, path)
    options = []
    for item in variants:
        w = max(min_w, (item["width"] + NODE_PAD_X) / scale_w)
        h = max(min_h, (item["height"] + NODE_PAD_Y) / scale_h)
        lines = item["text"].count("\n")
        options.append((w + 3 * h + .32 * lines, w, h))
    _, w, h = min(options)
    node.update(w=w, h=h)


def size_positioned_node(node, font):
    # Keep the dimensions established for anchored or explicitly routed inputs.
    size = node.pop("size", None)
    if size:
        node.update(w=size["width"], h=size["height"])
        return
    kind, text = node["kind"], node.get("text", "")
    scale_w, scale_h = shape_text_scale(kind)
    width, height = (2.15, .70) if kind != "diamond" else (2.25, 1.12)
    if text:
        points, bold = node.get("fontSize", 11), node.get("bold", False)
        widest = max((font.measure(c, points, bold)[0] for c in set(text) if c not in "\r\n\t"), default=0)
        width = max(width, (widest + 2 * TEXT_MARGIN + .01) / scale_w)
        measured = wrap_text(font, text, points, bold, width * scale_w, f"node {node['id']}.text")
        height = max(height, (measured["height"] + .025) / scale_h)
    node.update(w=width, h=height)


def g4_grid(columns, left, top, gaps):
    """Align lane centers while keeping each node's own visible rectangle."""
    lane_heights = [0.0] * max(map(len, columns.values()))
    for column in columns.values():
        for index, node in enumerate(column):
            lane_heights[index] = max(lane_heights[index], node["h"])
    row_centers = []
    for height in lane_heights:
        row_centers.append(top + height / 2)
        top += height + .50

    depths = {}
    for rank in range(max(columns) + 1):
        column = columns.get(rank, ())
        depth = max((node["w"] for node in column), default=0)
        depths[rank] = depth
        for y, node in zip(row_centers, column):
            node.update(x=left + (depth - node["w"]) / 2, y=y - node["h"] / 2)
        left += depth + gaps[rank]
    return {"columnWidthsIn": depths, "laneHeightsIn": lane_heights,
            "algorithm": "Rank widths and aligned lanes with individual node sizes"}


def layered(columns, left, top, direction, gaps, cross_gaps):
    horizontal = direction == "right"
    main, cross = ("w", "h") if horizontal else ("h", "w")
    x_main, x_cross = ("x", "y") if horizontal else ("y", "x")
    main_origin, cross_origin = (left, top) if horizontal else (top, left)
    widths = {rank: sum(node[cross] for node in column) + cross_gaps[rank] * (len(column) - 1)
              for rank, column in columns.items()}
    extent = max(widths.values())
    for rank in range(max(columns) + 1):
        column = columns.get(rank, ())
        depth = max((node[main] for node in column), default=0)
        cross_pos = cross_origin + (extent - widths.get(rank, 0)) / 2
        for node in column:
            node[x_main] = main_origin + (depth - node[main]) / 2
            node[x_cross] = cross_pos
            cross_pos += node[cross] + cross_gaps[rank]
        main_origin += depth + gaps[rank]


def rank_gaps(edges, columns, ranks, measured, direction, strategy, additions):
    # Keep room for ordinary labels on aligned horizontal runs. Long or
    # off-axis labels can also use the space beside their run or a local retry.
    gaps = defaultdict(lambda: 1.0 if direction == "right" else .90)
    if direction == "right":
        lanes = {node["id"]: (index if strategy == "g4-grid" or len(column) == 1 else None)
                 for column in columns.values() for index, node in enumerate(column)}
        for edge in edges:
            head, tail = edge["from"], edge["to"]
            rank = ranks[head]
            if ranks[tail] != rank + 1 or lanes[head] is None or lanes[head] != lanes[tail]:
                continue
            variants = measured.get(edge["id"], ())
            preferred = next((item for item in variants if item["width"] <= 1.55), None)
            if preferred:
                gaps[rank] = max(gaps[rank], preferred["width"] + .16)
    return {rank: gaps[rank] + additions.get(rank, 0) for rank in range(max(columns) + 1)}


def place(spec, layout, strategy, font, measured, extra_gaps, extra_cross, reversed_layer=None):
    scene = deepcopy(spec)
    page = scene["page"]
    fixed_width, fixed_height = page.get("width"), page.get("height")
    title = page.get("title", "")
    title_height = .45
    if title:
        width = fixed_width or 5.6
        title_layout = wrap_text(font, title, page.get("titleFontSize", 16), True, width - .6, "$.page.title")
        title_height = max(title_height, title_layout["height"])
    ports = port_loads(scene["edges"])
    for node in scene["nodes"]:
        if node["kind"] == "container":
            continue
        if strategy == "positioned":
            size_positioned_node(node, font)
            node.update(node.pop("position"), fixed=True)
        else:
            size_node(node, font, ports[node["id"]])
    placement = {}
    if strategy != "positioned":
        columns, ranks = layers(scene["nodes"], scene["edges"])
        if reversed_layer is not None:
            columns[reversed_layer].reverse()
        left = .65
        top = left + (title_height if title else 0)
        gaps = rank_gaps(scene["edges"], columns, ranks, measured, layout["direction"], strategy, extra_gaps)
        if strategy == "g4-grid":
            placement = g4_grid(columns, left, top, gaps)
        else:
            cross_gaps = defaultdict(lambda: .90, {rank: .90 + delta for rank, delta in extra_cross.items()})
            layered(columns, left, top, layout["direction"], gaps, cross_gaps)
        placement.update(layers=ranks, gapAfterLayerIn=gaps, extraLayerGapIn=extra_gaps.copy(),
                         extraSiblingGapIn=extra_cross.copy())
        if reversed_layer is not None:
            placement["reversedLayer"] = reversed_layer
    for node in scene["nodes"]:
        node.pop("layer", None)
    scene = prepare(scene, font=font)
    # Account for authored constraints when deriving a page for positioned drawings.
    required_w = max(node["x"] + node["w"] for node in scene["nodes"])
    required_h = max(node["y"] + node["h"] for node in scene["nodes"])
    for edge in scene["edges"]:
        if edge.get("route"):
            edge["fixedRoute"] = edge.pop("route")
            for x, y in edge["fixedRoute"]:
                required_w, required_h = max(required_w, x), max(required_h, y)
        if edge.get("label"):
            label = edge["label"]
            required_w, required_h = max(required_w, label["x"] + label["w"]), max(required_h, label["y"] + label["h"])
    page = scene["page"]
    page["width"] = fixed_width or max(5.6 if title else 3.5, required_w + .65)
    page["height"] = fixed_height or max(2.2, required_h + .65)
    if required_w > page["width"] + 1e-8 or required_h > page["height"] + 1e-8:
        raise GenerationError("PAGE_CAPACITY", "Nodes or fixed constraints exceed the fixed page",
                              "$.page", requiredWidth=required_w, requiredHeight=required_h,
                              width=page["width"], height=page["height"])
    if title:
        page["_titleHeight"] = title_height
    return scene, placement
