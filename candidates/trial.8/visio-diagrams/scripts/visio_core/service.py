"""The public generation service shared by CLI, Python and compatibility commands."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
import platform
import sys
from uuid import uuid4
from pathlib import Path
import shutil
import tempfile
from xml.etree import ElementTree as ET
import zipfile

from .delivery_layout import check_workflow_alignment, diagnose, validate_target
from .containers import prepare
from .contracts import load, normalize, reject
from .edge_labels import measure_labels
from .errors import GenerationError
from .layout import place, select_strategy
from .package_check import check_package
from .preview import write_preview
from .resources import DEFAULT_FONT, resolve_stencil
from .scene import check_router_capacity, label_excess, route_quality, route_scene, scene_checks
from .text_layout import WindowsFont
from .vsdx_writer import write_vsdx

VERSION = "1.5.0rc6"


@dataclass(frozen=True)
class GenerationConfig:
    stencil: str | Path | None = None
    font_family: str | None = None
    color_fill: str = "spec"
    backend: str = "python"
    target_width_mm: float | None = None
    target_height_mm: float | None = None
    min_font_pt: float | None = None


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(staged):
    """Publish the validation receipt last. Competing writers cannot overwrite files."""
    published = []
    try:
        for src, dest in staged:
            try:
                if os.name == "nt":
                    src.rename(dest)  # Windows rename fails if the target exists.
                else:
                    os.link(src, dest)
            except PermissionError:
                # Shared folders can disallow rename while permitting creation.
                with dest.open("xb") as stream:
                    published.append(dest)
                    with src.open("rb") as source:
                        shutil.copyfileobj(source, stream)
            else:
                published.append(dest)
    except OSError:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        raise


def generate(description, output, *, config=None):
    """Return a JSON-serializable result. FAILED results always have empty artifacts."""
    config = config or GenerationConfig()
    result = {"version": VERSION, "status": "FAILED", "backend": config.backend, "artifacts": {},
              "strategy": None, "checks": {}, "diagnostics": [], "visioAcceptance": "NOT_RUN",
              "visualReview": "NOT_RUN", "png": None}
    try:
        _generate(description, output, config, result)
    except GenerationError as error:
        result["diagnostics"].append(error.diagnostic)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError, zipfile.BadZipFile, ET.ParseError) as error:
        result["diagnostics"].append({"code": "GENERATION_FAILED", "message": str(error) or type(error).__name__})
    if result["status"] == "FAILED":
        result["artifacts"] = {}
        if result["diagnostics"]:
            diagnostic = result["diagnostics"][-1]
            stage = result.pop("_stage", "input")
            if stage == "routing" and diagnostic["code"].startswith(("LABEL_", "TEXT_", "FONT_")):
                stage = "text"
            diagnostic.setdefault("stage", stage)
    return result


def ambiguous_labels(work):
    return [edge_id for edge_id, quality in work["_labelOwnership"].items()
            if quality["otherCloser"] and quality["supportSegment"] is not None]


def layout_focus(work, measured):
    receipt = work["_routingValidation"]
    constrained = []
    for edge_id in ambiguous_labels(work):
        constrained.extend((edge_id, work["_labelOwnership"][edge_id]["nearestOtherEdge"]))
    constrained.extend(edge["id"] for edge in work["edges"] if label_excess(edge, measured))
    constrained.extend(edge_id for pair in receipt["crossingEdgePairs"] for edge_id in pair)
    constrained.extend(receipt["detourEdges"])
    return list(dict.fromkeys(constrained))


def spacing_candidates(work, placement, focus, measured, strategy, direction, *, failed):
    ranks = placement["layers"]
    edges = {edge["id"]: edge for edge in work["edges"]}
    edge = edges[focus[0]]
    sizes = Counter(ranks.values())
    cross = {}
    if strategy == "layered":
        axis, dim, xy = ("y", "h", 1) if direction == "down" else ("x", "w", 0)
        for edge_id in focus:
            active = edges[edge_id]
            first, last = ranks[active["from"]], ranks[active["to"]]
            points = [p[xy] for p in active.get("routePoints", ())]
            lo, hi = (min(points), max(points)) if points else (float("inf"), -float("inf"))
            for node in work["nodes"]:
                rank = ranks[node["id"]]
                in_span = first <= rank <= last or (node[axis] <= hi and node[axis] + node[dim] >= lo)
                if sizes[rank] > 1 and in_span:
                    cross[rank] = .50
    # An across-layer adjustment can leave the length and position of every
    # unaffected layer interval unchanged.
    if cross and not failed:
        yield {}, cross, None
    yield from approach_spacing(edge, ranks, sizes, measured, strategy, direction)

    if not failed and cross:
        # A crowded, crossing corridor can also benefit from reversing one
        # affected sibling order. Compare at most two of these alternatives.
        last = max(ranks[edges[item]["to"]] for item in focus)
        first = min(ranks[edges[item]["from"]] for item in focus)
        nearby = [e for e in work["edges"] if first <= ranks[e["from"]] < ranks[e["to"]] <= last]
        demanding = max(nearby, key=lambda e: max((m["width"] for m in measured.get(e["id"], ())), default=0))
        gaps, siblings, _ = next(approach_spacing(demanding, ranks, sizes, measured, strategy, direction))
        for rank in sorted(cross)[:2]:
            yield gaps, siblings, rank


def approach_spacing(edge, ranks, sizes, measured, strategy, direction):
    head, tail = ranks[edge["from"]], ranks[edge["to"]]
    variants = measured.get(edge["id"], ())
    counts = [item["text"].count("\n") for item in variants]
    allowed = min(counts, default=0) + 1
    axis = "width" if direction == "right" else "height"
    demand = min((item[axis] for item, count in zip(variants, counts) if count <= allowed), default=.34)
    delta = max(.33, min(.90, demand * .65))
    siblings = {rank: .30 for rank in (head, tail) if strategy == "layered" and sizes[rank] > 1}
    yield {tail - 1: delta}, siblings, None
    yield {tail - 1: delta * 2}, siblings, None


def resolve(original, layout, strategy, compatible, font, config, result):
    from time import perf_counter
    from .delivery_layout import SELECTION_CRITERIA, selection_metrics
    from .layout import layers
    if strategy == "intent":
        from .intent_layout import resolve_intent
        font.avoid_orphans = True
        return resolve_intent(original, layout, font, config, result)
    movable = not compatible and layout["mode"] == "auto"
    target = movable and (config.target_width_mm is not None or config.target_height_mm is not None)
    started = perf_counter()
    result["_stage"] = "text"
    measured = None if compatible else measure_labels(original["edges"], font)
    trials = []
    first_failure = best = best_quality = None
    pending = [({}, {}, None)]
    if target:
        # Reduce only unprescribed gaps. All authored sizes, layer assignments
        # and routes still pass the same scene and text checks as the baseline.
        columns, _ = layers(original["nodes"], original["edges"])
        rank_spacing = {rank: -.35 for rank in range(max(columns))}
        peer_spacing = {k: -.4 for k, row in columns.items() if len(row) > 1 and strategy == "layered"}
        if rank_spacing or peer_spacing:
            pending.append((rank_spacing, peer_spacing, None))
    for extra_gaps, extra_cross, reversed_layer in pending:
        if not target and reversed_layer is not None and best_quality is not None and best_quality[:3] == (0, 0, 0):
            # Prefer the caller's sibling order when its labels and routes fit.
            break
        trial = {"extraLayerGapIn": extra_gaps, "extraSiblingGapIn": extra_cross}
        if reversed_layer is not None:
            trial["reversedLayer"] = reversed_layer
        trials.append(trial)
        result["_stage"] = "layout"
        try:
            work, placement = ((prepare(original), {}) if compatible else
                               place(original, layout, strategy, font, measured, extra_gaps, extra_cross, reversed_layer))
            set_fill(work, config.color_fill)
            result["_stage"] = "routing"
            work, router = route_scene(work, font, compatible=compatible, measured=measured)
            if target:
                scene_checks(work, original, compatible=False)
        except GenerationError as error:
            if not movable or (not (target and len(trials) > 1) and error.diagnostic["code"] not in {
                    "LABEL_NO_SPACE", "ROUTE_UNAVAILABLE", "PORT_CAPACITY", "ROUTE_SEARCH_LIMIT", "PAGE_CAPACITY"}):
                raise
            trial["failure"] = error.diagnostic
            if len(trials) == 1:
                first_failure = error
                focus = error.diagnostic.get("details", {}).get("edgeId")
                if focus:
                    trial["focusEdges"] = [focus]
                    pending.extend(spacing_candidates(work, placement, [focus], measured, strategy,
                                                      layout["direction"], failed=True))
            continue
        if not movable:
            return work, placement, router
        quality = route_quality(work, measured, work["_labelOwnership"])
        trial.update(quality=quality, pageIn=[work["page"]["width"], work["page"]["height"]])
        if target:
            report = diagnose(work, original, config, placement, font)
            quality = selection_metrics(report, quality)
            trial.update(displayScore=quality, minimumEffectivePt=report["minimumEffectivePt"],
                         belowTargetCount=report["belowTargetCount"])
        if best is None or quality < best_quality:
            best, best_quality = (work, placement, router), quality
            selected = len(trials)
        focus = layout_focus(work, measured)
        if not focus:
            if target:
                continue
            break
        trial["focusEdges"] = focus
        if len(trials) == 1:
            pending.extend(spacing_candidates(work, placement, focus, measured, strategy,
                                              layout["direction"], failed=False))

    if best is None:
        first_failure.diagnostic["layoutAttempts"] = [dict(trial, failure=dict(trial["failure"])) for trial in trials]
        raise first_failure
    best[1]["attempts"] = trials
    if target:
        best[1]["selection"] = {"selectedAttempt": selected, "attemptCount": len(trials),
                                "elapsedSeconds": round(perf_counter()-started, 4), "criteria": SELECTION_CRITERIA,
                                "method": "Lowest lexicographic displayScore among feasible trials."}
    main, cross = best[1]["extraLayerGapIn"], best[1]["extraSiblingGapIn"]
    if main or cross:
        compact = any(v < 0 for v in (*main.values(), *cross.values()))
        result["diagnostics"].append({"code": "AUTO_SPACING_REDUCED" if compact else "AUTO_SPACING_INCREASED",
                                      "severity": "info", "message": "Reduced spacing for the requested display size."
                                      if compact else "Additional space was reserved near competing connectors or labels.",
                                      "extraLayerGapIn": main, "extraSiblingGapIn": cross})
    return best


def _generate(description, output, config, result):
    if config.backend not in ("python", "com"):
        reject("BACKEND_UNKNOWN", "Expected python or the legacy com backend", "$.config.backend")
    result["identity"] = {"runId": uuid4().hex,
                          "environment": {"os": platform.system(), "osVersion": platform.version(),
                                          "python": sys.version.split()[0], "backend": config.backend}}
    validate_target(config)
    source = load(description, identity=result["identity"])
    original, layout, compatible = normalize(source, legacy_com=config.backend == "com")
    if not compatible:
        check_router_capacity(original)
    if config.color_fill not in ("spec", "on", "off"):
        reject("COLOR_MODE_UNKNOWN", "Expected spec, on or off", "$.config.color_fill")
    result["checks"]["input"] = {"status": "PASS", "schema": "legacy-geometry" if compatible else source["schema"]}
    result["_stage"] = "output"
    output = Path(output)
    if output.suffix.lower() != ".vsdx":
        reject("INVALID_OUTPUT", "Output path must end in .vsdx", output)
    # Resolve the parent only, to reject rather than follow an existing target symlink.
    output = output.parent.resolve() / output.name
    preview_suffix = ".png" if config.backend == "com" else ".svg"
    targets = {"vsdx": output, "preview": output.with_suffix(preview_suffix),
               "scene": output.with_suffix(".planned.json"), "report": output.with_suffix(".validation.json")}
    for path in targets.values():
        if os.path.lexists(path):
            reject("OUTPUT_EXISTS", f"Refusing to overwrite {path}", path)
    result["_stage"] = "strategy"
    if layout["mode"] == "intent":
        strategy, rejected = "intent", {}
    else:
        strategy, rejected = ("positioned", {}) if compatible else select_strategy(original, layout)
    result["strategy"] = {"layout": strategy, "direction": None if strategy == "positioned" else layout["direction"],
                          "rejected": rejected}
    result["_stage"] = "resources"
    family = DEFAULT_FONT if config.font_family is None else config.font_family
    with WindowsFont(family) as font:
        result["resources"] = {"requestedFontFamily": font.requested, "fontFamily": font.family,
                               "fontAliases": list(font.aliases), "measurement": "Windows GDI"}
        if config.backend == "com":
            from .compat import generate_com
            generate_com(original, config, font, targets, result)
            result.pop("_stage", None)
            return
        stencil = resolve_stencil(config.stencil or original["page"].get("stencilPath"))
        result["resources"]["stencil"] = result["stencil"] = str(stencil)
        work, placement, router = resolve(original, layout, strategy, compatible, font, config, result)
        result["strategy"]["routing"] = router
        result["_stage"] = "scene"
        result["checks"]["scene"] = scene_checks(work, original, compatible=compatible)
        alignment = result["checks"]["workflowAlignment"] = check_workflow_alignment(work, original, placement)
        if alignment["status"] == "WARNING":
            result["diagnostics"].append({
                "code": "WORKFLOW_ALIGNMENT_FIXED_PAGE_FALLBACK", "severity": "warning", "stage": "layout",
                "message": "Aligned candidates failed on the fixed sheet. Review the listed frame differences.",
                "details": {"violations": [c for c in alignment["checks"] if c["status"] == "FAIL"],
                            "fixedPageIn": alignment["fixedPageIn"], "toleranceIn": alignment["toleranceIn"]}})
        result["deliveryLayout"] = diagnose(work, original, config, placement, font)

    if work.get("_routingValidation") and router != "legacy-groups":
        work["_routingValidation"].update(
            labelsPlaced=result["checks"]["scene"]["labels"],
            scope="Resolved orthogonal routes and label rectangles; see checks.scene")
    result["placement"] = placement
    result["layout"] = work.get("_localLayout")
    result["routing"] = work.get("_routingValidation")
    ambiguous = ambiguous_labels(work)
    if ambiguous:
        result["diagnostics"].append({"code": "LABEL_NEAR_OTHER_EDGE", "severity": "warning",
                                      "message": "A movable label is closer to a neighboring connector.",
                                      "edgeIds": ambiguous})
    result["unplannedEdges"] = sum(bool(edge.get("_legacySegment")) for edge in work["edges"])
    if result["unplannedEdges"]:
        result["diagnostics"].append({"code": "LEGACY_STRAIGHT_SEGMENTS", "severity": "info",
                                      "message": "Legacy port-to-port segments were retained and checked for crossings through other nodes.",
                                      "count": result["unplannedEdges"]})
    degraded = (result["layout"] or {}).get("status") == "DEGRADED"
    if degraded:
        result["diagnostics"].append({"code": "LAYOUT_PRESERVED", "severity": "warning",
                                      "message": result["layout"]["reason"]})
    result["_stage"] = "export"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visio-core-", dir=output.parent) as directory:
        staged = {key: Path(directory) / target.name for key, target in targets.items()}
        scene_bytes = json_bytes(work)
        scene_hash = hashlib.sha256(scene_bytes).hexdigest()
        staged["scene"].write_bytes(scene_bytes)
        write_preview(work, staged["preview"], scene_hash)
        ET.parse(staged["preview"])
        result["masters"] = write_vsdx(work, staged["vsdx"], stencil, style_profile="B1_NATIVE_STABLE_V2")
        result["_stage"] = "package"
        result["structure"] = result["checks"]["vsdx"] = check_package(staged["vsdx"], work)
        result["preview"] = {"kind": "GEOMETRY_SVG", "sceneSha256": scene_hash}
        result["sha256"] = sha(staged["vsdx"])
        result["hashes"] = {"vsdx": result["sha256"], "scene": scene_hash, "preview": sha(staged["preview"])}
        result["_stage"] = "publish"
        artifacts = {key: str(path) for key, path in targets.items()}
        status = "DEGRADED" if degraded else "GENERATED"
        staged["report"].write_bytes(json_bytes({key: value for key, value in result.items() if key != "_stage"}
                                                 | {"status": status, "artifacts": artifacts}))
        publish((staged[key], target) for key, target in targets.items())
        result.update(status=status, artifacts=artifacts)
        result.pop("_stage", None)


def set_fill(work, color_fill):
    use_color = work["page"].get("colorFill", True) if color_fill == "spec" else color_fill == "on"
    if not use_color:
        for node in work["nodes"]:
            node["fill"] = "#FFFFFF"
        for group in work.get("groups", []):
            group["fill"] = "#FFFFFF"
