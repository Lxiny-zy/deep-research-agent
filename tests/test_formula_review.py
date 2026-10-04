"""Formula transcription must preserve coefficients, scripts and operator bounds."""

import json

import pytest

from deep_research.workbench.support import (
    SupportDecision,
    SupportDecisions,
    SupportReviewer,
    SupportUnit,
)

SC60_PDF_SHA256 = "d72934d27b52514081e2ca1b718df84c59a91db428464fbd8a38b1f429fea4c0"
WRONG = (
    r"z^{k+1}=\arg\min_{z}\frac{1}{2}\left(\sqrt{\tau_{k+1}/\mu_{k+1}}\right)^2"
    r"\lVert z-x^{k+1}\rVert^2+R(z)"
)
CORRECT = (
    r"z^{k+1}=\arg\min_{z}\frac{1}{2\left(\sqrt{\tau_{k+1}/\mu_{k+1}}\right)^2}"
    r"\lVert z-x^{k+1}\rVert^2+R(z)"
)
RAW_PDF = (
    "Returning to Eq. (5), we also set τ as iteration-speciﬁc parameters "
    "and zk+1 can be reformulated as\n"
    "zk+1 = arg min\nz\n1\n2(\np\nτk+1/µk+1)2 ||z −xk+1||2 + R(z).\n(11)"
)


class Optimistic:
    async def parse(self, system, user, schema, **kwargs):
        assert schema is SupportDecisions
        data = json.loads(user)
        return SupportDecisions(
            decisions=[
                SupportDecision(
                    unit_id=unit["id"],
                    verdict="supported",
                    evidence_ids=[
                        entry["id"]
                        for entry in data["evidence"]
                        if entry["citation"] in unit["citations"]
                    ],
                    reason="optimistic fixture",
                )
                for unit in data["units"]
            ]
        )


@pytest.mark.asyncio
async def test_sc60_reciprocal_error_cannot_pass_an_optimistic_general_review():
    reviewer = SupportReviewer(
        Optimistic(),
        [
            dict(
                id="source",
                citation=1,
                statement="Denoising objective",
                quote=f"Equation (11): ${CORRECT}$",
                source="https://example.org/dauhst",
            )
        ],
        50000,
    )
    unit = SupportUnit("eq11", f"论文式(11)为 $${WRONG}$$ [1]。", kind="prose", citations=[1])
    decision = (await reviewer.review([unit]))[0]
    assert decision.verdict == "unsupported"
    assert "公式" in decision.reason


@pytest.mark.parametrize(
    "reported, reference, expected",
    [
        (WRONG, CORRECT, "different"),
        (CORRECT, CORRECT.replace(r"\frac", r"\dfrac"), "equal"),
        (r"\frac{\sigma^2}{2}", r"\frac{1}{2}\sigma^{2}", "equal"),
        (r"\frac{1}{2\sigma^2}", r"\frac{1}{2}\frac{1}{\sigma^2}", "equal"),
        (r"y=x_i", r"y=x_{i+1}", "different"),
        (r"y=x^2", r"y=x^3", "different"),
        (r"y=\sum_{i=0}^n x_i", r"y=\sum_{i=1}^n x_i", "different"),
        (r"y=\sum\limits_{i=1}^{n} x_i", r"y=\sum_{i=1}^n x_i", "equal"),
        (r"y=\sum_{j=1}^n x_j", r"y=\sum_{i=1}^n x_i", "unresolved"),
        (r"y=a+b", r"y=b+a", "unresolved"),
        (r"y=R(z)", r"y=Rz", "unresolved"),
        (r"\sqrt10", r"\sqrt{10}", "unresolved"),
        (r"x^12", r"x^{12}", "unresolved"),
    ],
)
def test_structural_comparison_is_conservative(reported, reference, expected):
    from deep_research.workbench.formula_structure import compare_formulas

    assert compare_formulas(reported, reference)[0] == expected


