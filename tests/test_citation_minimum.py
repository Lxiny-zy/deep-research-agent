"""A small available corpus does not create an impossible citation requirement."""

from dataclasses import replace

from deep_research.models import ResearchResult
from deep_research.workbench.gates import citation_gate, scholarly_gate
from deep_research.workbench.quality import QualityPolicy
from deep_research.workbench.revision import assess_draft
from deep_research.workbench.templates import get_template
from tests.fakes import verified_finding


def test_two_available_library_documents_satisfy_writer_and_delivery_minimum():
    template = replace(get_template("autoResearch"), sections=(), min_length=0)
    urls = ["https://library.test/a", "https://library.test/b"]
    body = "已核验结果 [1,2]。\n\n## 局限\n仍需更多独立资料。"
    policy = QualityPolicy(register_check=False, require_limitations=False)
    gate = citation_gate(body, urls, template, min_citations=3)
    assert gate.status == "pass"
    assert gate.metrics["required"] == 2
    assert gate.metrics["requested"] == 3
    assert gate.metrics["retrieval_shortfall"] == 1
    scholarly = scholarly_gate(
        body, template=template, query="q", citations=urls, min_citations=3, policy=policy
    )
    assert scholarly.status == "pass"
    assessment = assess_draft(
        body,
        template=template,
        query="q",
        results=[
            ResearchResult(
                sub_question="q", findings=[verified_finding(source_url=url) for url in urls]
            )
        ],
        url_to_idx={url: i for i, url in enumerate(urls, 1)},
        policy=policy,
        min_citations=3,
        check_citations=False,
    )
    assert not assessment.hard


def test_missing_an_available_document_still_requires_revision():
    template = get_template("autoResearch")
    urls = ["https://library.test/a", "https://library.test/b"]
    assert citation_gate("Result [1].", urls, template, 3).status == "warn"
    gate = scholarly_gate(
        "Result [1].",
        template=template,
        query="q",
        citations=urls,
        min_citations=3,
        policy=QualityPolicy(register_check=False, require_limitations=False),
    )
    assert gate.status == "warn"
    assert any("下限" in issue for issue in gate.issues)


def test_citation_locations_of_one_work_do_not_inflate_available_count():
    urls = [
        "https://library.test/a#chunk-1",
        "https://library.test/a#chunk-2",
        "https://library.test/b",
    ]
    keys = {urls[0]: "a", urls[1]: "a", urls[2]: "b"}
    gate = citation_gate(
        "Result [1,2,3].", urls, get_template("autoResearch"), 3, document_keys=keys
    )
    assert gate.status == "pass" and gate.metrics["required"] == 2
    assert gate.metrics["used"] == 2
    assert (
        citation_gate(
            "Result [4].", urls, get_template("autoResearch"), 3, document_keys=keys
        ).status
        == "fail"
    )
