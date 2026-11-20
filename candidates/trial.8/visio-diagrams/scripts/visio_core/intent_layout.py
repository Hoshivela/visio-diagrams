"""Bounded, text-aware block placement for explicitly classified relationships."""
from collections import defaultdict
from copy import deepcopy
from itertools import chain, combinations
from statistics import median
from time import perf_counter

from .containers import prepare
from .delivery_layout import (SELECTION_CRITERIA, check_workflow_alignment, diagnose, page_metrics,
                              selection_metrics, text_runs)
from .edge_labels import measure_labels
from .errors import GenerationError
from .geometry import in_page, intersects
from .intent_anchors import check_locked_overlap, fit_among_anchors
from .layout import port_loads, port_minimum, size_node
from .scene import route_quality, route_scene, scene_checks
from .text_layout import node_text_metrics, wrap_text
from .workflow_alignment import apply_workflow_tracks, workflow_rules


def fail(code, message, path, **details):
    raise GenerationError(code, message, path, **details)


def ranked(items, pairs, parallel, align_branches=False):
    """Collapse ordered peers before assigning longest-predecessor ranks."""
    representative = {item: item for item in items}
    for peers in parallel:
        for item in peers:
            representative[item] = peers[0]
    parents = {key: set() for key in representative.values()}
    for first, second in pairs:
        a, b = representative[first], representative[second]
        if a == b:
            fail("INTENT_RANK_CONFLICT", "Precedence contradicts parallel alignment", "$.layout",
                 first=first, second=second)
        parents[b].add(a)
    ranks = {}
    while len(ranks) < len(parents):
        ready = [k for k in parents if k not in ranks and parents[k] <= ranks.keys()]
        if not ready:
            fail("INTENT_RANK_CYCLE", "Main relationships and local order contain a cycle", "$.layout",
                 items=[k for k in parents if k not in ranks])
        for key in ready:
            ranks[key] = max((ranks[p] for p in parents[key]), default=-1) + 1
    lanes = defaultdict(list)
    for key in dict.fromkeys(representative.values()):
        lanes[ranks[key]].append(key)
    peer_lists = {p[0]: p for p in parallel}
    lateral = {}
    ordered = []
    for rank in sorted(lanes):
        lane = lanes[rank]
        if align_branches and rank:
            # A whole declared parallel list moves together. Relative order
            # within it, and all along-axis ranks, remain authoritative.
            lane = sorted(lane, key=lambda key: median(lateral[p] for p in parents[key]))
        row = []
        count = sum(len(peer_lists.get(k, [k])) for k in lane)
        for key in lane:
            peers = peer_lists.get(key, [key])
            lateral[key] = (len(row) + len(peers) / 2) / count
            row.extend(peers)
        ordered.append(row)
    return ordered


def arrange(items, lanes, horizontal, gap, cross_gap, slots=None):
    """Place measured rectangles into aligned ranks; return their occupied bounds."""
    main, cross, u, v = ("w", "h", "x", "y") if horizontal else ("h", "w", "y", "x")
    slots = slots or {}
    breadths = [[slots.get(k, items[k][cross]) for k in lane] for lane in lanes]
    spans = [sum(row) + cross_gap * (len(row) - 1) for row in breadths]
    pivots = []
    for lane, row, span in zip(lanes, breadths, spans):
        middle = len(row) // 2
        if lane[0] in slots and len(row) % 2:
            # Place the actual middle track on the spine, even if a locked
            # outer shape makes the two halves use unequal amounts of paper.
            pivots.append(sum(row[:middle]) + cross_gap * middle + row[middle] / 2)
        else:
            pivots.append(span / 2)
    axis = max(pivots)
    extent = axis + max(span - pivot for span, pivot in zip(spans, pivots))
    offset = 0
    for lane, pivot in zip(lanes, pivots):
        thickness = max(items[k][main] for k in lane)
        lateral = axis - pivot
        for k in lane:
            item = items[k]
            item[u] = offset + (thickness - item[main]) / 2
            slot = slots.get(k, item[cross])
            item[v] = lateral + (slot - item[cross]) / 2
            lateral += slot + cross_gap
        offset += thickness + gap
    return (offset - gap, extent) if horizontal else (extent, offset - gap)


