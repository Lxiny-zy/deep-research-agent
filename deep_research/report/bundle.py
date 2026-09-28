"""Deterministic, self-contained research delivery bundle."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterable
from typing import Any

from ..models import RunManifest, Source
from .charts import ChartAssetUnavailable, render_chart_pdf, render_chart_svg
from .document import ReportDocument
from .latex import ExportProfile, render_bibtex, render_latex
from .markdown import render_markdown
from .templates import LatexTemplateName


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_reproducibility_bundle(
    document: ReportDocument,
    *,
    run_id: str,
    manifest: RunManifest | None = None,
    sources: Iterable[Source] = (),
    profile: ExportProfile = "academic",
    template: LatexTemplateName = "ctexart",
) -> bytes:
    """Return a stable ZIP containing every non-secret research deliverable.

    ZIP timestamps and entry ordering are fixed so the same persisted run
    produces byte-stable bundles, which makes artifact checksums meaningful.
    """

    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", run_id).strip("._-") or "run"
    source_list = list(sources)
    files: dict[str, bytes] = {
        "README.txt": (
            "Deep Research reproducibility bundle\n"
            f"run_id: {safe_id}\n"
            "Contents: report.tex, references.bib, report.md, document.json, "
            "manifest.json, sources.json, optional figures/*.svg and checksums.sha256.\n"
            "The final PDF can be compiled with XeLaTeX and latexmk using report.tex.\n"
            f"LaTeX template: {template}; export profile: {profile}.\n"
            "SVG figures are deterministic projections of cited source tables.\n"
        ).encode(),
        "report.tex": render_latex(document, profile=profile, template=template).encode("utf-8"),
        "references.bib": render_bibtex(document, sources=source_list).encode("utf-8"),
        "report.md": render_markdown(document).encode("utf-8"),
        "document.json": _json(document.model_dump(mode="json")).encode("utf-8"),
        "manifest.json": _json(
            {"run_id": run_id, "manifest": manifest.model_dump(mode="json") if manifest else None}
        ).encode("utf-8"),
        "sources.json": _json([source.model_dump(mode="json") for source in source_list]).encode(
            "utf-8"
        ),
    }
    for index, chart in enumerate(document.chart_blocks(), 1):
        table = document.table(chart.source_table)
        if table is None:
            raise ValueError(f"chart {chart.id!r} references missing table {chart.source_table!r}")
        slug = re.sub(r"[^A-Za-z0-9._-]", "_", chart.id).strip("._-") or f"chart-{index}"
        asset_name = f"figures/{index:02d}-{slug}.svg"
        files[asset_name] = render_chart_svg(chart, table).encode("utf-8")
        try:
            files[f"figures/{index:02d}-{slug}.pdf"] = render_chart_pdf(chart, table)
        except ChartAssetUnavailable:
            pass
    files["checksums.sha256"] = (
        "".join(f"{hashlib.sha256(files[name]).hexdigest()}  {name}\n" for name in sorted(files))
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, files[name])
    return output.getvalue()


__all__ = ["render_reproducibility_bundle"]
