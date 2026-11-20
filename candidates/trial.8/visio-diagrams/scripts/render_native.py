"""Compatibility entry point for editable VSDX generation."""
from visio_core import GenerationConfig, generate
from visio_core.cli import main
from visio_core.package_check import check_package


def render(spec, output, color_fill="spec", stencil=None, *, font_family=None):
    result = generate(spec, output, config=GenerationConfig(stencil=stencil, color_fill=color_fill, font_family=font_family))
    if result["status"] == "FAILED":
        diagnostic = result["diagnostics"][-1]
        if diagnostic["code"] == "OUTPUT_EXISTS":
            raise FileExistsError(diagnostic["message"])
        raise ValueError(diagnostic["message"])
    return result


if __name__ == "__main__":
    raise SystemExit(main())
