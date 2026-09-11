"""Report formats actually usable by the installed deployment."""

from functools import lru_cache
from importlib import import_module


@lru_cache(maxsize=1)
def export_capabilities() -> dict[str, bool]:
    formats = {"md": True, "csv": True}
    for name, module in (("xlsx", "openpyxl"), ("pdf", "weasyprint")):
        try:
            import_module(module)
            formats[name] = True
        except (ImportError, OSError):
            formats[name] = False
    return formats
