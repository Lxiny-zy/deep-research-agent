"""Render a bibliography view without altering the checked research document."""

from __future__ import annotations

from ..bibliography import project_citations
from .document import ChartBlock, ProseBlock, ReferenceEntry, ReportDocument, TableBlock


def presentation_document(document: ReportDocument) -> ReportDocument:
    catalog = document.bibliography
    if catalog is None or document._bibliography_presented:
        return document
    result = document.model_copy(deep=True)
    result._bibliography_presented = True
    by_index = {location.index: location.document for location in catalog.locations}
    result.references = [
        ReferenceEntry(index=d.index, url=d.url, reference=d.reference) for d in catalog.documents
    ]
    result.abstract = project_citations(result.abstract, catalog, links=False)
    blocks = [*result.blocks, *(block for section in result.sections for block in section.blocks)]
    seen = set()
    seen_rows: set[int] = set()
    seen_cells: set[int] = set()
    for block in blocks:
        if id(block) in seen:
            continue
        seen.add(id(block))
        if isinstance(block, ProseBlock):
            block.markdown = project_citations(block.markdown, catalog, links=False)
        elif isinstance(block, ChartBlock):
            block.caption = project_citations(block.caption, catalog, links=False)
        elif isinstance(block, TableBlock):
            block.caption = project_citations(block.caption, catalog, links=False)
            for row in block.rows:
                if id(row) not in seen_rows:
                    seen_rows.add(id(row))
                    if row.citation is not None:
                        row.citation = by_index.get(row.citation, row.citation)
                for cell in row.cells.values():
                    if id(cell) not in seen_cells:
                        seen_cells.add(id(cell))
                        cell.citations = list(
                            dict.fromkeys(by_index.get(i, i) for i in cell.citations)
                        )
    return result


def evidence_label(document: ReportDocument, citation: int) -> str:
    catalog = document.bibliography
    location = (
        next((item for item in catalog.locations if item.index == citation), None)
        if catalog
        else None
    )
    if location is None:
        return f"来源 [{citation}]"
    return f"文献 [{location.document}] · {location.label or f'引用定位 {citation}'}"