def leaf_sizes(nodes, edges, font, width_cap, use_draft, fit_titles=False):
    loads = port_loads(edges)
    for n in nodes.values():
        size_node(n, font, loads[n["id"]], width_limit=width_cap if fit_titles else None)
        if n.get("detail") and "size" not in n.get("_input", {}):
            points = n.get("fontSize", 11)
            title = max(font.measure(s, points, n.get("bold", False))[0] for s in n["text"].split("\n"))
            body = max(font.measure(s, n.get("detailFontSize", 10), False)[0] for s in n["detail"].split("\n"))
            # Use a content-derived width for short cards; reserve enough width
            # for the longer text, then calculate height from its actual wrap.
            min_w, min_h = port_minimum(n["kind"], loads[n["id"]])
            n["w"] = max(min_w, min(width_cap, max(title, body) + .23))
            metrics = node_text_metrics(font, n, n["w"])
            n["h"] = max(min_h, metrics["height"] + .08)
        draft = n.get("draft", {}).get("size")
        if use_draft and draft and "size" not in n["_input"]:
            n.update(w=draft["width"], h=draft["height"])


def bounds(members):
    left = min(m["x"] for m in members)
    top = min(m["y"] for m in members)
    return left, top, max(m["x"] + m["w"] for m in members), max(m["y"] + m["h"] for m in members)


def frame_group(group, nodes, font):
    left, top, right, bottom = bounds([nodes[k] for k in group["members"]])
    padding = group.get("padding", .16)
    title = group.get("title", "")
    box = wrap_text(font, title, group.get("fontSize", 11), True, right-left, "group "+group["id"])
    header = group.get("headerHeight", box["height"] + .055)
    if group.get("kind") == "container":
        # Use the same header metrics as prepare() for native containers.
        # Visual group headings have their own frame and type style.
        box = wrap_text(font, title, group.get("fontSize", 11), group.get("bold", False),
                        right-left, "node " + group["id"] + ".text")
        header = group.get("headerHeight", max(.32, box["height"] + .04) if title else .20)
    return {**group, "x": left-padding, "y": top-padding-header,
            "w": right-left+2*padding, "h": bottom-top+2*padding+header, "headerHeight": header,
            "_textFrame": {"x": left, "y": top-padding-header, "w": box["width"]+.002, "h": header},
            "fontSize": group.get("fontSize", 11), "text": box["text"], "bold": True, "_lineHeight":box["lineHeight"]}


def shift(items, dx, dy):
    for item in items:
        item["x"] += dx
        item["y"] += dy


def peer_components(blocks, owner, parallel):
    """Keep blocks linked by leaf alignment in a common coordinate system."""
    neighbors = {key: set() for key in blocks}
    before = {key: set() for key in blocks}
    after = {key: set() for key in blocks}
    index = {key: i for i, key in enumerate(blocks)}
    for peers in parallel:
        for first, second in zip(peers, peers[1:]):
            a, b = owner[first], owner[second]
            if a != b:
                neighbors[a].add(b)
                neighbors[b].add(a)
                before[b].add(a)
                after[a].add(b)

    components = {}
    assigned = set()
    for root in blocks:
        if root in assigned:
            continue
        connected = {root}
        pending = [root]
        while pending:
            others = neighbors[pending.pop()] - connected
            connected.update(others)
            pending.extend(others)
        assigned.update(connected)
        # Resolve cross-axis order only. Different members of the same block
        # may align with different ranks in several neighboring blocks.
        degree = {key: len(before[key]) for key in connected}
        ready = [key for key in connected if degree[key] == 0]
        ordered = []
        while ready:
            following = []
            for key in sorted(ready, key=index.__getitem__):
                ordered.append(key)
                for child in after[key]:
                    degree[child] -= 1
                    if degree[child] == 0:
                        following.append(child)
            ready = following
        if len(ordered) != len(connected):
            remaining = sorted((key for key in connected if degree[key]), key=index.__getitem__)
            fail("INTENT_PARALLEL_CONFLICT", "Parallel lists require conflicting block order",
                 "$.layout.parallel", blocks=remaining,
                 peers=[p for p in parallel if any(owner[n] in remaining for n in p)])
        components[root] = ordered
    return components


