"""Verify a generated drawing and its separately identified styled derivative."""
import argparse
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from uuid import UUID

from visio_core.move_evidence import Drawing, native_move, require
from visio_core.package_check import NS, text_value
from visio_core.review import artifact_path, check_png, read, reference, summarize, verify, visio_page


def contents(path):
    drawing = Drawing(path)
    shapes = visio_page(path).findall("v:Shapes/v:Shape", NS)
    texts = {shape.get("NameU"): text_value(shape) for shape in shapes}
    arrows = {name: tuple(float(cells[key].get("V")) if key in cells else None
                          for key in ("BeginArrow", "EndArrow"))
              for name, cells in drawing.shapes.items() if name and re.fullmatch(r"edge_[1-9][0-9]*", name)}
    return {"shapeText": texts, "glue": drawing.links, "arrows": arrows, "containers": drawing.members}


def compare_drawing(expected, path):
    for field, actual in contents(path).items():
        wanted = expected[field]
        if actual != wanted:
            ids = [str(key) for key in wanted.keys() | actual.keys() if actual.get(key) != wanted.get(key)]
            raise ValueError(f"{path}: changed {field} at {', '.join(sorted(ids))}")


def native_check(path, styled, scene, expected, base):
    data = read(path)
    verify(styled, data.get("sourceSha256"))
    result = {"status": data.get("status"), "source": reference(styled, base), "receipt": reference(path, base)}
    if result["status"] == "FAIL":
        result.update(stage=data.get("stage"), error=data.get("error"))
        return result
    require(result["status"] == "PASS" and data.get("stage") == "finished",
            "A completed native receipt is required")
    for key in ("opened", "afterSave", "reopened"):
        stage = data.get(key) or {}
        require(stage.get("shapes") == len(expected["shapeText"]) and
                stage.get("connects") == len(expected["glue"]), f"Incomplete native {key} object/endpoint counts")
    for key in ("saved", "preview"):
        target = artifact_path(path, data.get(key))
        verify(target, data.get(key + "Sha256"))
        if key == "saved":
            require(target.resolve() != styled.resolve(), "Native save needs a distinct file")
            compare_drawing(expected, target)
        else:
            check_png(target)
        result[key] = reference(target, base)
    result["move"] = native_move(data["move"], styled, scene) if data.get("move") else {"status": "NOT_RUN"}
    return result


def reviewer_record(path, kind, native, style, styled, base):
    if path is None:
        return {"status": "NOT_RUN"}
    data = read(path)
    verify(styled, data.get("sourceSha256"))
    require(data.get("operationId") == style["operationId"], f"{kind}: different style operation")
    require(data.get("reviewerType") in ("agent", "human"), f"{kind}: identify the reviewer type")
    require(data.get("status") in ("PASS", "FAIL", "BLOCKED"), f"{kind}: invalid review outcome")
    if kind == "visual":
        require(native.get("preview") and native["preview"]["sha256"] == str(data.get("previewSha256")).lower(),
                "Visual review must identify the verified native preview")
        require(bool(data.get("targetSize")) and bool(data.get("findings")), "Missing visual findings or display size")
    else:
        require(bool(data.get("requirements")), "Missing business requirement findings")
    return {"status": data["status"], "receipt": reference(path, base)}


def build(args):
    base = args.output.resolve().parent
    source = summarize(SimpleNamespace(input=args.input, report=args.report, figure_id=args.style.stem,
                                       native=None, edits=None, visual=None, business=None,
                                       before=None, mapping=None), base)
    require(source["generation"]["status"] == source["offline"]["status"] == "PASS",
            "The base requires a completed generation and both offline checks")
    style = read(args.style)
    require(style.get("schema") == "visio.style/1" and style.get("status") == "STYLED", "Invalid style receipt")
    UUID(str(style.get("operationId")))
    artifacts = source["artifacts"]
    generated = base / artifacts["vsdx"]["path"]
    planned = base / artifacts["scene"]["path"]
    styled = artifact_path(args.style, style.get("output"))
    verify(generated, style.get("sourceSha256"))
    verify(planned, style.get("plannedSha256"))
    verify(styled, style.get("outputSha256"))
    require(generated.resolve() != styled.resolve(), "Style output must be a separate file")
    require(style["sourceSha256"].lower() != style["outputSha256"].lower(),
            "Style output is byte-identical to its generation source")
    scene = read(planned)
    expected = contents(generated)
    edge_names = {f"edge_{i}" for i in range(1, len(scene["edges"]) + 1)}
    require(expected["arrows"].keys() == edge_names, "Native edge names disagree with the planned edges")
    require(expected["glue"].keys() == {(edge, end) for edge in edge_names for end in ("BeginX", "EndX")},
            "Missing or additional glued endpoints")
    compare_drawing(expected, styled)
    native = native_check(args.native, styled, scene, expected, base) if args.native else {"status": "NOT_RUN"}
    return {
        "schema": "visio.style-review/1",
        "base": {"runId": source["runId"], "input": source["input"], "artifacts": artifacts,
                 "generation": source["generation"], "offline": source["offline"], "quality": source["quality"]},
        "style": {"operationId": style["operationId"], "receipt": reference(args.style, base),
                  "sourceSha256": style["sourceSha256"], "styled": reference(styled, base),
                  "preserveColors": style["preserveColors"], "noteNodes": style["noteNodes"], "endNodes": style["endNodes"]},
        "preservation": {"status": "PASS", "shapeTexts": len(expected["shapeText"]), "edges": len(expected["arrows"]),
                         "gluedEndpoints": len(expected["glue"]), "containers": len(expected["containers"])},
        "native": native, "finalVsdx": reference(styled, base),
        "visualReview": reviewer_record(args.visual, "visual", native, style, styled, base),
        "businessReview": reviewer_record(args.business, "business", native, style, styled, base),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "report", "style", "output", "native", "visual", "business"):
        parser.add_argument("--" + name, type=Path, required=name in ("input", "report", "style", "output"))
    args = parser.parse_args()
    try:
        result = build(args)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"style review: {error}\n")
    outcomes = [result[key]["status"] for key in ("native", "visualReview", "businessReview")]
    outcomes.append(result["native"].get("move", {}).get("status"))
    return int(any(status in ("FAIL", "BLOCKED") for status in outcomes))


if __name__ == "__main__":
    sys.exit(main())
