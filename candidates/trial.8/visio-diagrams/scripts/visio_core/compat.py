"""Explicit legacy COM renderer for visual-only boundaries and dynamic routing."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .contracts import reject
from .delivery_layout import diagnose
from .edge_labels import measure_labels, place_labels
from .resources import discover_stencil, resolve_stencil
from .scene import route_legacy
from .service import json_bytes, publish, set_fill, sha
from .text_layout import fit_text, wrap_text


def prepare_com_text(source, font):
    """Measure authored frames. Visio lays out text on its dynamic connectors."""
    page = source["page"]
    blockers = list(source["nodes"])
    for node in blockers:
        frame = dict(node, h=node["h"] - (.055 if node["kind"] == "container" else 0))
        fit_text(font, node, frame, f"node {node['id']}.text")
    if page.get("title"):
        title = {"text": page["title"], "fontSize": page.get("titleFontSize", 16), "bold": True}
        frame = {"x": .3, "y": .1, "w": page["width"] - .6, "h": .45}
        fit_text(font, title, frame, "$.page.title")
        if "sourceText" in title:
            page["sourceTitle"] = title["sourceText"]
        page["title"] = title["text"]
        blockers.append(frame)
    if all(edge.get("routePoints") for edge in source["edges"]) and source.get("localLayout"):
        measured = measure_labels(source["edges"], font)
        source["_labelOwnership"] = place_labels(source, font, blockers, measured, compatible=False)
        return
    for edge in source["edges"]:
        if not edge.get("text"):
            continue
        path = f"edge {edge['id']}.text"
        if edge.get("label"):
            fit_text(font, edge, edge["label"], path, base_size=10)
        else:
            wrap_text(font, edge["text"], edge.get("fontSize", 10), edge.get("bold", False), float("inf"), path)


def generate_com(source, config, font, targets, result):
    result["_stage"] = "resources"
    explicit = config.stencil or source["page"].get("stencilPath") or os.environ.get("VISIO_CORE_STENCIL")
    stencil = resolve_stencil(explicit) if explicit else discover_stencil()
    result["resources"]["stencil"] = str(stencil) if stencil else None
    result["_stage"] = "routing"
    set_fill(source, config.color_fill)
    authored = source
    source, router = route_legacy(source)
    result["_stage"] = "text"
    source.update(fontFamily=font.family, fontAliases=font.aliases)
    prepare_com_text(source, font)
    result["deliveryLayout"] = diagnose(source, authored, config, {}, font)
    result["deliveryLayout"]["scope"] = "Authored geometry and measured text before Visio dynamic routing."
    result["strategy"]["routing"] = "visio-dynamic" if router == "legacy-ports" else router + "+visio-dynamic"
    result["_stage"] = "legacy-com"
    output = targets["vsdx"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visio-com-", dir=output.parent) as directory:
        staged = {key: Path(directory) / path.name for key, path in targets.items()}
        staged["scene"].write_bytes(json_bytes(source))
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                   str(Path(__file__).with_name("compat_com.ps1")), "-SpecPath", str(staged["scene"]),
                   "-OutputPath", str(staged["vsdx"])]
        if stencil:
            command.extend(["-StencilPath", str(stencil)])
        try:
            run = subprocess.run(command, capture_output=True, timeout=120, check=False)
        except subprocess.TimeoutExpired:
            reject("LEGACY_COM_TIMEOUT", "Visio COM did not finish before the 120 second limit", "$.config.backend")
        if run.returncode != 0:
            message = (run.stderr or run.stdout).decode("oem", errors="replace").strip()
            reject("LEGACY_COM_FAILED", message or f"Visio COM process exited {run.returncode}", "$.config.backend")
        report = json.loads(staged["report"].read_text(encoding="utf-8-sig"))
        if report["status"] not in ("PASS", "DEGRADED") or not all(path.is_file() for path in staged.values()):
            reject("LEGACY_COM_INCOMPLETE", "Visio COM did not produce a successful drawing and preview", "$.config.backend")
        status = "DEGRADED" if report["status"] == "DEGRADED" else "GENERATED"
        artifacts = {key: str(path) for key, path in targets.items()}
        result.update(legacyCom=report, preview={"kind": "VISIO_PNG"}, png=artifacts["preview"],
                      hashes={key: sha(staged[key]) for key in ("vsdx", "scene", "preview")})
        result["checks"]["legacyCom"] = {"status": report["nativeVerification"], "scope": report["scope"]}
        result["_stage"] = "publish"
        staged["report"].write_bytes(json_bytes({key: value for key, value in result.items() if key != "_stage"}
                                                 | {"status": status, "artifacts": artifacts}))
        publish((staged[key], path) for key, path in targets.items())
        result.update(status=status, artifacts=artifacts)
