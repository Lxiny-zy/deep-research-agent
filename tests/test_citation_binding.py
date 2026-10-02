from __future__ import annotations

import json
import re

from deep_research.bibliography import build_bibliography
from deep_research.models import ResearchResult
from deep_research.workbench.citation_binding import bind_review
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.support import SupportDecisions, evidence_id, evidence_records
from tests.fakes import verified_finding


class Judge:
    calls = 0

    async def parse(self, system, user, schema, **kwargs):
        self.calls += 1
        data = json.loads(user)
        decisions = []
        for unit in data["units"]:
            key = "B" if "CLAIM-B" in unit["text"] else "A"
            selected = [e["id"] for e in data["evidence"] if e["statement"] == key]
            factual = "CLAIM-" in unit["text"]
            decisions.append(
                {
                    "unit_id": unit["id"],
                    "verdict": "supported" if factual else "non_factual",
                    "evidence_ids": selected if factual else [],
                    "reason": "controlled review fixture",
                }
            )
        return SupportDecisions(decisions=decisions)


def material(urls=None):
    urls = urls or ["https://example.org/paper"] * 3
    return [
        ResearchResult(
            sub_question="q",
            findings=[
                verified_finding(key, source_url=url, evidence_quote=f"original quote for {key}")
                for key, url in zip(["A", "B", "C"], urls, strict=True)
            ],
        )
    ]


async def test_same_location_in_different_paragraphs_keeps_distinct_selected_quotes():
    results = material()
    urls = ["https://example.org/paper"]
    text = "CLAIM-A [1].\n\nCLAIM-B [1].\n\n## 参考来源\n[1] Paper"
    judge = Judge()
    checker = ProseReviewer.research(judge, results, {urls[0]: 1}, 50000)
    record = await checker.review(text)
    before = judge.calls
    catalog = build_bibliography(text, urls, results[0].findings)
    assert bind_review(catalog, checker, text, record)
    assert judge.calls == before
    a, b = catalog.occurrences
    assert a.id != b.id and a.locations == b.locations == [1]
    assert a.scope == b.scope == "reviewed_unit"
    assert a.evidence_ids == [evidence_id(results[0].findings[0])]
    assert b.evidence_ids == [evidence_id(results[0].findings[1])]
    assert f"#cite-o-{a.id}" in catalog.body and f"#cite-o-{b.id}" in catalog.body


async def test_table_rows_and_crlf_use_their_own_review_decisions():
    results = material()
    url = results[0].findings[0].source_url
    text = "| Item | Result |\r\n|---|---|\r\n| A | CLAIM-A [1] |\r\n| B | CLAIM-B [1] |"
    checker = ProseReviewer.research(Judge(), results, {url: 1}, 50000)
    catalog = build_bibliography(text, [url], results[0].findings)
    assert bind_review(catalog, checker, text, await checker.review(text))
    assert [c.evidence_ids for c in catalog.occurrences] == [
        [evidence_id(results[0].findings[0])],
        [evidence_id(results[0].findings[1])],
    ]


async def test_unselected_location_does_not_borrow_another_source_or_fall_back_to_all():
    urls = ["https://example.org/a", "https://example.org/b", "https://example.org/b"]
    results = material(urls)
    text = "CLAIM-A [1,2]."
    checker = ProseReviewer.research(Judge(), results, {urls[0]: 1, urls[1]: 2}, 50000)
    catalog = build_bibliography(text, urls[:2], results[0].findings)
    assert bind_review(catalog, checker, text, await checker.review(text))
    assert catalog.occurrences[0].scope == "reviewed_unit"
    assert catalog.occurrences[1].scope == "unused_location"
    assert catalog.occurrences[1].evidence_ids == []


async def test_stale_review_and_wrong_body_cannot_create_scoped_links():
    results = material()
    url = results[0].findings[0].source_url
    text = "CLAIM-A [1]."
    checker = ProseReviewer.research(Judge(), results, {url: 1}, 50000)
    record = await checker.review(text)
    catalog = build_bibliography("Changed [1].", [url], results[0].findings)
    assert not bind_review(catalog, checker, text, record)
    assert not catalog.occurrences and catalog.binding_status == "invalid"
    catalog = build_bibliography(text, [url], results[0].findings)
    stale = {**record, "input_hash": "old"}
    assert not bind_review(catalog, checker, text, stale)
    assert "#cite-1" in catalog.body and "#cite-o-" not in catalog.body
    assert not bind_review(catalog, checker, text, None)
    assert catalog.binding_status == "unavailable"
    changed = results[0].model_copy(deep=True)
    changed.findings[0].verification.source_content_hash = "new-snapshot"
    newer = ProseReviewer.research(Judge(), [changed], {url: 1}, 50000)
    assert not bind_review(catalog, newer, text, record)
    assert not catalog.occurrences


async def test_offline_html_filters_scoped_evidence_and_offers_explicit_source_browse():
    from deep_research.workbench.delivery.html import render_html

    results = material()
    url = results[0].findings[0].source_url
    text = "CLAIM-A [1].\n\nCLAIM-B [1]."
    checker = ProseReviewer.research(Judge(), results, {url: 1}, 50000)
    catalog = build_bibliography(text, [url], results[0].findings)
    bind_review(catalog, checker, text, await checker.review(text))
    html = render_html(
        catalog.body, title="t", bibliography=catalog, evidence=evidence_records(results, {url: 1})
    )
    first = re.search(
        rf'<aside class="citation-location" id="cite-o-{catalog.occurrences[0].id}">(.*?)</aside>',
        html,
        re.S,
    )[1]
    assert "original quote for A" in first
    assert "original quote for B" not in first and "original quote for C" not in first
    assert 'href="#cite-1"' in first
    broad = re.search(r'<aside class="citation-location" id="cite-1">(.*?)</aside>', html, re.S)[1]
    assert (
        "original quote for A" in broad
        and "original quote for B" in broad
        and "original quote for C" in broad
    )


def test_unadmitted_findings_have_no_support_id_in_report_wire_data():
    from deep_research.models import Report
    from deep_research.report.assemble import assemble_document

    results = material()
    results[0].findings[1].verification.semantic_status = "unsupported"
    url = results[0].findings[0].source_url
    document = assemble_document(
        Report(query="q", markdown="CLAIM-A [1].", citations=[url]), results
    )
    assert document.evidence[0].support_id == evidence_id(results[0].findings[0])
    assert not document.evidence[1].support_id
