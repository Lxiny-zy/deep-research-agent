"""Report formats actually usable by the installed deployment."""

import shutil
from functools import lru_cache
from importlib import import_module


@lru_cache(maxsize=1)
def export_capabilities() -> dict[str, bool]:
    # LaTeX source is deterministic and needs no external binary.  The PDF
    # profile is reported separately because a minimal API image may not carry
    # a TeX distribution even though normal PDF/Markdown exports are usable.
    formats = {
        "md": True,
        "csv": True,
        "tex": True,
        "bib": True,
        "bundle": True,
        "paper_pdf": False,
    }
    for name, module in (("xlsx", "openpyxl"), ("pdf", "weasyprint")):
        try:
            import_module(module)
            formats[name] = True
        except (ImportError, OSError):
            formats[name] = False
    formats["paper_pdf"] = bool(shutil.which("latexmk") and shutil.which("xelatex"))
    return formats