class Specialist(Optimistic):
    def __init__(self, *, mismatch=False, omit=False, fake_quote=False, wrong_source=False):
        self.mismatch, self.omit = mismatch, omit
        self.fake_quote, self.wrong_source = fake_quote, wrong_source
        self.inspections = []

    async def parse(self, system, user, schema, **kwargs):
        from deep_research.workbench.formula_review import FormulaDecisions

        if schema is not FormulaDecisions:
            return await super().parse(system, user, schema, **kwargs)
        data = json.loads(user)
        self.inspections.append(data)
        assert all(
            word in system for word in ("coefficients", "subscripts", "superscripts", "bounds")
        )
        source = data["sources"][0]
        from deep_research.workbench.formula_structure import compare_formulas

        ref = next(
            (
                ref
                for ref in data["reference_formulas"]
                if ref["source_id"] == source["id"]
                and compare_formulas(data["formulas"][0]["tex"], ref["tex"])[0] == "equal"
            ),
            None,
        )
        rows = [
            dict(
                formula_id=formula["id"],
                verdict="mismatch" if self.mismatch else "matched",
                source_id="outside" if self.wrong_source else source["id"],
                reference_id=ref["id"] if ref else "",
                source_quote="invented formula"
                if self.fake_quote
                else ref["tex"]
                if ref
                else source["context"],
                checks={
                    key: "different" if self.mismatch and key == "coefficients" else "same"
                    for key in ("symbols", "coefficients", "subscripts", "superscripts", "bounds")
                },
                reason="式(11)是倒数系数，报告把分母移到了分子"
                if self.mismatch
                else "controlled formula match",
            )
            for formula in data["formulas"]
        ]
        return FormulaDecisions(decisions=rows[:-1] if self.omit else rows)


@pytest.mark.asyncio
async def test_raw_pdf_layout_is_sent_to_the_specialist_and_not_guessed_by_the_parser():
    llm = Specialist(mismatch=True)
    reviewer = SupportReviewer(
        llm,
        [
            dict(
                id="source",
                citation=1,
                statement="Denoising",
                quote=RAW_PDF,
                source="https://example.org/dauhst",
            )
        ],
        50000,
    )
    decision = (
        await reviewer.review(
            [SupportUnit("eq11", f"论文式(11)： $${WRONG}$$ [1]。", kind="prose", citations=[1])]
        )
    )[0]
    assert decision.verdict == "unsupported"
    assert llm.inspections[0]["sources"][0]["context"] == RAW_PDF
    assert "coefficients" in decision.formula_review["decisions"][0]["checks"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["omit", "fake_quote", "wrong_source"])
async def test_missing_decisions_or_unbound_formula_sources_never_pass(failure):
    reviewer = SupportReviewer(
        Specialist(**{failure: True}),
        [
            dict(
                id="source",
                citation=1,
                statement="Denoising",
                quote=RAW_PDF,
                source="https://example.org/dauhst",
            )
        ],
        50000,
    )
    decision = (
        await reviewer.review(
            [SupportUnit("eq11", f"论文式(11)： $${CORRECT}$$ [1]。", kind="prose", citations=[1])]
        )
    )[0]
    assert decision.verdict in {"unsupported", "uncertain"}


def test_formula_extraction_ignores_code_and_isolated_notation():
    from deep_research.workbench.formula_review import formulas

    text = "$x_i$ 和 $x^{k+1}$ 是变量。\n\n```python\n'$L=99$'\n```\n\n真实公式 $L=1$ [1]。"
    assert [item["tex"] for item in formulas(text)] == ["L=1"]
    assert formulas("量在区间 $[0,1]$ 内。") == []


