"""Physical display metrics for resolved geometry. Dimensions in the scene are inches."""
import math

from .errors import GenerationError
from .workflow_alignment import workflow_rules


SELECTION_CRITERIA = ["labelOwnership", "worstPointShortfall", "runsBelowMinimum", "aspectPenalty",
                      "sparseNodes", "sparseGroups", "crossings", "excessLabelWrap", "detours",
                      "spaceOutsideLeaves", "bends", "routeLength"]


def measure_workflow_alignment(scene, original, placement):
    """Measure final frames against peer membership inferred from the input and ranks."""
    tolerance = .001  # Drawing inches; 0.0254 mm, above serialized geometry rounding.
    report = {"status": "NOT_APPLICABLE", "toleranceIn": tolerance, "checks": []}
    layout = original.get("layout", {})
    if "schema" not in original or not isinstance(layout, dict) or layout.get("mode") != "intent":
        return report
    rules = workflow_rules(original, placement["blockRanks"])
    nodes = {node["id"]: node for node in scene["nodes"]}
    horizontal = layout.get("direction") == "right"
    axis, extent, measure = ("y", "h", "centerY") if horizontal else ("x", "w", "centerX")

    def record(kind, members, dimensions):
        spread = {name: max(values) - min(values) for name, values in dimensions.items()}
        status = "FAIL" if any(delta > tolerance for delta in spread.values()) else "PASS"
        report["checks"].append({"kind": kind, "objectIds": members, "spreadIn": spread, "status": status})

    for kind, groups in (("spine", [rules["spine"]]), ("column", rules["columns"])):
        for members in groups:
            if len(members) >= 2:
                centers = [nodes[k][axis] + nodes[k][extent] / 2 for k in members]
                record(kind, members, {measure: centers})
    for members in rules["peerSizes"]:
        record("peerSize", members, {"width": [nodes[k]["w"] for k in members],
                                     "height": [nodes[k]["h"] for k in members]})
    if report["checks"]:
        report["status"] = "FAIL" if any(c["status"] == "FAIL" for c in report["checks"]) else "PASS"
    if report["status"] == "FAIL":
        fallback = placement.get("workflowAlignmentFallback", {})
        fixed = {key: original["page"][key] for key in ("width", "height") if key in original.get("page", {})}
        if fixed and fallback.get("reason") == "fixedPage" and fallback.get("failedAlignedAttempts"):
            report.update(status="WARNING", fixedPageIn=fixed, fallback=fallback)
    return report


def check_workflow_alignment(scene, original, placement):
    report = measure_workflow_alignment(scene, original, placement)
    if report["status"] == "FAIL":
        violations = [item for item in report["checks"] if item["status"] == "FAIL"]
        raise GenerationError("WORKFLOW_ALIGNMENT_MISMATCH",
                              "Workflow frame centers or peer dimensions exceed the alignment tolerance.",
                              "$.layout", toleranceIn=report["toleranceIn"], violations=violations)
    return report


def validate_target(config):
    for field in ("target_width_mm", "target_height_mm", "min_font_pt"):
        value = getattr(config, field)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
            raise GenerationError("INVALID_DISPLAY_TARGET", "Expected a positive finite number", "$.config." + field)
    if config.min_font_pt is not None and config.target_width_mm is None and config.target_height_mm is None:
        raise GenerationError("INVALID_DISPLAY_TARGET", "A point-size target requires a display width or height",
                              "$.config.min_font_pt")


def page_metrics(page, config):
    w, h = page["width"], page["height"]
    ratios = [limit / (inches * 25.4) for limit, inches in ((config.target_width_mm, w),
              (config.target_height_mm, h)) if limit is not None]
    scale = min(ratios) if ratios else None
    return {"widthIn": w, "heightIn": h, "aspectRatio": w/h, "scale": scale,
            "displayWidthMm": None if scale is None else 25.4*w*scale,
            "displayHeightMm": None if scale is None else 25.4*h*scale}


def text_runs(scene):
    page = scene["page"]
    if page.get("title"):
        yield "page", "diagram_title", "title", page.get("titleFontSize", 16)
    for node in scene["nodes"]:
        if node.get("text"):
            yield "node", node["id"], "text", node.get("fontSize", 11)
        if node.get("detail"):
            yield "node", node["id"], "detail", node.get("detailFontSize", 10)
    for edge in scene["edges"]:
        if edge.get("text"):
            yield "edge", edge["id"], "text", edge.get("fontSize", 10)
    for group in scene.get("groups", []):
        if group.get("title"):
            yield "group", group["id"], "title", group.get("fontSize", 11)


def area(item):
    return item["w"] * item["h"]


