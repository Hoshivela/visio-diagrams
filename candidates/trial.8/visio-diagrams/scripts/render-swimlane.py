"""Build, reopen and verify horizontal or vertical native swimlanes in a single run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import visio_core
from visio_core import GenerationConfig, generate
from visio_core.service import json_bytes, publish, sha


def elapsed(start):
    return round(perf_counter() - start, 4)


def render(args):
    from swimlane_shapes import DEFAULT_HEADER_FILL, DEFAULT_LANE_FILL, write_swimlanes

    started = perf_counter()
    if os.name != "nt":
        raise OSError("The native swimlane workflow requires desktop Visio on Windows.")
    output = args.output.parent.resolve() / args.output.name
    if output.suffix.lower() != ".vsdx":
        raise ValueError("Output must end in .vsdx")
    preview = output.with_suffix(".png")
    report = output.with_suffix(".swimlane.json")
    for path in (output, preview, report):
        if os.path.lexists(path):
            raise FileExistsError(f"Output already exists: {path}")
    lane_bytes = args.lanes.read_bytes()
    lanes = json.loads(lane_bytes.decode("utf-8-sig"))
    validator = Path(visio_core.__file__).resolve().parent.parent / "validate-vsdx.ps1"
    config = GenerationConfig(font_family=args.font_family, stencil=args.stencil,
                              target_width_mm=args.target_width_mm, target_height_mm=args.target_height_mm,
                              min_font_pt=args.min_font_pt)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Work in an exclusive directory beside the targets so final renames stay on one volume.
    with TemporaryDirectory(prefix="swim-", dir=output.parent) as workspace:
        work = Path(workspace)
        base = work / "base.vsdx"
        ts = perf_counter()
        core = generate(args.input, base, config=config)
        core_seconds = elapsed(ts)
        if core["status"] != "GENERATED":
            raise ValueError(json.dumps(core["diagnostics"], ensure_ascii=False))
        if core["strategy"]["layout"] != "positioned":
            raise ValueError("Swimlanes require positioned activity frames.")
        ts = perf_counter()
        staged = work / "lane-shapes.vsdx"
        write_swimlanes(base, lanes, staged, stencil=args.swimlane_stencil,
                        font_family=core["resources"]["fontFamily"])
        shape_seconds = elapsed(ts)
        # Record defaults explicitly for the desktop renderer's pixel comparison.
        for lane in lanes["lanes"]:
            lane.setdefault("fill", DEFAULT_LANE_FILL)
            lane.setdefault("headerFill", DEFAULT_HEADER_FILL)
        spec = work / "lanes.json"
        spec.write_bytes(json_bytes(lanes))
        drawing = work / "registered.vsdx"
        png = work / "preview.png"
        native = work / "native.json"
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
               Path(__file__).with_name("finalize-swimlane.ps1"), "-Source", staged, "-Drawing", drawing,
               "-Planned", base.with_suffix(".planned.json"), "-Swimlanes", spec, "-Preview", png,
               "-Report", native, "-Validator", validator]
        ts = perf_counter()
        process = subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        desktop_seconds = elapsed(ts)
        if process.returncode:
            message = (process.stderr or process.stdout).decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or f"Desktop Visio process exited with code {process.returncode}")
        receipt = json.loads(native.read_text(encoding="utf-8-sig"))
        if (receipt["status"] != "PASS" or receipt["previewVisibility"]["status"] != "PASS"
                or receipt["sourceSha256"].lower() != sha(drawing) or receipt["previewSha256"].lower() != sha(png)):
            raise ValueError("Published drawing or preview does not match its native verification receipt.")
        receipt["source"], receipt["preview"] = output.name, preview.name
        timings = {"coreGeneration": core_seconds, "nativeShapes": shape_seconds,
                   "desktopProcess": desktop_seconds, "desktopPhases": receipt.pop("phaseSeconds"),
                   "toVerifiedArtifacts": elapsed(started)}
        result = {"status": "GENERATED", "version": core["version"],
                  "orientation": lanes.get("orientation", "horizontal"), "artifacts": {
                     "vsdx": output.name, "preview": preview.name, "report": report.name},
                  "inputSha256": core["identity"]["inputSha256"], "laneSpecSha256": hashlib.sha256(lane_bytes).hexdigest(),
                  "coreChecks": core["checks"], "coreDiagnostics": core["diagnostics"],
                  "display": {"target": core["deliveryLayout"]["target"], "minimumEffectivePt": core["deliveryLayout"]["minimumEffectivePt"]},
                  "native": receipt, "timingsSeconds": timings}
        record = work / "receipt.json"
        record.write_bytes(json_bytes(result))
        publish([(drawing, output), (png, preview), (record, report)])
    return {"status": "GENERATED", "orientation": result["orientation"],
            "output": str(output), "preview": str(preview), "report": str(report),
            "elapsedSeconds": elapsed(started), "timingsSeconds": timings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Positioned Visio Core JSON")
    parser.add_argument("lanes", type=Path, help="Lane bounds, member IDs and orientation (horizontal or vertical)")
    parser.add_argument("output", type=Path, help="New .vsdx output")
    parser.add_argument("--font-family")
    parser.add_argument("--target-width-mm", type=float)
    parser.add_argument("--target-height-mm", type=float)
    parser.add_argument("--min-font-pt", type=float)
    parser.add_argument("--stencil", type=Path, help="Basic flowchart stencil")
    parser.add_argument("--swimlane-stencil", type=Path, help="Visio cross-functional flowchart stencil")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        result = render(args)
    except (OSError, ValueError, RuntimeError, BadZipFile, ParseError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