def place_component(keys, blocks, leaves, lanes, horizontal, along, across, font, local_lanes):
    """Align leaf centers on shared ranks, then pack their measured frames."""
    axis, length, cross, breadth = ("x","w","y","h") if horizontal else ("y","h","x","w")
    cursor = 0
    centers = {}
    for lane in lanes:
        thickness = max(leaves[n][length] for n in lane)
        for n in lane:
            centers[n] = cursor + thickness / 2
        cursor += thickness + along

    members = []
    frames = []
    lateral = 0
    for key in keys:
        block = blocks[key]
        ids = set(block["members"])
        rows = [[n for n in lane if n in ids] for lane in lanes]
        local_lanes[key] = [row for row in rows if row]
        arrange(leaves, local_lanes[key], horizontal, along, across)
        items = [leaves[n] for n in block["members"]]
        members.extend(items)
        for item in items:
            item[axis] = centers[item["id"]] - item[length] / 2
        frame = frame_group(block, leaves, font) if "id" in block else leaves[key].copy()
        offset = lateral - frame[cross]
        for item in items:
            item[cross] += offset
        frame[cross] = lateral
        frames.append(frame)
        lateral += frame[breadth] + across
    left, top, right, bottom = bounds(frames)
    shift(members, -left, -top)
    return {"w": right-left, "h": bottom-top}


def arrange_blocks(blocks, owner, leaves, pairs, parallel, horizontal, along, across, font,
                   row_limit=None, align_branches=False, workflow_source=None, trial=None):
    if all("id" not in block for block in blocks.values()):
        # Without group frames, leaf coordinates already share both axes.
        ranks = ranked(blocks, pairs, parallel, align_branches)
        slots = {}
        if workflow_source is not None:
            rules = workflow_rules(workflow_source, ranks)
            if rules["strips"]:
                ranks, slots = apply_workflow_tracks(leaves, ranks, rules, horizontal)
                if trial is not None:
                    trial["workflowTracks"] = True
        w, h = arrange(leaves, ranks, horizontal, along, across, slots)
        return w, h, {n: [[n]] for n in blocks}, ranks

    local_lanes = {}
    components = peer_components(blocks, owner, parallel)
    units = {}
    unit_of = {}
    for root, keys in components.items():
        members = [n for key in keys for n in blocks[key]["members"]]
        ids = set(members)
        unit_of.update(dict.fromkeys(ids, root))
        lanes = ranked(members, [(a,b) for a,b in pairs if a in ids and b in ids],
                       [p for p in parallel if p[0] in ids], align_branches)
        units[root] = place_component(keys, blocks, leaves, lanes, horizontal, along, across,
                                      font, local_lanes)
    outer_pairs = [(unit_of[a],unit_of[b]) for a,b in pairs if unit_of[a] != unit_of[b]]
    ranks = ranked(units, outer_pairs, [], align_branches)
    # Independent components can occupy successive rows. Each component
    # already includes every group coupled by an explicit parallel list.
    if row_limit and not horizontal:
        ranks = [lane[i:i+row_limit] for lane in ranks for i in range(0, len(lane), row_limit)]
    bw,bh = arrange(units, ranks, horizontal, along, across)
    for n, root in unit_of.items():
        leaves[n]["x"] += units[root]["x"]
        leaves[n]["y"] += units[root]["y"]
    outer_lanes = [[key for root in lane for key in components[root]] for lane in ranks]
    return bw, bh, local_lanes, outer_lanes


