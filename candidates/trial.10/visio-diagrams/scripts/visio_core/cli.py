"""Command line for the Visio Core generation service."""
import argparse
import json
import sys

from .service import GenerationConfig, VERSION, generate


def main(argv=None):
    parser = argparse.ArgumentParser(prog="visio-core", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("input", help="UTF-8 visio.diagram/1.0 or legacy Geometry JSON")
    parser.add_argument("output", help="New .vsdx output path")
    parser.add_argument("--stencil", help="Path to the basic flowchart BASFLO_M.VSSX stencil")
    parser.add_argument("--font-family", help="Installed Windows font family (default Microsoft YaHei)")
    parser.add_argument("--color-fill", choices=("spec", "on", "off"), default="spec")
    parser.add_argument("--backend", choices=("python", "com"), default="python",
                        help="Use com only for legacy visual boundaries and Visio-controlled routing")
    parser.add_argument("--target-width-mm", type=float, help="Available figure width in millimetres")
    parser.add_argument("--target-height-mm", type=float, help="Available figure height in millimetres")
    parser.add_argument("--min-font-pt", type=float, help="Required font size at the target display scale")
    args = parser.parse_args(argv)
    report = generate(args.input, args.output, config=GenerationConfig(
        stencil=args.stencil, font_family=args.font_family, color_fill=args.color_fill, backend=args.backend,
        target_width_mm=args.target_width_mm, target_height_mm=args.target_height_mm, min_font_pt=args.min_font_pt))
    # UTF-8 even when redirected to a file by Windows PowerShell or another process.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "FAILED" else 0
