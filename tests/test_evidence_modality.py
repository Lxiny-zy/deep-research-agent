"""sc82 regression: fake verdicts check policy wiring, not real model accuracy."""

import json

from deep_research.agents.base import RunContext
from deep_research.guardrails import SemanticEvidenceDecisionList, SemanticEvidenceVerifier
from deep_research.models import ExtractedFindingList, Finding, Source
from deep_research.observability import Tracer
from deep_research.prompting import EVIDENCE_MODALITY_RULES, MEASUREMENT_SCOPE_RULES
from deep_research.workbench.qa import answer_question
from deep_research.workbench.support import SupportDecisions
from tests.fakes import FakeLLM, FakeSearch, verified_finding

URL = "https://arxiv.org/abs/2608.09166v1?dr_section=7"
QUOTE = (
    "In contact-rich motion planning, prediction uncertainty can vary significantly across "
    "different strata of the robot's C-space.\n"
    "For example, sliding contact along a surface can display substantially different dynamics "
    "and uncertainty characteristics than free-space motion.\n"
    "Hence, a single globally calibrated threshold can become overly conservative in "
    "low-uncertainty regions (or overly optimistic in high-uncertainty regions), making "
    "MondrianCP well-suited for our setting."
)
REJECTED = (
    "由于不确定性在接触丰富与无接触运动等不同分层间差异显著，单一全局标定阈值会在低不确定性区域"
    "过度保守、在高不确定性区域过度乐观，因此需要按组标定。"
)
SUPPORTED = "MondrianCP 适合文中设定，单一全局标定阈值可能在低不确定性区域过度保守。"


class ModalityJudge(FakeLLM):
    def __init__(self):
        super().__init__()
        self.evidence_systems = []

    async def parse(self, system, user, schema, **kwargs):
        if schema is ExtractedFindingList:
            return ExtractedFindingList(findings=[
                Finding(statement=s, source_url=URL, evidence_quote=QUOTE, confidence=0.9)
                for s in (REJECTED, SUPPORTED)
            ])
        if schema is SemanticEvidenceDecisionList:
            self.evidence_systems.append(system)
            # Model fixture returns the policy-aware verdict only when it receives
            # the shared instructions. This deliberately does not infer semantics.
            has_policy = EVIDENCE_MODALITY_RULES in system
            records = user.split("\n\nRecords to verify:\n", 1)[1].split("\n\n")
            decisions = []
            for record in records:
                lines = record.splitlines()
                index = int(lines[0].removeprefix("Index: "))
                statement = json.loads(
                    next(line[11:] for line in lines if line.startswith("Statement: "))
                )
                rejected = has_policy and statement == REJECTED
                decisions.append(dict(
                    index=index, verdict="unsupported" if rejected else "supported",
                    confidence=0.95,
                    reason="fixture: modality strengthened" if rejected else "fixture",
                ))
            return SemanticEvidenceDecisionList(decisions=decisions)
        if schema is SupportDecisions:
            raise TimeoutError("force the evidence-only fallback")
        return await super().parse(system, user, schema, **kwargs)

    async def stream(self, *args, **kwargs):
        self.stream_calls += 1
        yield SUPPORTED + " [1]"


class Sc82Search(FakeSearch):
    async def search(self, query, *, max_results=5):
        return [Source(url=URL, title="sc82 archived evidence", content=QUOTE)]


async def test_finding_verifier_receives_shared_modality_and_scope_rules():
    model = ModalityJudge()
    checked = await SemanticEvidenceVerifier().verify_batch(
        [verified_finding(REJECTED, URL, QUOTE), verified_finding(SUPPORTED, URL, QUOTE)], model
    )
    assert model.evidence_systems
    assert all(EVIDENCE_MODALITY_RULES in s for s in model.evidence_systems)
    assert all(MEASUREMENT_SCOPE_RULES in s for s in model.evidence_systems)
    assert "可能" in EVIDENCE_MODALITY_RULES and "条件" in EVIDENCE_MODALITY_RULES
    assert checked[0].verification.semantic_status == "unsupported"
    assert checked[1].verification.semantic_status == "supported"


async def test_sc82_rejected_finding_cannot_reappear_in_qa_fallback(settings):
    model = ModalityJudge()
    ctx = RunContext(llm=model, search_tool=Sc82Search(), tracer=Tracer(), settings=settings)
    result = await answer_question(
        "为什么单一全局标定与分组标定的适用条件不同？", history=[], ctx=ctx, include_web=True
    )
    assert model.evidence_systems and model.stream_calls == 1
    assert result.fallback and "已验证素材摘要" in result.answer
    assert REJECTED not in result.answer
    assert "因此需要按组标定" not in result.answer
    assert SUPPORTED in result.answer
    assert all(f.statement != REJECTED for f in result.findings)
