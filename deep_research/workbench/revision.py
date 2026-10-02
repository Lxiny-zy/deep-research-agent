"""写作返工循环：写 → 查 → 带问题清单重写，直到合格或返工次数用尽。

为什么需要这一层：旧流程写作者一次成稿，复核发现引用或数字对不上就整篇回退成
「已核验素材摘要」——安全，但交付物质量断崖式下降；而章节缺失、文体问题、引用
不足只在交付登记里打个标记。科研交付物不能靠「发现问题但照发」过关。

循环的纪律：

* **基础检查是确定性的**：引用 / 数值复核（``validate_body``）、模板章节、学术文体与来源
  检查（``scholarly.evaluate``）。特定交付物可以加入异步证据核对，并明确记录模型判断
  的范围；不能用模型判断冒充事实真值。核验服务失败时可停止无效的内容返工。
* **只让写作者修它能修的问题**：素材本身不足（可用来源少于下限、缺少指定年份的
  文献）是检索问题，重写不会变好；这类问题交给反思补洞与交付门报告，不消耗返工次数。
* **取最好的一版**：每一版都记下硬性问题数，最终交付问题最少的一版（同分取后者），
  返工不会让结果变差。
* **最终安全网不变**：选出的正文仍经过 ``finalize_report``；若仍有引用或数值问题，
  照旧回退为素材摘要，绝不交付编造的引用或改写过的数字。
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..models import ResearchResult
from ..persistence.repository import LeaseLostError
from ..report.validation import describe_problems, validate_body
from .delivery.math_markdown import citation_text
from .quality import QualityPolicy
from .scholarly import abstract_sections, evaluate, revision_brief, source_counts
from .templates import TaskTemplate

_CITE = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")


@dataclass
class Assessment:
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    brief: str = ""
    can_revise: bool = True

    @property
    def clean(self) -> bool:
        return not self.hard


@dataclass
class RevisionLog:
    attempts: int = 0
    history: list[dict[str, int]] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    chosen: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "history": self.history,
            "remaining": self.remaining,
            "advisories": self.advisories,
            "chosen": self.chosen,
        }


def _used_indices(body: str) -> set[int]:
    from ..bibliography import source_body

    return {
        int(number)
        for match in _CITE.findall(citation_text(source_body(body)))
        for number in re.split(r"\s*[,，]\s*", match)
    }


def _missing_sections(body: str, template: TaskTemplate) -> list[str]:
    from .gates import structure_gate

    if not template.sections or template.key in {"slides", "mindmap"}:
        return []
    result = structure_gate(body, template)
    return list(result.issues)


def assess_draft(
    body: str,
    *,
    template: TaskTemplate,
    query: str,
    results: list[ResearchResult],
    url_to_idx: dict[str, int],
    policy: QualityPolicy,
    min_citations: int,
    require_corroboration: bool = False,
    check_citations: bool = True,
    section_support: dict[str, str] | None = None,
    scratch: dict[str, Any] | None = None,
) -> Assessment:
    """对一版草稿做全部确定性检查，并写成交给写作者的返工说明。"""
    hard: list[str] = []
    soft: list[str] = []
    if check_citations and url_to_idx:
        check = validate_body(
            body,
            results,
            url_to_idx,
            require_corroboration=require_corroboration,
            fallback=False,
            uncited_sections=abstract_sections(template.key, policy),
            section_support=section_support,
        )
        hard += describe_problems(check)
    hard += _missing_sections(body, template)
    from ..bibliography import build_bibliography, work_keys

    document_keys = work_keys(
        build_bibliography(
            "", list(url_to_idx), [finding for result in results for finding in result.findings]
        )
    )
    available = source_counts(list(url_to_idx), document_keys=document_keys)[0]
    # 可用来源不足下限是检索问题：只要求写作者用上全部可用来源，不要求它凑数。
    effective_minimum = min(min_citations, available)
    used = len(_used_indices(body) & set(url_to_idx.values()))
    idx_to_url = {index: url for url, index in url_to_idx.items()}
    cited_urls = [idx_to_url[i] for i in sorted(_used_indices(body)) if i in idx_to_url]
    if scratch is not None:
        from .corpus import corpus_issues

        hard += corpus_issues(scratch, results, cited_urls, writable_only=True)
        soft += corpus_issues(scratch, results)
    report = evaluate(
        body,
        template_key=template.key,
        query=query,
        citations=cited_urls,
        used_citations=used,
        min_citations=effective_minimum,
        policy=policy,
        source_texts=None,  # 时效覆盖是检索问题，不在写作返工里判
        document_keys=document_keys,
    )
    hard += [finding.render() for finding in report.errors]
    soft += [finding.render() for finding in report.warnings]
    if effective_minimum < min_citations:
        soft.append(
            f"可用已核验来源只有 {available} 个，低于下限 {min_citations} 个"
            "（检索不足，需扩大检索而非改写）"
        )
    assessment = Assessment(hard=list(dict.fromkeys(hard)), soft=list(dict.fromkeys(soft)))
    if assessment.hard:
        assessment.brief = revision_brief(report, extra=[*hard[: len(hard) - len(report.errors)]])
    return assessment


def revision_prompt(previous: str, assessment: Assessment) -> str:
    """附在用户提示词末尾的返工材料：上一版全文 + 逐条问题。"""
    items = "\n".join(f"- {line}" for line in assessment.hard[:20])
    advice = "\n".join(f"- （建议）{line}" for line in assessment.soft[:5])
    return (
        "\n\n## 返工要求\n"
        "上一版未通过交付质量检查。请输出修订后的**完整全文**（不是修改说明），"
        "保持所有引用角标与数字忠实于上面的已核验素材；素材无法支持的内容删除或写明"
        "「素材未覆盖」，不得为凑数引入素材外的文献或事实。需要解决的问题：\n"
        f"{items}\n" + (f"{advice}\n" if advice else "") + "\n## 上一版全文\n" + previous
    )


async def write_with_revisions(
    write: Callable[[str | None], Awaitable[str]],
    assess: Callable[[str], Assessment | Awaitable[Assessment]],
    *,
    max_revisions: int,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> tuple[str, RevisionLog]:
    """执行返工循环，返回问题最少的一版正文与返工记录。

    ``write(revision)``：revision 为 None 表示首稿，否则是附加到提示词的返工材料。
    写作本身抛出的异常（预算耗尽等）向上传播给调用方，已有的最好版本不会丢失——
    调用方在首稿之后失败时，本函数返回已有最好版本。
    """
    log = RevisionLog()
    best: tuple[int, str, Assessment] | None = None
    revision: str | None = None
    for attempt in range(max_revisions + 1):
        try:
            body = await write(revision)
        except LeaseLostError:
            raise  # 租约被接管不是写作失败：交回已有版本会让失去租约的 worker 继续写盘
        except Exception:
            if best is None:
                raise
            break
        result = assess(body)
        assessment = await result if inspect.isawaitable(result) else result
        log.attempts = attempt + 1
        log.history.append({"attempt": attempt + 1, "hard": len(assessment.hard)})
        if best is None or len(assessment.hard) <= best[0]:
            best = (len(assessment.hard), body, assessment)
            log.chosen = attempt + 1
        if on_event is not None:
            on_event(
                "quality.check",
                {
                    "attempt": attempt + 1,
                    "hard": assessment.hard[:8],
                    "soft": assessment.soft[:5],
                },
            )
        if assessment.clean or not assessment.can_revise or attempt == max_revisions:
            break
        revision = revision_prompt(body, assessment)
    assert best is not None
    log.remaining = best[2].hard
    log.advisories = best[2].soft
    return best[1], log


__all__ = [
    "Assessment",
    "RevisionLog",
    "assess_draft",
    "revision_prompt",
    "write_with_revisions",
]