def place_intent(original, layout, font, measured, width, gap, cross_gap, use_draft, row_limit=None,
                 fit_branches=False, align_only=False, workflow_fallback=None, trial=None):
    scene = deepcopy(original)
    page = scene["page"]
    leaves = {n["id"]: n for n in scene["nodes"] if n["kind"] != "container"}
    for key, n in leaves.items():
        # size_node consumes the size constraint, so retain its provenance here.
        n["_input"] = {field: n[field] for field in ("size", "position", "draft") if field in n}
    leaf_sizes(leaves, scene["edges"], font, width, use_draft, fit_branches)
    visual = layout.get("groups", [])
    blocks = {}
    for n in scene["nodes"]:
        if n["kind"] == "container":
            members = [key for key, leaf in leaves.items() if leaf.get("parent") == n["id"]]
            if n.get("parent") or not members:
                fail("INTENT_CONTAINMENT_DEPTH", "Intent placement supports a single layer of nonempty containers",
                     "node " + n["id"])
            blocks[n["id"]] = dict(n, members=members, title=n.get("text", ""),
                                    padding=n.get("padding", .25))
    for group in visual:
        if group["id"] in blocks or any(set(group["members"]) & set(b["members"]) for b in blocks.values()):
            fail("INTENT_GROUP_CONTAINMENT", "Overlapping visual and containment blocks require separate layouts",
                 "group " + group["id"])
        blocks[group["id"]] = dict(group)
    owner = {k: key for key, b in blocks.items() for k in b["members"]}
    for k in sorted(leaves):
        if k not in owner:
            owner[k] = k
            blocks[k] = {"members": [k]}

    horizontal = layout["direction"] == "right"
    pairs = [(e["from"], e["to"]) for e in scene["edges"] if e.get("role", "main") == "main"]
    for order in layout.get("order", []):
        pairs.extend(zip(order, order[1:]))
    parallel = layout.get("parallel", [])
    label_width = max((min(option["width"] for option in choices
                           if option["text"].count("\n") == min(x["text"].count("\n") for x in choices))
                       for choices in measured.values() if choices), default=0)
    along = max(gap, min(1.20, label_width + .105)) if horizontal else gap
    across = cross_gap if horizontal else max(cross_gap, min(1.40, label_width + .16))
    bw, bh, local_lanes, outer_lanes = arrange_blocks(
        blocks, owner, leaves, pairs, parallel, horizontal, along, across, font, row_limit,
        fit_branches or align_only, None if workflow_fallback else original, trial)
    title_h = 0
    if page.get("title"):
        title_h = max(.38, wrap_text(font, page["title"], page.get("titleFontSize",16), True,
                                     page.get("width", max(bw, 5.6))-.6, "$.page.title")["height"])
        page["_titleHeight"] = title_h
    top = title_h + .21 if title_h else .18
    margin = .18
    page.setdefault("width", max(3.5, bw + 2*margin))
    page.setdefault("height", max(2.2, bh + top + margin))
    dx = max(margin, (page["width"]-bw)/2)
    dy = max(top, (page["height"]-top-bh)/2+top)
    # Measured peer frames can fit a fixed sheet with a smaller outside
    # margin. Keep title clearance and distribute the remaining whitespace.
    if dx + bw > page["width"]:
        dx = max(0, (page["width"] - bw) / 2)
    if dy + bh > page["height"]:
        usable_top = title_h + .10 if title_h else 0
        dy = usable_top + max(0, (page["height"] - usable_top - bh) / 2)
    anchors = [n for n in leaves.values() if "position" in n]
    if anchors:
        dx, dy = (median(n["position"][axis] - n[axis] for n in anchors) for axis in ("x", "y"))
    shift(leaves.values(), dx, dy)
    for n in leaves.values():
        if "position" in n:
            n.update(n.pop("position"), fixed=True)
        elif use_draft:
            n.update(n.get("draft", {}).get("position", {}))
    check_locked_overlap(anchors)
    if len(anchors) > 1 and not use_draft:
        fit_among_anchors(leaves, anchors, original["page"], title_h + .10 if title_h else 0.,
                          pairs, parallel, horizontal, along, across)
    constraints = []
    for n in leaves.values():
        source = n.pop("_input")
        for key in ("position","size"):
            if key in source:
                constraints.append(dict(node=n["id"], field=key, status="locked", value=source[key]))
        for key, draft in n.pop("draft", {}).items():
            actual = {"width":n["w"], "height":n["h"]} if key=="size" else {"x":n["x"], "y":n["y"]}
            constraints.append(dict(node=n["id"], field=key, requested=draft, selected=actual,
                                    status="retained" if draft==actual else "adjusted",
                                    reason="Measured content, relative ranks, routing channels and page fit"))
    for edge in scene["edges"]:
        for key in ("fromPort", "toPort", "route", "label"):
            if key in edge:
                constraints.append(dict(edge=edge["id"], field=key, status="locked", value=edge[key]))
        if "route" in edge:
            edge["fixedRoute"] = edge.pop("route")
    scene = prepare(scene, font=font)
    scene["groups"] = [frame_group(group, leaves, font) for group in visual]
    # Bounds are evaluated after honoring all fixed positions.
    boxes = scene["nodes"] + scene["groups"]
    page = scene["page"]
    if anchors and not use_draft:
        for dim, axis, length in (("width", "x", "w"), ("height", "y", "h")):
            if dim not in original["page"]:
                required = max(box[axis]+box[length] for box in boxes) + margin
                page[dim] = max(page[dim], required)
    for box in boxes:
        if not in_page(box, page) or (title_h and box["y"] < title_h+.10):
            fail("INTENT_PAGE_CAPACITY", "Object exceeds available drawing region", "object "+box["id"],
                 page=[page["width"],page["height"]], bounds={k:box[k] for k in ("x","y","w","h")},
                 locks=constraints)
    outer = list(scene["groups"]) + [n for n in scene["nodes"] if n["id"] not in owner or owner[n["id"]]==n["id"]]
    for a,b in list(combinations(outer, 2)) + list(combinations(leaves.values(), 2)):
        if intersects(a,b):
            fail("INTENT_OVERLAP", "Objects overlap after applying layout constraints", "object "+a["id"],
                 other=b["id"], locks=constraints)
    ax,dim,c,v = ("x","w","y","h") if horizontal else ("y","h","x","w")
    for a,b in pairs:
        if leaves[a][ax]+leaves[a][dim] > leaves[b][ax]+1e-7:
            fail("INTENT_ORDER_CONFLICT", "Locked geometry conflicts with precedence", "$.layout.order",
                 first=a, second=b, locks=constraints)
    for peers in parallel:
        for a,b in zip(peers,peers[1:]):
            na,nb=leaves[a],leaves[b]
            if (abs(na[ax]+na[dim]/2-nb[ax]-nb[dim]/2)>1e-7 or na[c]+na[v]>nb[c]+1e-7):
                fail("INTENT_PARALLEL_CONFLICT", "Geometry conflicts with aligned peer order", "$.layout.parallel",
                     first=a, second=b, locks=constraints)
    placement = dict(direction=layout["direction"], blockRanks=outer_lanes, memberRanks=local_lanes,
                     constraints=constraints, cardWidthLimitIn=width, rankGapIn=along, peerGapIn=across)
    if workflow_fallback:
        placement["workflowAlignmentFallback"] = workflow_fallback
    return scene, placement


