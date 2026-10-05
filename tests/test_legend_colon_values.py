"""Explicit method/metric legend labels must survive numeric inventory."""

import hashlib

import pytest

from deep_research.agents.researcher import Researcher
from deep_research.guardrails import EvidenceVerifier
from deep_research.models import Quantity, Source
from deep_research.quantities import measurement_supported, parse_measurements
from deep_research.workbench.paper_evidence import plan_findings
from tests.fakes import verified_finding
from tests.test_paper_evidence_reuse import SelectionLLM


@pytest.mark.parametrize("separator", [":", "=", "："])
def test_dimensionless_legend_preserves_method_metric_and_exact_offsets(separator):
    source = (
        f"DAUHST-9stg, corr{separator} 0.9987\r\n"
        f"MST-L, corr{separator} 0.9953\r\n"
        f"DAUHST-9stg, corr{separator} 0.9994\r\n"
    )
    values = [v for v in parse_measurements(source) if v.entity]
    assert [(v.entity, v.row_metric, v.value, v.unit) for v in values] == [
        ("DAUHST-9stg", "corr", 0.9987, ""),
        ("MST-L", "corr", 0.9953, ""),
        ("DAUHST-9stg", "corr", 0.9994, ""),
    ]
    assert all(source[v.start:v.end].strip() == v.raw for v in values)
    assert all(v.header_metric == "corr" for v in values)


def test_colon_legend_keeps_units_local_and_does_not_borrow_a_different_axis():
    text = "Runtime/ms\nA, AT: 30\nB, AT: 0.04 s\nA, corr: 0.9\nB, AT: 50\n"
    values = [v for v in parse_measurements(text) if v.entity]
    assert [(v.row_metric, v.value, v.unit) for v in values] == [
        ("AT", 0.03, "s"), ("AT", 0.04, "s"), ("corr", 0.9, ""), ("AT", 50, ""),
    ]


@pytest.mark.parametrize("tail", ["0.9 and 0.8", "> 0.9", "[0.8, 0.9]", "0.9 ± 0.1"])
def test_non_scalar_legend_does_not_invent_one_bound_value(tail):
    assert not any(v.entity for v in parse_measurements(f"DAUHST-9stg, corr: {tail}"))


def test_same_number_with_an_explicit_different_metric_is_rejected():
    ok, _ = measurement_supported(
        value=0.9994, unit="", rendered="0.9994", metric="SSIM",
        evidence="DAUHST-9stg, corr: 0.9994",
    )
    assert not ok


async def test_two_sc55_legend_values_require_both_even_when_model_selects_one(settings):
    rows = ["DAUHST-9stg, corr: 0.9987", "DAUHST-9stg, corr: 0.9994"]
    source = Source(url="https://paper.test/figure4", content="\n\n".join(rows))

    def make(index, metric="corr"):
        finding = verified_finding("图4中一处相关系数", source.url, rows[index])
        finding.entity = "DAUHST-9stg"
        finding.quantity = Quantity(
            metric=metric, value=[0.9987, 0.9994][index], rendered=rows[index].split()[-1],
        )
        checked = EvidenceVerifier().verify(finding, source).finding
        assert checked is not None
        checked.verification.semantic_status = "supported"
        return checked

    one, two = make(0), make(1)
    llm = SelectionLLM()
    researcher = Researcher(llm=llm, settings=settings)
    partial = await plan_findings([one], "图4的 corr 值是什么", [], researcher, [source])
    assert not partial.sufficient and partial.source_urls == [source.url]
    complete = await plan_findings([one, two], "图4的 corr 值是什么", [], researcher, [source])
    assert complete.sufficient and len(complete.findings) == 2
    # A different metric or changed source snapshot cannot fill the missing value.
    wrong = two.model_copy(deep=True)
    wrong.quantity.metric = "SSIM"
    mismatch = await plan_findings([one, wrong], "图4 corr", [], researcher, [source])
    assert not mismatch.sufficient
    wrong.quantity.metric = "corr"
    wrong.verification.source_content_hash = hashlib.sha256(b"other source").hexdigest()
    stale = await plan_findings([one, wrong], "图4 corr", [], researcher, [source])
    assert not stale.sufficient