@pytest.mark.asyncio
async def test_formula_record_uses_source_context_and_survives_only_while_its_inputs_match():
    from copy import deepcopy

    from deep_research.bibliography import build_bibliography
    from deep_research.document_corpus import content_hash
    from deep_research.models import ExtractionAudit, ResearchResult, Source
    from deep_research.workbench.citation_binding import bind_review
    from deep_research.workbench.prose_review import ProseReviewer
    from tests.fakes import verified_finding

    url = "https://example.org/dauhst"
    quote = "The prior step is a Gaussian denoising problem."
    source = Source(url=url, content=quote + f"\nEquation (11): ${CORRECT}$", locator="page 4")
    finding = verified_finding("Denoising objective", url, quote)
    finding.verification.source_content_hash = content_hash(source.content)
    results = [
        ResearchResult(
            sub_question="q",
            findings=[finding],
            extraction_audit=ExtractionAudit(question="q", sources=[source]),
        )
    ]
    body = f"论文式(11)： $${CORRECT}$$ [1]。"
    llm = Specialist()
    reviewer = ProseReviewer.research(llm, results, {url: 1}, 50000)
    record = await reviewer.review(body)
    assert record["status"] == "pass" and len(llm.inspections) == 1
    assert llm.inspections[0]["sources"][0]["context"] == source.content
    assert reviewer.check(body, record) == (True, [])
    catalog = build_bibliography(body, [url], [finding], [source])
    assert bind_review(catalog, reviewer, body, record)
    assert "公式核查原文" in catalog.occurrences[0].review_note
    forged = deepcopy(record)
    forged["decisions"][0]["formula_review"] = None
    assert reviewer.check(body, forged)[1]
    reviewer.reviewer.cache.clear()
    reviewer.prime(body, forged)
    assert not reviewer.reviewer.cache
    assert not bind_review(catalog, reviewer, body, forged)
    changed = deepcopy(results)
    changed[0].extraction_audit.sources[0].content = quote + "\nChanged equation."
    current = ProseReviewer.research(None, changed, {url: 1}, 50000)
    assert not current.check(body, record)[0]


@pytest.mark.asyncio
async def test_a_general_judge_cannot_bind_another_excerpt_after_formula_verification():
    class WrongBinding(Optimistic):
        async def parse(self, system, user, schema, **kwargs):
            result = await super().parse(system, user, schema, **kwargs)
            for decision in result.decisions:
                decision.evidence_ids = ["other"]
            return result

    reviewer = SupportReviewer(
        WrongBinding(),
        [
            dict(id="source", citation=1, quote="$y=x_i$", source="url"),
            dict(id="other", citation=1, quote="Some unrelated context.", source="url"),
        ],
        50000,
    )
    decision = (
        await reviewer.review([SupportUnit("u", "公式 $y=x_i$ [1]。", kind="prose", citations=[1])])
    )[0]
    assert decision.verdict == "unsupported"
    assert "公式核验使用的原文未绑定" in decision.reason


@pytest.mark.asyncio
async def test_a_specialist_cannot_return_an_entire_long_source_as_its_witness():
    source = RAW_PDF * 5
    reviewer = SupportReviewer(
        Specialist(), [dict(id="source", citation=1, quote=source, source="url")], 50000
    )
    decision = (
        await reviewer.review(
            [SupportUnit("u", f"论文式(11)： $${CORRECT}$$ [1]。", kind="prose", citations=[1])]
        )
    )[0]
    assert decision.verdict == "uncertain" and "600" in decision.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("explanation", ["代入已声明的 sigma=1 后，两式均为二分之一。", ""])
@pytest.mark.parametrize("actual_condition", [True, False])
async def test_conditional_equivalence_needs_an_explicit_premise_and_justification(
    explanation, actual_condition
):
    from deep_research.workbench.formula_review import FormulaDecisions

    condition = "假设 sigma 等于 1"

    class Conditional(Optimistic):
        async def parse(self, system, user, schema, **kwargs):
            if schema is not FormulaDecisions:
                return await super().parse(system, user, schema, **kwargs)
            data = json.loads(user)
            ref = data["reference_formulas"][0]
            return FormulaDecisions(
                decisions=[
                    dict(
                        formula_id=data["formulas"][0]["id"],
                        verdict="matched",
                        source_id="source",
                        reference_id=ref["id"],
                        source_quote=ref["tex"],
                        relation="conditional",
                        condition_quote=condition if actual_condition else "假设 sigma 等于 0",
                        equivalence_explanation=explanation,
                        checks={
                            key: "same"
                            for key in (
                                "symbols",
                                "coefficients",
                                "subscripts",
                                "superscripts",
                                "bounds",
                            )
                        },
                        reason="在给定条件下等价",
                    )
                ]
            )

    reviewer = SupportReviewer(
        Conditional(),
        [
            dict(
                id="source",
                citation=1,
                source="url",
                quote=r"$y=\frac{1}{2\sigma^2}$",
            )
        ],
        50000,
    )
    unit = SupportUnit(
        "u", condition + r"，原式可写为 $y=\sigma^2/2$ [1]。", kind="prose", citations=[1]
    )
    decision = (await reviewer.review([unit]))[0]
    assert (decision.verdict == "supported") == bool(explanation and actual_condition)