def same_size_branch_trial(candidates, trials, selected):
    """Keep the winning card sizes, rank gaps and block limits for a local reorder."""
    option, already_aligned = candidates[selected - 1]
    route = trials[selected - 1]["routeQuality"]
    if not already_aligned and not option[3] and (route[0] or route[3]):
        yield option, False


def resolve_intent(original, layout, font, config, result):
    from .service import set_fill
    measured = measure_labels(original["edges"], font)
    trials = []
    best = quality = selected = None
    started = perf_counter()
    target = config.target_width_mm is not None or config.target_height_mm is not None
    # Changes in card aspect, rank separation and peer spacing are tried
    # together, before routing the measured geometry against the fixed page.
    options = [(2.50,.48,.40),(2.85,.39,.26),(2.15,.40,.25),
               (3.15,.34,.18),(1.85,.33,.22),(2.40,.63,.54)]
    candidates = [(w,g,c,False,None) for w,g,c in options]
    if target:
        candidates[1:1] = [(1.45,.30,.22,False,None)]
        if layout.get("groups") and layout["direction"] == "down":
            candidates[2:2] = [(1.85,.45,.30,False,2), (2.15,.42,.32,False,1)]
    if any(n.get("draft") for n in original["nodes"]):
        candidates.insert(0, (*options[0], True, None))
    # Retain the existing positions for comparison and for inputs anchored
    # to authored geometry. One extra candidate compares compact titles and
    # places unlocked branches beside their own predecessors.
    fitted = (1.45, .30, .22, False, None)
    compare_branches = (not any("position" in n for n in original["nodes"])
                        and not any("route" in e or "label" in e for e in original["edges"]))
    candidates = [(option, False) for option in candidates]
    if compare_branches:
        candidates.insert(1, (fitted, True))

    def recheck_winner():
        # A narrow fitted trial can lose on page aspect even if its branch
        # order is easier to follow. Reuse the winning size and gaps once
        # when connectors cross or take long detours on that actual page.
        if not compare_branches or best is None:
            return
        yield from same_size_branch_trial(candidates, trials, selected)

    fallback = {}

    def fixed_page_recovery():
        # Give authored sheet dimensions priority only after exhausting
        # aligned candidates. Preserve the actual blockers for the receipt.
        if best or not {"width", "height"}.intersection(original["page"]):
            return
        blocked = [{"attempt": i, "code": t["failure"]["code"], "stage": t["stage"]}
                   for i, t in enumerate(trials, 1) if "failure" in t and t.get("workflowTracks")]
        capacity = {"INTENT_PAGE_CAPACITY", "LABEL_NO_SPACE", "ROUTE_UNAVAILABLE", "PORT_CAPACITY"}
        if (not blocked or not any(row["code"] in capacity for row in blocked)
                or any(t.get("stage") == "scene" for t in trials)):
            return
        fallback.update(reason="fixedPage", failedAlignedAttempts=blocked)
        yield from candidates

    geometries = {}
    for (width, gap, cross, use_draft, row_limit), fit_branches in chain(
            candidates, recheck_winner(), fixed_page_recovery()):
        align_only = len(trials) == len(candidates) and not fallback
        trial = dict(cardWidthLimitIn=width, gapIn=gap, peerGapIn=cross, tryDraft=use_draft,
                     maxIndependentBlocksPerRow=row_limit)
        if align_only:
            trial["branchOrder"] = "median predecessor position"
            trial["sameSizeAsAttempt"] = selected
        if fit_branches:
            trial["branchOrder"] = "median predecessor position"
            trial["titleWidthLimitIn"] = width
        if fallback:
            trial["fixedPageRecovery"] = True
        trials.append(trial)
        try:
            result["_stage"] = "layout"
            source = original
            if use_draft and not {"width","height"} <= original["page"].keys():
                # Compare optional draft geometry to the usable page computed
                # from content and real anchors. An oversized draft alone does
                # not allocate a larger sheet.
                natural, _ = place_intent(original, layout, font, measured, width, gap, cross, False,
                                          workflow_fallback=fallback, trial=trial)
                source = dict(original, page=dict(original["page"],
                              width=natural["page"]["width"], height=natural["page"]["height"]))
            scene, placement = place_intent(source, layout, font, measured, width, gap, cross, use_draft,
                                            row_limit, fit_branches, align_only, fallback, trial)
            if target:
                geometry = (scene["page"]["width"], scene["page"]["height"],
                            tuple((n["id"], n["x"], n["y"], n["w"], n["h"]) for n in scene["nodes"]))
                if geometry in geometries:
                    trial["sameGeometryAsAttempt"] = geometries[geometry]
                    continue
                geometries[geometry] = len(trials)
                if quality is not None and quality[0] == 0 and config.min_font_pt is not None:
                    # No routing outcome can improve on zero ambiguous labels.
                    # Routing preserves page dimensions and font sizes, so a
                    # strictly larger point deficit cannot win on a later key.
                    page = page_metrics(scene["page"], config)
                    minimum = min((pt * page["scale"] for _, _, _, pt in text_runs(scene)), default=None)
                    if minimum is not None and config.min_font_pt - minimum > quality[1] + 1e-8:
                        trial.update(page=page, minimumEffectivePt=minimum, dominatedByAttempt=selected,
                                     reason="Greater point deficit than an earlier candidate with zero ambiguous labels.")
                        continue
            set_fill(scene, config.color_fill)
            result["_stage"] = "routing"
            work, router = route_scene(scene, font, compatible=False, measured=measured)
            scene_checks(work, original, compatible=False)
            result["_stage"] = "scene"
            check_workflow_alignment(work, original, placement)
        except GenerationError as error:
            trial["failure"] = error.diagnostic
            trial["stage"] = result["_stage"]
            if error.diagnostic["code"] in ("INTENT_RANK_CYCLE","INTENT_RANK_CONFLICT",
                                            "INTENT_CONTAINMENT_DEPTH","INTENT_GROUP_CONTAINMENT"):
                break
            continue
        route = route_quality(work, measured, work["_labelOwnership"])
        trial["routeQuality"] = route
        score = route
        if target:
            report = diagnose(work, original, config, placement, font)
            score = selection_metrics(report, route)
            trial.update(page=report["page"], minimumEffectivePt=report["minimumEffectivePt"],
                         belowTargetCount=report["belowTargetCount"], quality=score)
        if quality is None or score < quality:
            best, quality, selected = (work, placement, router), score, len(trials)
        compare_next = compare_branches and len(trials) == 1 and route[4] > 0
        if not compare_next and route[:4] == (0,0,0,0) and (not target or (
                report["belowTargetCount"] == 0 and score[3] < .000001 and score[4:6] == (0,0)
                and report["space"]["leafAreaFraction"] >= .35)):
            break
    if best is None:
        # Report the terminal structural conflict or the last ordinary trial.
        # Copy its diagnostic before attaching the history to avoid a cycle.
        last = next(trial for trial in reversed(trials) if "failure" in trial)
        failure = last["failure"]
        result["_stage"] = last["stage"]
        error = GenerationError(failure["code"], failure["message"],
                                failure.get("path","$.layout"), **failure.get("details",{}))
        error.diagnostic["layoutAttempts"] = trials
        raise error
    if trials[0]["tryDraft"] and selected != 1:
        rejected = trials[0].get("failure")
        for entry in best[1]["constraints"]:
            if entry["status"] == "adjusted":
                if rejected:
                    entry["reason"] = ("Draft layout rejected: " + rejected["code"] + " at "
                                       + rejected.get("path", "$.layout") + ". " + rejected["message"])
                else:
                    entry["reason"] = f"Retained draft in attempt 1 ranked below selected attempt {selected}."
    best[1]["attempts"] = trials
    best[1]["selection"] = {
        "selectedAttempt": selected, "attemptCount": len(trials),
        "candidateLimit": len(candidates) * (2 if fallback else 1) + int(any("sameSizeAsAttempt" in t for t in trials)),
        "criteria": SELECTION_CRITERIA if target else ["routeQuality"],
        "method": "Lowest lexicographic score among feasible trials; first equal candidate wins."}
    best[0]["_intentPlacement"] = best[1].copy()
    # Runtime belongs in the validation receipt, outside the hashed scene.
    best[1]["selection"] = dict(best[1]["selection"], elapsedSeconds=round(perf_counter()-started, 4))
    return best
