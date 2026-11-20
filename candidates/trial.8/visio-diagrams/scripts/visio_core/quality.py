"""Explain delivery review findings using the generation and verification receipts."""
import re


def diagnostic_objects(item, source):
    details = item.get("details", {})
    ids = [details.get(k) for k in ("edgeId", "nodeId", "fromNode", "toNode", "other", "obstacle", "first", "second", "member")]
    for violation in details.get("violations", []):
        ids.extend(violation.get("objectIds", []))
    ids.extend(item.get("edgeIds", []))
    path = item.get("path", "")
    match = re.match(r"^\$\.(nodes|edges)\[(\d+)\]", path)
    if match:
        rows = source.get(match[1], [])
        index = int(match[2])
        if isinstance(rows, list) and index < len(rows) and isinstance(rows[index], dict):
            ids.append(rows[index].get("id"))
    match = re.match(r"^(?:node|group|edge|object) (.+?)(?:\.(?:route|position|size|label|text|detail|fromPort|toPort)\b|$)", path)
    if match:
        ids.append(match[1])
    return list(dict.fromkeys(x for x in ids if isinstance(x, str) and x))


def issues_for(receipt, summary, source):
    issues = []

    def add(level, code, stage, ids, condition, evidence):
        issues.append(dict(severity=level, code=code, stage=stage, objectIds=ids,
                           trigger=condition, evidence=evidence, autoFixable=False))

    for item in receipt.get("diagnostics", []):
        code = item["code"]
        if item.get("severity") == "info":
            continue
        stage = item.get("stage", "layout")
        if receipt["status"] == "FAILED" and item.get("severity") != "warning":
            # An input refusal or an unavailable output is a failed run, not
            # evidence of corrupted content in an artifact that never existed.
            level = None
            if (code.startswith(("SCENE_CONTENT", "ROUTE_PORT", "FONT_GLYPH", "GLUE_"))
                    or code.endswith("CONSTRAINT_CHANGED") or code == "VSDX_STRUCTURE_MISMATCH"):
                level = "S0"
            elif stage in ("text", "layout", "routing", "scene") and code != "ROUTER_CAPACITY":
                level = "S1"
        else:
            level = "S2"
        add(level, code, stage, diagnostic_objects(item, source), item["message"],
            {key: item[key] for key in ("path", "details", "edgeIds", "layoutAttempts") if key in item})

    alignment = summary["offline"].get("checks", {}).get("workflowAlignment", {})
    if alignment.get("status") == "FAIL":
        for check in alignment["checks"]:
            if check["status"] == "FAIL":
                add("S1", "WORKFLOW_ALIGNMENT_MISMATCH", "layout", check["objectIds"],
                    "Published workflow frames exceed the alignment tolerance.",
                    dict(check, toleranceIn=alignment["toleranceIn"], geometrySource=alignment["geometrySource"]))

    layout = receipt.get("deliveryLayout") or {}
    for conflict in layout.get("conflicts", []):
        if conflict["code"] == "EFFECTIVE_FONT_BELOW_TARGET":
            for run in conflict["affected"]:
                add("S1", conflict["code"], "display", [run["id"]],
                    "Effective point size falls below the requested minimum at the supplied display size.",
                    dict(run, minimumPt=conflict["minimumPt"], limitingDimensions=conflict["limitingDimensions"]))
    space = layout.get("space", {})
    for node in space.get("sparseNodes", []):
        hard = node["sizeSource"] == "hardSize"
        add("S2", "SPARSE_HARD_SIZE" if hard else "SPARSE_NODE", "layout", [node["id"]],
            "Measured text uses a small fraction of the supplied frame. Review the required dimensions."
            if hard else "Review the text area, shape geometry and space needed for connections.", node)
    for group in space.get("groups", []):
        if group["sparse"]:
            add("S2", "SPARSE_GROUP", "layout", [group["id"]], "Review space reserved around group members.", group)
    page = layout.get("page", {})
    if page and not .5 <= page["aspectRatio"] <= 2:
        add("S2", "ELONGATED_PAGE", "layout", ["$.page"],
            "The longer page dimension exceeds twice the shorter one. Review reading order at delivery size.", page)
    for requirement, ids in summary.get("authoringDiff", {}).get("missingMappedObjects", {}).items():
        add("S0", "MISSING_MAPPED_OBJECT", "businessReview", ids, "A mapped requirement references absent objects.",
            {"requirement": requirement, "mapping": summary["mappingEvidence"]})
    for stage, level in (("native", "S0"), ("move", "S0"), ("editedRoundTrip", "S0"),
                         ("businessReview", "S0"), ("visualReview", "S1")):
        record = summary[stage]
        if record["status"] == "FAIL":
            data = record.get("record", {})
            add(level, stage.upper() + "_FAILED", stage,
                [data["move"]["node"]] if (data.get("move") or {}).get("node") else [],
                data.get("error") or record.get("reason") or "The submitted verification failed.",
                {k: record[k] for k in ("evidence", "reason") if k in record})
    return issues


def quality_review(receipt, summary, source):
    issues = issues_for(receipt, summary, source)
    blockers = any(i["severity"] in ("S0", "S1") for i in issues)
    failed = receipt["status"] == "FAILED" or summary["offline"]["status"] == "FAIL"
    # This is a triage outcome. Separate evidence stages retain their own status.
    disposition = "BLOCKED" if failed or blockers else "REVIEW_REQUIRED" if (
        issues or receipt["status"] == "DEGRADED" or
        any(summary[k]["status"] not in ("PASS", "NOT_APPLICABLE") for k in
            ("offline", "native", "move", "visualReview", "businessReview"))) else "REVIEWED"
    placement = receipt.get("placement") or {}
    return {"disposition": disposition, "issues": issues, "selection": placement.get("selection"),
            "candidateCount": len(placement.get("attempts", [])) or max(
                (len(d.get("layoutAttempts", [])) for d in receipt.get("diagnostics", [])), default=0),
            "leafAreaFraction": (receipt.get("deliveryLayout") or {}).get("space", {}).get("leafAreaFraction")}
