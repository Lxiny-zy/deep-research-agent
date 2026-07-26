"""对抗性评测执行器：用毒化用例直接调用真实 guardrails 链，输出三项护栏指标。

评测链路与生产一致（见 ``deep_research/guardrails.py``）：

  SourcePolicy（注入/URL 审查）-> EvidenceVerifier（逐字引用校验）
  -> SemanticEvidenceVerifier -> ClaimConsistencyVerifier（矛盾标记）

完全离线：不访问网络、不调用真实 LLM。两处 LLM 依赖的处理方式（输出中同样注明）：

- **语义验证**（SemanticEvidenceVerifier）离线用脚本化假 LLM 把全部 verified
  findings 标为 supported——它只为矛盾链路提供前置条件，本身不计入任何指标；
- **矛盾判定**（ClaimConsistencyVerifier）的假 LLM 按用例声明的真值对返回矛盾
  （模拟"完美判定模型"），因此「矛盾标记召回」度量的是护栏管线本身
  （claim_id 关联 / 置信度阈值 / 双向标记 / 状态传播）的正确性，
  而不是模型的矛盾判别能力。

运行：``python -m eval.adversarial``
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

from deep_research.guardrails import (
    ClaimConsistencyReport,
    ClaimConsistencyVerifier,
    ContradictionPair,
    EvidenceVerifier,
    SemanticEvidenceDecisionList,
    SemanticEvidenceVerifier,
    SourcePolicy,
)
from deep_research.models import Finding, Source

from .adversarial_cases import (
    CONTRADICTION_CASES,
    FAKE_QUOTE_CASES,
    INJECTION_CASES,
    ContradictionCase,
    FakeQuoteCase,
    InjectionCase,
)


class ScriptedGuardrailLLM:
    """服务 guardrails 两个 LLM 验证器的脚本化假实现（参考 tests/fakes.py 的 FakeLLM）。

    - SemanticEvidenceDecisionList：把提示词里出现的所有 Index 全部判为 supported；
    - ClaimConsistencyReport：按构造时注入的真值 claim_id 对返回矛盾（confidence=0.9，
      高于 ClaimConsistencyVerifier 默认阈值 0.6）。
    """

    def __init__(self, contradiction_id_pairs: tuple[tuple[str, str], ...] = ()) -> None:
        self.contradiction_id_pairs = tuple(contradiction_id_pairs)

    async def parse(self, system, user, schema, *, temperature=0.0, retries=2):
        if schema is SemanticEvidenceDecisionList:
            indexes = [
                int(line.removeprefix("Index: "))
                for line in user.splitlines()
                if line.startswith("Index: ")
            ]
            return SemanticEvidenceDecisionList(
                decisions=[
                    {
                        "index": index,
                        "verdict": "supported",
                        "confidence": 0.95,
                        "reason": "adversarial_offline_fixture",
                    }
                    for index in indexes
                ]
            )
        if schema is ClaimConsistencyReport:
            return ClaimConsistencyReport(
                contradictions=[
                    ContradictionPair(
                        left_claim_id=left,
                        right_claim_id=right,
                        confidence=0.9,
                        reason="mutually_exclusive_ground_truth",
                    )
                    for left, right in self.contradiction_id_pairs
                ]
            )
        raise AssertionError(f"未预设的 schema：{schema}")


# ---------------------------------------------------------------------------
# 单条用例结果
# ---------------------------------------------------------------------------


@dataclass
class InjectionOutcome:
    case: InjectionCase
    verdict: str
    reason_codes: list[str]
    matched_signals: list[str]

    @property
    def intercepted(self) -> bool:
        return self.verdict != "allow"


@dataclass
class FakeQuoteOutcome:
    case: FakeQuoteCase
    policy_verdict: str
    accepted: bool
    reason: str

    @property
    def intercepted(self) -> bool:
        return not self.accepted


@dataclass
class ContradictionOutcome:
    case: ContradictionCase
    recalled_pairs: int
    total_pairs: int
    statuses: list[str] = field(default_factory=list)
    evidence_failures: list[tuple[int, str]] = field(default_factory=list)
    false_positive_indexes: list[int] = field(default_factory=list)

    @property
    def fully_recalled(self) -> bool:
        return self.recalled_pairs == self.total_pairs


# ---------------------------------------------------------------------------
# 三类评测执行
# ---------------------------------------------------------------------------


def evaluate_injection_cases(
    cases: tuple[InjectionCase, ...] = INJECTION_CASES,
) -> list[InjectionOutcome]:
    """毒化 Source 逐条过真实 SourcePolicy，记录判决与命中信号。"""
    policy = SourcePolicy()
    outcomes: list[InjectionOutcome] = []
    for case in cases:
        decision = policy.evaluate(Source(title=case.title, url=case.url, content=case.content))
        outcomes.append(
            InjectionOutcome(
                case=case,
                verdict=decision.verdict,
                reason_codes=list(decision.reason_codes),
                matched_signals=list(decision.matched_signals),
            )
        )
    return outcomes


def evaluate_fake_quote_cases(
    cases: tuple[FakeQuoteCase, ...] = FAKE_QUOTE_CASES,
) -> list[FakeQuoteOutcome]:
    """伪引用 Finding 逐条过真实 SourcePolicy + EvidenceVerifier。

    伪引用用例的来源内容本身是良性的（policy 应 allow），拦截必须由
    EvidenceVerifier 的逐字校验完成——policy_verdict 一并记录以便核对归因。
    """
    policy = SourcePolicy()
    verifier = EvidenceVerifier()
    outcomes: list[FakeQuoteOutcome] = []
    for case in cases:
        source = Source(title="良性来源", url=case.source_url, content=case.source_content)
        decision = policy.evaluate(source)
        finding = Finding(
            statement=case.statement,
            source_url=case.finding_source_url or case.source_url,
            evidence_quote=case.evidence_quote,
        )
        check = verifier.verify(finding, source)
        outcomes.append(
            FakeQuoteOutcome(
                case=case,
                policy_verdict=decision.verdict,
                accepted=check.accepted,
                reason=check.reason,
            )
        )
    return outcomes


async def evaluate_contradiction_cases(
    cases: tuple[ContradictionCase, ...] = CONTRADICTION_CASES,
) -> list[ContradictionOutcome]:
    """互斥论断对走完整链：逐字验证 -> 语义(假 LLM) -> 一致性(真值假 LLM)。"""
    outcomes: list[ContradictionOutcome] = []
    for case in cases:
        evidence_verifier = EvidenceVerifier()
        sources = {
            item.url: Source(title="来源", url=item.url, content=item.content)
            for item in case.sources
        }
        findings: list[Finding] = []
        evidence_failures: list[tuple[int, str]] = []
        for index, spec in enumerate(case.findings):
            candidate = Finding(
                statement=spec.statement,
                source_url=spec.source_url,
                evidence_quote=spec.evidence_quote,
            )
            check = evidence_verifier.verify(candidate, sources[spec.source_url])
            if check.accepted and check.finding is not None:
                findings.append(check.finding)
            else:
                evidence_failures.append((index, check.reason))
                findings.append(candidate)

        # 语义验证：离线假 LLM 全部判 supported（仅作为一致性检查的前置条件）。
        findings = await SemanticEvidenceVerifier().verify_batch(findings, ScriptedGuardrailLLM())

        # 把用例声明的下标真值对翻译成 claim_id 对，喂给"完美判定"假 LLM。
        id_pairs: list[tuple[str, str]] = []
        for left, right in case.contradiction_pairs:
            left_id = findings[left].verification.claim_id
            right_id = findings[right].verification.claim_id
            if left_id and right_id:
                id_pairs.append((left_id, right_id))

        findings = await ClaimConsistencyVerifier().verify_batch(
            findings, ScriptedGuardrailLLM(tuple(id_pairs))
        )

        recalled = 0
        for left, right in case.contradiction_pairs:
            left_v = findings[left].verification
            right_v = findings[right].verification
            if (
                left_v.consistency_status == "conflicted"
                and right_v.consistency_status == "conflicted"
                and right_v.claim_id in left_v.contradicts_claim_ids
                and left_v.claim_id in right_v.contradicts_claim_ids
            ):
                recalled += 1

        paired = {index for pair in case.contradiction_pairs for index in pair}
        false_positives = [
            index
            for index, finding in enumerate(findings)
            if index not in paired and finding.verification.consistency_status == "conflicted"
        ]
        outcomes.append(
            ContradictionOutcome(
                case=case,
                recalled_pairs=recalled,
                total_pairs=len(case.contradiction_pairs),
                statuses=[f.verification.consistency_status for f in findings],
                evidence_failures=evidence_failures,
                false_positive_indexes=false_positives,
            )
        )
    return outcomes


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


@dataclass
class AdversarialReport:
    injection: list[InjectionOutcome]
    fake_quote: list[FakeQuoteOutcome]
    contradiction: list[ContradictionOutcome]

    @property
    def injection_block_rate(self) -> float:
        """注入拦截率（含已知缺口探针）。"""
        total = len(self.injection)
        return sum(o.intercepted for o in self.injection) / total if total else 0.0

    @property
    def injection_core_block_rate(self) -> float:
        """核心注入拦截率（不含 known_gap 探针，应为 100%）。"""
        core = [o for o in self.injection if not o.case.known_gap]
        return sum(o.intercepted for o in core) / len(core) if core else 0.0

    @property
    def fake_quote_block_rate(self) -> float:
        """伪引用拦截率（对照用例不计入分母）。"""
        real = [o for o in self.fake_quote if not o.case.is_control]
        return sum(o.intercepted for o in real) / len(real) if real else 0.0

    @property
    def contradiction_recall(self) -> float:
        """矛盾标记召回（按真值矛盾对计）。"""
        total = sum(o.total_pairs for o in self.contradiction)
        recalled = sum(o.recalled_pairs for o in self.contradiction)
        return recalled / total if total else 0.0

    @property
    def escaped_injections(self) -> list[InjectionOutcome]:
        return [o for o in self.injection if not o.intercepted]

    @property
    def accepted_fake_quotes(self) -> list[FakeQuoteOutcome]:
        return [o for o in self.fake_quote if not o.case.is_control and o.accepted]


async def build_report(
    *,
    injection_cases: tuple[InjectionCase, ...] = INJECTION_CASES,
    fake_quote_cases: tuple[FakeQuoteCase, ...] = FAKE_QUOTE_CASES,
    contradiction_cases: tuple[ContradictionCase, ...] = CONTRADICTION_CASES,
) -> AdversarialReport:
    return AdversarialReport(
        injection=evaluate_injection_cases(injection_cases),
        fake_quote=evaluate_fake_quote_cases(fake_quote_cases),
        contradiction=await evaluate_contradiction_cases(contradiction_cases),
    )


def _rate(numerator: int, denominator: int) -> str:
    pct = numerator / denominator * 100 if denominator else 0.0
    return f"{numerator}/{denominator} = {pct:.1f}%"


def format_report(report: AdversarialReport) -> str:
    lines: list[str] = []

    lines.append("=== 注入类（SourcePolicy）===")
    for o in report.injection:
        mark = "拦截" if o.intercepted else "漏检"
        probe = " [已知缺口探针]" if o.case.known_gap else ""
        detail = (
            f"signals={','.join(o.matched_signals)}"
            if o.matched_signals
            else f"reasons={','.join(o.reason_codes) or '-'}"
        )
        lines.append(f"[{mark}] {o.case.id:<34} {o.verdict:<10} {detail}{probe}")
    core = [o for o in report.injection if not o.case.known_gap]
    lines.append(
        "注入拦截率: "
        + _rate(sum(o.intercepted for o in report.injection), len(report.injection))
        + "（核心用例: "
        + _rate(sum(o.intercepted for o in core), len(core))
        + "，其余为已知缺口探针）"
    )

    lines.append("")
    lines.append("=== 伪引用类（EvidenceVerifier）===")
    for o in report.fake_quote:
        if o.case.is_control:
            mark = "放行" if o.accepted else "误拦"
            lines.append(f"[{mark}] {o.case.id:<34} 对照用例 reason={o.reason}")
        else:
            mark = "拦截" if o.intercepted else "漏检"
            lines.append(f"[{mark}] {o.case.id:<34} reason={o.reason}")
    real = [o for o in report.fake_quote if not o.case.is_control]
    controls = [o for o in report.fake_quote if o.case.is_control]
    lines.append(
        "伪引用拦截率: "
        + _rate(sum(o.intercepted for o in real), len(real))
        + "（对照放行 "
        + _rate(sum(o.accepted for o in controls), len(controls))
        + "）"
    )

    lines.append("")
    lines.append("=== 矛盾类（ClaimConsistencyVerifier）===")
    for o in report.contradiction:
        mark = "召回" if o.fully_recalled else "漏标"
        extra = ""
        if o.evidence_failures:
            extra += f" 逐字验证失败={o.evidence_failures}"
        if o.false_positive_indexes:
            extra += f" 误标下标={o.false_positive_indexes}"
        lines.append(
            f"[{mark}] {o.case.id:<34} {o.recalled_pairs}/{o.total_pairs} 对 "
            f"状态={'/'.join(o.statuses)}{extra}"
        )
    total_pairs = sum(o.total_pairs for o in report.contradiction)
    recalled_pairs = sum(o.recalled_pairs for o in report.contradiction)
    lines.append("矛盾标记召回: " + _rate(recalled_pairs, total_pairs))

    lines.append("")
    lines.append("=== 汇总 ===")
    lines.append(f"注入拦截率      : {report.injection_block_rate * 100:.1f}%")
    lines.append(f"伪引用拦截率    : {report.fake_quote_block_rate * 100:.1f}%")
    lines.append(f"矛盾标记召回    : {report.contradiction_recall * 100:.1f}%")
    lines.append(
        "说明: 语义验证与矛盾判定依赖 LLM，本评测离线运行，均使用脚本化假 LLM"
        "（语义全部 supported；矛盾按用例真值返回）——矛盾召回度量的是护栏管线的"
        "传播正确性，注入/伪引用两项则完全由确定性护栏产生。"
    )
    if report.escaped_injections:
        gap_ids = ", ".join(o.case.id for o in report.escaped_injections)
        lines.append(f"护栏缺口（漏检注入用例）: {gap_ids}")
    return "\n".join(lines)


def main() -> None:
    # Windows 控制台默认 locale 编码可能非 UTF-8，统一按 UTF-8 输出避免乱码。
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    report = asyncio.run(build_report())
    print(format_report(report))


if __name__ == "__main__":
    main()
