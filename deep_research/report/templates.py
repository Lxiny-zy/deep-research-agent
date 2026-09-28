"""Whitelisted LaTeX templates.

The class and package list are application code, never request or model input.
This keeps a paper export reproducible and prevents arbitrary LaTeX packages or
compiler flags from becoming an execution surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LatexTemplateName = Literal["ctexart", "ctexrep", "ieeetran", "acmart"]


@dataclass(frozen=True)
class LatexTemplate:
    name: LatexTemplateName
    documentclass: str
    packages: tuple[str, ...]
    uses_geometry: bool = True
    preamble: tuple[str, ...] = ()


_TEMPLATES: dict[LatexTemplateName, LatexTemplate] = {
    "ctexart": LatexTemplate(
        name="ctexart",
        documentclass=r"\documentclass[UTF8,a4paper,11pt]{ctexart}",
        packages=("booktabs", "longtable", "array", "enumitem", "hyperref", "xurl", "fancyhdr"),
    ),
    "ctexrep": LatexTemplate(
        name="ctexrep",
        documentclass=r"\documentclass[UTF8,a4paper,11pt]{ctexrep}",
        packages=("booktabs", "longtable", "array", "enumitem", "hyperref", "xurl", "fancyhdr"),
    ),
    "ieeetran": LatexTemplate(
        name="ieeetran",
        documentclass=r"\documentclass[journal,onecolumn,a4paper,10pt]{IEEEtran}",
        packages=("booktabs", "longtable", "array", "enumitem", "hyperref", "xurl"),
        uses_geometry=False,
        preamble=(r"\usepackage[UTF8]{ctex}",),
    ),
    "acmart": LatexTemplate(
        name="acmart",
        documentclass=r"\documentclass[manuscript,screen,nonacm]{acmart}",
        packages=("booktabs", "longtable", "array", "enumitem", "hyperref", "xurl"),
        uses_geometry=False,
        preamble=(r"\usepackage[UTF8]{ctex}",),
    ),
}


def get_latex_template(name: str) -> LatexTemplate:
    """Resolve a template from the fixed application registry."""

    try:
        return _TEMPLATES[name]  # type: ignore[index]
    except KeyError as exc:
        allowed = ", ".join(_TEMPLATES)
        raise ValueError(f"unknown LaTeX template {name!r}; choose one of: {allowed}") from exc


def latex_template_names() -> tuple[LatexTemplateName, ...]:
    return tuple(_TEMPLATES)


__all__ = ["LatexTemplate", "LatexTemplateName", "get_latex_template", "latex_template_names"]