def test_parser_declines_unbounded_reduction_and_non_math_commands():
    from deep_research.workbench.formula_structure import formula_tree

    assert formula_tree(r"\input{some-file}") is None
    assert formula_tree("(" * 110 + "x" + ")" * 110) is None
    assert formula_tree("+".join(["x"] * 1000)) is None
    assert formula_tree("(((2^{16})^{16})^{16})^{16}") is None


@pytest.mark.asyncio
async def test_each_formula_uses_its_own_attached_citation_not_another_equation_in_the_unit():
    reviewer = SupportReviewer(
        Optimistic(),
        [
            dict(id="first", citation=1, quote="$y=x^2$", source="one"),
            dict(id="second", citation=2, quote="$y=x^3$", source="two"),
        ],
        50000,
    )
    wrong = SupportUnit(
        "u", "原式为 $y=x^3$ [1]，另一文献为 $y=x^2$ [2]。", kind="prose", citations=[1, 2]
    )
    assert (await reviewer.review([wrong]))[0].verdict == "unsupported"
    correct = SupportUnit(
        "v", "原式为 $y=x^2$ [1]，另一文献为 $y=x^3$ [2]。", kind="prose", citations=[1, 2]
    )
    assert (await reviewer.review([correct]))[0].verdict == "supported"


@pytest.mark.asyncio
async def test_verified_fraction_conversion_does_not_lend_numbers_to_other_claims():
    reviewer = SupportReviewer(
        Optimistic(),
        [
            dict(
                id="source",
                citation=1,
                quote=r"$y=\frac{1}{2}$",
                source="url",
            )
        ],
        50000,
    )
    good = SupportUnit("u", "公式 $y=0.5$ [1]。", kind="prose", citations=[1])
    assert (await reviewer.review([good]))[0].verdict == "supported"
    bad = SupportUnit(
        "v", "公式 $y=0.5$ [1]，另一个实验得分 0.5 [1]。", kind="prose", citations=[1]
    )
    assert (await reviewer.review([bad]))[0].verdict == "unsupported"


def test_plain_source_assignment_is_never_cut_before_its_operator_or_unit():
    from deep_research.workbench.formula_review import _source_formulas

    assert [item["tex"] for item in _source_formulas("训练通过 L = 1/2 进行优化。")] == ["L = 1/2"]
    assert _source_formulas("报告 x = 95% 作为比例。") == []
    assert _source_formulas("报告 x = 1e-3 作为误差。") == []
    assert _source_formulas("报告 x = 1 + y 作为结果。") == []


@pytest.mark.asyncio
async def test_specialist_pass_cannot_disable_exact_arithmetic_checks():
    reviewer = SupportReviewer(
        Specialist(),
        [
            dict(
                id="source",
                citation=1,
                source="url",
                quote="The calculation uses 1 and 2 and 3.",
            )
        ],
        50000,
    )
    unit = SupportUnit("u", "公式 $1+1=3$ [1]。", kind="prose", citations=[1])
    assert (await reviewer.review([unit]))[0].verdict == "unsupported"


def test_approximation_and_inequality_rewrites_require_semantic_review():
    from deep_research.workbench.formula_structure import compare_formulas

    assert compare_formulas(r"y\approx0.33", r"y\approx0.333")[0] == "unresolved"
    assert compare_formulas("x<2", "x<1")[0] == "unresolved"
