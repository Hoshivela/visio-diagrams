"""Locate Windows stencil resources without using checkout paths."""
from itertools import chain
from pathlib import Path
import os

from .errors import GenerationError

DEFAULT_FONT = "Microsoft YaHei"


def stencil_candidates():
    roots = []
    if os.name == "nt":
        import winreg

        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                key = r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | view) as config:
                    roots.append(Path(winreg.QueryValueEx(config, "InstallationPath")[0]))
            except OSError:
                continue
    roots.extend(Path(os.environ[name]) / "Microsoft Office"
                 for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432") if os.environ.get(name))
    for root in dict.fromkeys(roots):
        for parent in (root / "root", root):
            for office in sorted(parent.glob("Office*"), reverse=True):
                content = office / "Visio Content"
                yield from chain((content / "2052" / "BASFLO_M.VSSX",),
                                 sorted(content.glob("*/BASFLO_M.VSSX")))


def discover_stencil():
    return next((path for path in stencil_candidates() if path.is_file()), None)


def resolve_stencil(explicit=None):
    configured = explicit or os.environ.get("VISIO_CORE_STENCIL")
    path = Path(configured).expanduser() if configured else discover_stencil()
    if path is None or not path.is_file():
        raise GenerationError("STENCIL_MISSING",
                              "Basic flowchart BASFLO_M.VSSX is required. Supply --stencil or set VISIO_CORE_STENCIL.",
                              path or "$.resources.stencil")
    return path.resolve()
