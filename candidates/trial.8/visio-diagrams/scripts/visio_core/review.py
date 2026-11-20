"""Compare diagram revisions or summarize identity-checked evidence from one run."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import sys
import zipfile
import zlib
from xml.etree import ElementTree as ET

from .business_diff import compare
from .contracts import load, normalize
from .delivery_layout import measure_workflow_alignment
from .move_evidence import Drawing, edited_move, native_move
from .package_check import checked_xml, NS, text_value, value
from .quality import quality_review
from .service import VERSION, json_bytes


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except ValueError as error:
        raise ValueError(f"Cannot parse JSON evidence {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def verify(path, digest):
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f"Missing or invalid SHA-256 for {path}")
    if fingerprint(path) != digest.lower():
        raise ValueError(f"SHA-256 mismatch for {path}")


def reference(path, base):
    try:
        name = os.path.relpath(Path(path).resolve(), base)
    except ValueError:  # separate Windows volumes
        name = str(Path(path).resolve())
    return {"path": Path(name).as_posix(), "sha256": fingerprint(path)}


def artifact_path(receipt, raw, *, legacy_absolute=True):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Missing artifact path in {receipt}")
    path = Path(raw.replace("\\", "/"))
    if path.is_absolute() or PureWindowsPath(raw).is_absolute():
        # Older generation/native receipts use absolute staging paths. Their
        # published copies travel beside the receipt and are verified by hash.
        adjacent = receipt.parent / PureWindowsPath(raw).name
        return adjacent if legacy_absolute and adjacent.is_file() else path
    return receipt.parent / path


def check_png(path):
    content = path.read_bytes()
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Invalid edited PNG signature: {path}")
    offset = 8
    has_data = False
    decoder = zlib.decompressobj()
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset:offset + 4], "big")
        kind = content[offset + 4:offset + 8]
        end = offset + 8 + length
        crc = int.from_bytes(content[end:end + 4], "big")
        if end + 4 > len(content) or zlib.crc32(content[offset + 4:end]) != crc:
            raise ValueError(f"Corrupt edited PNG chunk {kind!r}: {path}")
        if offset == 8 and (kind != b"IHDR" or length != 13):
            raise ValueError(f"Missing edited PNG header: {path}")
        if kind == b"IDAT":
            payload = content[offset + 8:end]
            try:
                # Discard decoded scanlines in bounded pieces. The zlib checksum
                # catches damaged image data even if a chunk has a valid CRC.
                while payload:
                    has_data |= bool(decoder.decompress(payload, 65536))
                    payload = decoder.unconsumed_tail
                    if decoder.unused_data:
                        raise ValueError(f"Trailing compressed image data in {path}")
            except zlib.error as error:
                raise ValueError(f"Corrupt edited PNG image data in {path}: {error}") from error
        offset = end + 4
        if kind == b"IEND":
            if length == 0 and has_data and decoder.eof and offset == len(content):
                return
            break
    raise ValueError(f"Incomplete edited PNG: {path}")


def visio_page(path):
    required = {"[Content_Types].xml", "_rels/.rels", "visio/document.xml",
                "visio/_rels/document.xml.rels", "visio/pages/pages.xml",
                "visio/pages/_rels/pages.xml.rels", "visio/pages/page1.xml"}
    try:
        parts = checked_xml(path)
        missing = required.difference(parts)
        if missing:
            raise ValueError(f"missing package parts: {', '.join(sorted(missing))}")
        page = parts["visio/pages/page1.xml"]
        if page.tag != f"{{{NS['v']}}}PageContents":
            raise ValueError("invalid Visio page root element")
        return page
    except (zipfile.BadZipFile, ET.ParseError, ValueError) as error:
        raise ValueError(f"Invalid VSDX package {path}: {error}") from error


def shape_text(page, path, node):
    matches = [shape for shape in page.findall("v:Shapes/v:Shape", NS)
               if shape.get("NameU") == f"node_{node}"]
    if len(matches) != 1:
        raise ValueError(f"Edited text node {node!r} missing or ambiguous in {path}")
    return text_value(matches[0])


def check_edit(path, data, source_path):
    if data.get("reopened") is not True or data.get("error"):
        raise ValueError(f"{path}: edited copy requires a successful reopen")
    if "source" in data:
        verify(artifact_path(path, data["source"], legacy_absolute=False), data["sourceSha256"])
    edited = {}
    for field in ("saved", "preview"):
        if not data.get(field):
            raise ValueError(f"{path}: missing edited {field} artifact path")
        target = artifact_path(path, data[field], legacy_absolute=False)
        verify(target, data.get(field + "Sha256"))
        edited[field] = target
    if data["savedSha256"].lower() == data["sourceSha256"].lower():
        raise ValueError(f"{path}: edited VSDX is identical to the source")
    page = visio_page(edited["saved"])
    check_png(edited["preview"])
    actions = data.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError(f"{path}: missing executed edit actions")
    for action in actions:
        if not isinstance(action, dict) or action.get("status", "PASS") != "PASS":
            raise ValueError(f"{path}: invalid edit action record or failed result")
        kind = action.get("kind")
        if kind in ("move-node", "move-native-container"):
            continue  # Verified with geometry and endpoint checks below.
        if kind != "edit-long-text":
            raise ValueError(f"{path}: unsupported edit action {kind!r}")
        node, before, after = (action.get(k) for k in ("node", "before", "after"))
        if (not isinstance(node, str) or not node or not isinstance(before, str)
                or not isinstance(after, str) or before == after):
            raise ValueError(f"{path}: edit-long-text requires a node and changed text")
        if data.get("reopenedText") != after or shape_text(page, edited["saved"], node) != after:
            raise ValueError(f"{path}: reopened text differs from edit result for {node}")
        if shape_text(visio_page(source_path), source_path, node) != before:
            raise ValueError(f"{path}: source text differs from recorded original for {node}")
    return edited["saved"]


def review_workflow_alignment(source, vsdx, planned):
    """Read published ShapeSheet frames, using the saved ranks only for topology."""
    placement = planned.get("_intentPlacement", {})
    if not placement.get("blockRanks"):
        return {"status": "NOT_RUN", "reason": "Missing intent ranks in the hash-verified scene."}
    original, _, _ = normalize(source)
    drawing = Drawing(vsdx)
    nodes = []
    for node in original["nodes"]:
        name = "node_" + node["id"]
        x, y = drawing.position(name, "Pin")
        data = drawing.shapes[name]
        w, h = value(data, "Width"), value(data, "Height")
        nodes.append({"id": node["id"], "x": x - w / 2, "y": planned["page"]["height"] - y - h / 2,
                      "w": w, "h": h})
    return {**measure_workflow_alignment({"nodes": nodes}, original, placement),
            "geometrySource": "publishedVsdx"}


def summarize(args, base):
    receipt = read(args.report)
    identity = receipt.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("runId"), str) or not identity["runId"].strip():
        raise ValueError(f"Missing generation runId in {args.report}")
    if identity["inputEncoding"] == "canonical-json":
        data = json.dumps(load(args.input), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
        if hashlib.sha256(data).hexdigest() != identity["inputSha256"]:
            raise ValueError("Canonical input differs from generation input")
    else:
        verify(args.input, identity["inputSha256"])
    report_ref = reference(args.report, base)
    hashes = receipt.get("hashes", {})
    artifacts = {}
    for name, digest in hashes.items():
        path = artifact_path(args.report, receipt["artifacts"][name])
        verify(path, digest)
        artifacts[name] = reference(path, base)
    source_path = base / artifacts["vsdx"]["path"] if "vsdx" in artifacts else None
    generated = receipt["status"] in ("GENERATED", "DEGRADED") and "vsdx" in artifacts
    checks = dict(receipt.get("checks", {}))
    offline = [checks.get(k, {}).get("status") for k in ("scene", "vsdx")]
    status = "PASS" if generated and offline == ["PASS", "PASS"] else (
        "FAIL" if "FAIL" in offline else "NOT_RUN")
    if generated:
        source = load(args.input)
        if source.get("schema") and source.get("layout", {}).get("mode") == "intent":
            alignment = (review_workflow_alignment(source, source_path, read(base / artifacts["scene"]["path"]))
                         if "scene" in artifacts else
                         {"status": "NOT_RUN", "reason": "Missing hash-verified planned scene."})
            checks["workflowAlignment"] = alignment
            if alignment["status"] == "FAIL" or (alignment["status"] == "NOT_RUN" and status == "PASS"):
                status = alignment["status"]
    summary = {
        "figureId": args.figure_id, "runId": identity["runId"], "version": receipt["version"],
        "reviewVersion": VERSION, "environment": identity["environment"], "input": reference(args.input, base),
        "artifacts": artifacts,
        "generation": {"status": "PASS" if generated else "FAIL", "result": receipt["status"], "evidence": report_ref},
        "offline": {"status": status, "checks": checks, "evidence": report_ref},
        "native": {"status": "NOT_RUN"}, "move": {"status": "NOT_RUN"},
        "editedRoundTrip": {"status": "NOT_RUN"}, "visualReview": {"status": "NOT_RUN"},
        "businessReview": {"status": "NOT_RUN"}, "layout": receipt.get("deliveryLayout"),
        "selection": (receipt.get("placement") or {}).get("selection"),
        "diagnostics": receipt.get("diagnostics", []),
    }

    def move_scene():
        if "scene" not in artifacts:
            raise ValueError(f"{args.report}: movement requires a hash-verified planned scene")
        return read(base / artifacts["scene"]["path"])

    for name, path in (("native", args.native), ("editedRoundTrip", args.edits),
                       ("visualReview", args.visual), ("businessReview", args.business)):
        if path is None:
            continue
        data = read(path)
        if data.get("runId") != identity["runId"]:
            raise ValueError(f"{name} evidence is for a different drawing or run: runId mismatch in {path}")
        if (not isinstance(data.get("sourceSha256"), str) or not hashes.get("vsdx")
                or data["sourceSha256"].lower() != hashes["vsdx"].lower()):
            raise ValueError(f"{name} evidence is for a different drawing or run: sourceSha256 mismatch in {path}")
        state = data.get("status")
        if state not in ("PASS", "FAIL", "BLOCKED", "NOT_RUN", "NOT_APPLICABLE"):
            raise ValueError(f"Invalid {name} evidence status {state!r} in {path}")
        if name == "native":
            complete = all(data.get(k) for k in ("opened", "afterSave", "reopened", "savedSha256"))
            if state == "PASS" and not complete:
                raise ValueError("Native PASS requires open, save and reopen evidence")
            if data.get("saved"):
                verify(artifact_path(path, data["saved"]), data.get("savedSha256"))
            if data.get("preview"):
                verify(artifact_path(path, data["preview"]), data.get("previewSha256"))
            move = data.get("move") or {}
            if move or (state == "FAIL" and data.get("stage") in ("move", "glue-after-move")):
                result = {"status": "FAIL", "reason": f"Native receipt status is {state}"}
                if state == "PASS":
                    result = native_move(move, source_path, move_scene())
                summary["move"] = {**result, "evidence": reference(path, base)}
        elif state == "PASS":
            if name == "editedRoundTrip":
                saved_path = check_edit(path, data, source_path)
                moved = [a for a in data["actions"] if a.get("kind") in ("move-node", "move-native-container")]
                if moved:
                    result = edited_move(moved, data.get("endpointChecks"), source_path, saved_path, move_scene())
                    result["evidence"] = reference(path, base)
                    previous = summary["move"]
                    if previous["status"] == "FAIL":
                        previous["editedMove"] = result
                    elif previous["status"] == "PASS" and result["status"] == "NOT_APPLICABLE":
                        previous["editedMove"] = result
                    else:
                        if previous["status"] != "NOT_RUN":
                            result["nativeMove"] = previous
                        summary["move"] = result
                elif data.get("endpointChecks"):
                    raise ValueError(f"{path}: endpoint checks without a corresponding move action")
            if name in ("visualReview", "businessReview") and data.get("reviewerType") not in ("agent", "human"):
                raise ValueError("Name the human or agent reviewer type")
            if name == "visualReview":
                if not all(data.get(k) for k in ("preview", "previewSha256", "previewSource", "targetSize", "findings")):
                    raise ValueError("Visual review requires the reviewed preview, target size and findings")
                verify(artifact_path(path, data["preview"]), data["previewSha256"])
            if name == "businessReview" and not data.get("requirements"):
                raise ValueError("Business review requires findings against requirement IDs")
        elif name == "editedRoundTrip":
            # A successful native move on another copy cannot establish that
            # the attempted move in this edited document passed.
            actions = data.get("actions")
            attempted = [a for a in actions if isinstance(a, dict) and a.get("kind") in
                         ("move-node", "move-native-container")] if isinstance(actions, list) else []
            if attempted or data.get("endpointChecks"):
                summary["move"] = {"status": state, "actions": attempted, "evidence": reference(path, base)}
        summary[name] = {"status": state, "evidence": reference(path, base), "record": data}
    if args.mapping or args.before:
        if not args.before or not args.mapping:
            raise ValueError("Provide both the frozen input and requirement mapping")
        comparison = compare(load(args.before), load(args.input), mapping=read(args.mapping),
                             previous_mapping=read(args.previous_mapping) if args.previous_mapping else None)
        summary["authoringDiff"] = comparison
        summary["requirements"] = read(args.mapping)
        summary["mappingEvidence"] = reference(args.mapping, base)
        summary["frozenInput"] = reference(args.before, base)
    # The published drawing is the original VSDX. Native round trips and edit
    # probes are separate verified artifacts, even when their bytes happen to match.
    chain = {"runId": identity["runId"], "input": summary["input"],
             "generationInputSha256": identity["inputSha256"],
             "generatedVsdx": artifacts.get("vsdx"), "finalVsdx": artifacts.get("vsdx"),
             "preview": artifacts.get("preview")}
    for key, path in (("native", args.native), ("editedRoundTrip", args.edits)):
        outcome = summary[key]
        if outcome["status"] != "PASS":
            continue
        rec = outcome["record"]
        chain[key] = {"sourceSha256": rec["sourceSha256"], "receipt": outcome["evidence"]}
        for field in ("saved", "preview"):
            if rec.get(field):
                resolved = artifact_path(path, rec[field], legacy_absolute=key == "native")
                chain[key][field] = reference(resolved, base)
    summary["artifactChain"] = chain
    # Malformed input can still have a verified byte hash and a failure report.
    try:
        description = load(args.input) if receipt.get("diagnostics") else {}
    except (ValueError, OSError):
        description = {}
    summary["quality"] = quality_review(receipt, summary, description if isinstance(description, dict) else {})
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    diff = commands.add_parser("compare", help="Compare logical IDs and layout independently")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    review = commands.add_parser("summarize", help="Combine evidence for a generated drawing")
    review.add_argument("--figure-id", required=True)
    for key in ("report", "input", "before", "native", "edits", "visual", "business"):
        review.add_argument("--" + key, required=key in ("report", "input"), type=Path)
    for command in (diff, review):
        command.add_argument("--mapping", type=Path)
        command.add_argument("--previous-mapping", type=Path)
        command.add_argument("--output", type=Path, help="Write a new UTF-8 JSON file")
    args = parser.parse_args(argv)
    try:
        if args.previous_mapping and not args.mapping:
            raise ValueError("--previous-mapping requires --mapping")
        if args.command == "compare":
            data = compare(load(args.before), load(args.after), mapping=read(args.mapping) if args.mapping else None,
                           previous_mapping=read(args.previous_mapping) if args.previous_mapping else None)
        else:
            data = summarize(args, args.output.resolve().parent if args.output else Path.cwd())
        if args.output:
            with args.output.open("xb") as output:
                output.write(json_bytes(data))
        else:
            sys.stdout.buffer.write(json_bytes(data))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, str(error) + "\n")
    return int(args.command == "compare" and data["comparison"] != "UNCHANGED")


if __name__ == "__main__":
    raise SystemExit(main())