def space_usage(scene, original, font):
    nodes = {n["id"]: n for n in scene["nodes"]}
    authored = {n["id"]: n for n in original["nodes"]}
    leaves = [n for n in nodes.values() if n["kind"] != "container"]
    sparse = []
    for node in leaves:
        if area(node) < 2:
            continue
        # Use the rendered lines and the active font. The inner text frame may
        # fill the whole shape even when its measured text occupies very little.
        ink = 0.
        for field, pt, bold in (("text", node.get("fontSize", 11), node.get("bold", False)),
                                ("detail", node.get("detailFontSize", 10), False)):
            if node.get(field):
                for line in node[field].split("\n"):
                    w, h = font.measure(line, pt, bold)
                    ink += w * h
        ratio = ink / area(node)
        if ratio < .20:
            source = authored[node["id"]]
            size = source.get("size")
            if size is None and "w" in source and "h" in source:
                size = {"width": source["w"], "height": source["h"]}
            retained = source.get("draft", {}).get("size") == {"width": node["w"], "height": node["h"]}
            source_kind = "hardSize" if size else ("softDraft" if retained else "automatic")
            sparse.append({"id": node["id"], "widthIn": node["w"], "heightIn": node["h"],
                           "measuredTextAreaIn2": ink, "textAreaFraction": ratio,
                           "sizeSource": source_kind, "hardSize": size})
    groups = []
    for group in [*scene.get("groups", []), *(n for n in nodes.values() if n["kind"] == "container")]:
        members = group.get("members")
        if members is None:
            members = [n["id"] for n in nodes.values() if n.get("parent") == group["id"]]
        ratio = sum(area(nodes[k]) for k in members) / area(group)
        groups.append({"id": group["id"], "kind": group.get("kind", "visual"), "members": members,
                       "memberAreaFraction": ratio, "sparse": ratio < .35})
    page = scene["page"]
    fraction = sum(area(n) for n in leaves) / (page["width"] * page["height"])
    return {"leafAreaFraction": fraction, "spaceOutsideLeafFramesFraction": 1 - fraction,
            "sparseNodes": sparse, "groups": groups}


def major_drafts(placement, runs, floor):
    result = []
    for entry in placement.get("constraints", []):
        if entry.get("status") != "adjusted":
            continue
        old, new = entry["requested"], entry["selected"]
        if entry["field"] == "size":
            large = any(abs(old[k]-new[k]) >= max(.25, old[k]*.25) for k in old)
        else:
            large = math.dist([old["x"], old["y"]], [new["x"], new["y"]]) >= .5
        if large:
            sizes = [r["effectivePt"] for r in runs if r["kind"] == "node" and r["id"] == entry["node"]
                     and r["effectivePt"] is not None]
            minimum = min(sizes, default=None)
            result.append({**entry, "selectedMinEffectivePt": minimum,
                           "selectedBelowTarget": None if floor is None or minimum is None else minimum < floor})
    return result


def diagnose(scene, original, config, placement, font):
    page = page_metrics(scene["page"], config)
    scale, floor = page["scale"], config.min_font_pt
    runs = [{"kind": kind, "id": key, "field": field, "originalPt": pt,
             "effectivePt": None if scale is None else pt * scale} for kind, key, field, pt in text_runs(scene)]
    below = [r for r in runs if floor is not None and r["effectivePt"] + 1e-8 < floor]
    minimum = min((r["effectivePt"] for r in runs if scale is not None), default=None)
    space = space_usage(scene, original, font)
    conflicts = []
    if below:
        limiting = [axis for axis, target in (("width", config.target_width_mm), ("height", config.target_height_mm))
                    if target and math.isclose(scale, target/(scene["page"][axis]*25.4))]
        fixed = {k: original["page"][k] for k in limiting if k in original["page"]}
        conflicts.append({"code": "EFFECTIVE_FONT_BELOW_TARGET", "minimumPt": floor, "actualMinimumPt": minimum,
                          "affected": below, "limitingDimensions": limiting, "fixedPageIn": fixed,
                          "reason": "Fixed sheet dimension limits proportional scale." if fixed else
                          "The selected layout at the requested physical size yields the listed point sizes.",
                          "options": ["Increase display size.", "Rearrange unlocked content within a smaller page."]})
    for node in space["sparseNodes"]:
        if node["hardSize"]:
            conflicts.append({"code": "SPARSE_HARD_SIZE", "node": node["id"], "inputSize": node["hardSize"],
                              "textAreaFraction": node["textAreaFraction"],
                              "reason": "The supplied dimensions reserve this area independently of text demand.",
                              "options": ["Retain the required dimensions.", "Compare an explicitly unlocked variant."]})
    return {"page": page, "target": {"widthMm": config.target_width_mm, "heightMm": config.target_height_mm,
            "minimumPt": floor}, "textRuns": runs, "minimumEffectivePt": minimum, "belowTargetCount": len(below),
            "space": space, "majorDraftChanges": major_drafts(placement, runs, floor), "conflicts": conflicts}


def selection_metrics(report, route):
    page, space = report["page"], report["space"]
    floor, minimum = report["target"]["minimumPt"], report["minimumEffectivePt"]
    target_w, target_h = report["target"]["widthMm"], report["target"]["heightMm"]
    ratio = page["aspectRatio"]
    # With both bounds, reward using the available rectangle. With a single
    # bound, penalize only extreme strips; portrait sheets are legitimate.
    if target_w and target_h:
        aspect = abs(math.log(ratio / (target_w/target_h)))
    else:
        aspect = max(0., math.log(.60/ratio), math.log(ratio/2.0))
    return (route[1], max(0., floor-minimum) if floor is not None and minimum is not None else 0.,
            report["belowTargetCount"], aspect, len(space["sparseNodes"]),
            sum(g["sparse"] for g in space["groups"]), route[0], route[2], route[3],
            1-space["leafAreaFraction"], route[4], route[5])
