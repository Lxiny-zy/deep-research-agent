import re

import pytest

from deep_research.bibliography import CitationOccurrence, build_bibliography
from deep_research.workbench.delivery.html import render_html


def panel(html, target):
    return re.search(
        rf'<aside class="citation-location" id="cite-{target}">(.*?)</aside>', html, re.S
    )[1]


@pytest.mark.parametrize("status", ["unavailable", "invalid", "bound"])
@pytest.mark.parametrize("scope", [None, "source_location", "reviewed_unit"])
def test_missing_valid_binding_does_not_present_source_quotes_as_claim_support(status, scope):
    catalog = build_bibliography("Current claim [1].", ["https://example.org/a"], [])
    catalog.binding_status = status
    target = "1"
    if scope:
        occurrence = CitationOccurrence(
            id="a" * 24,
            run=0,
            document=1,
            locations=[1],
            scope=scope,
            evidence_ids=["missing"] if status == "bound" else ["other"],
        )
        catalog.occurrences = [occurrence]
        target = f"o-{occurrence.id}"
        catalog.body = f"Current claim [[1]](#cite-{target})."
    html = render_html(
        catalog.body,
        title="Report",
        bibliography=catalog,
        evidence=[
            {"id": "other", "citation": 1, "statement": "Other claim", "quote": "OTHER QUOTE"},
        ],
    )
    claim = panel(html, target)
    assert "未绑定依据" in claim
    assert "OTHER QUOTE" not in claim
    assert 'href="#cite-source-1"' in claim
    broad = panel(html, "source-1")
    assert "OTHER QUOTE" in broad
    assert "不代表当前句的核验依据" in broad
