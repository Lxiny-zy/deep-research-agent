"""Interactive source-grounded answers: local context selection and one generation."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..blocking import run_blocking
from ..call_budget import ModelCallBudget, ModelCallLimitExceeded
from ..context_budget import ContextBudget
from ..guardrails import SourcePolicy
from ..llm import InputCapacityError
from ..models import Source
from ..persistence.repository import LeaseLostError
from ..prompting import SCIENTIFIC_MARKDOWN, PrefixPrompt, leaf_system_prompt
from ..tools.base import SearchTool
from .conversation_memory import History, history_turns, valid_memory
from .qa_context import dialogue_context

DIRECT_MODEL_CALL_LIMIT = 2  # One answer; at most one pre-generation transport/protocol retry.
DIRECT_SEARCH_CALL_LIMIT = 3  # Model-backed retrieval and answer share this ceiling.


class _SearchCallBudget(ModelCallBudget):
    """Reserve the final request for answering, even with several search models."""

    def __init__(self, parent: ModelCallBudget) -> None:
        super().__init__(parent.limit)
        self.parent = parent

    def reserve(self) -> None:
        if self.parent.used >= self.parent.limit - 1:
            self.rejected = True
            raise ModelCallLimitExceeded("已为回答保留模型请求名额，停止追加检索请求。")
        self.parent.reserve()
        self.used = self.parent.used

_SYSTEM = (
    "你是论文阅读与学术问答助手。依据本轮提供的原文直接回答当前问题，用中文。"
    "先给出结论，再解释关键理由；分析创新点或原理时，说明方法做了什么、为什么有效、"
    "创新体现在哪里。用户未要求时，不展开成完整研究报告或核查清单。"
    "可以解释和推导，但须区分作者报告的事实、你的分析判断与尚不确定的内容；"
    "‘最好’等评价应说明判断依据，不能冒充论文证明的客观排名。"
    "事实引用使用原文标题前的 [n] 编号，不沿用论文自身参考文献号或历史回答中的编号；"
    "不编造引用、数值、实验结果或原文未说明的必要条件。"
    "本轮材料可能是节选，不能把未看到的内容断言为全文不存在。"
    "历史对话只用于理解主题、指代和用户要求，不是原文证据。"
    "这是直接问答，不生成执行计划，不宣称已完成独立事实核验，不要求重复上传已有原文。"
    + SCIENTIFIC_MARKDOWN
)

_SEARCH_HINTS = (
    (r"创新|贡献|核心|最好", "contribution novel novelty propose proposed method"),
    (r"原理|方法|流程|实现|公式|推导", "method mechanism architecture framework formulation model"),
    (r"结果|实验|性能|效果|指标", "experiment evaluation results performance comparison"),
    (r"局限|不足|缺点|限制", "limitation discussion failure future work"),
)


@dataclass(frozen=True)
class Passage:
    source: Source
    source_index: int
    offset: int
    text: str


def _format(
    passages: list[Passage], origins: dict[str, str], partial: bool
) -> tuple[str, list[str]]:
    urls = list(dict.fromkeys(p.source.url for p in passages))
    mapping = {url: i for i, url in enumerate(urls, 1)}
    blocks = [
        "【本轮原文节选；未覆盖全部材料，不能据此断言全文不存在某内容】"
        if partial else "【本轮提供的原文材料】"
    ]
    labels = {
        "paper": "本论文", "research": "本次任务", "library": "资料库", "web": "联网资料",
    }
    for passage in passages:
        source = passage.source
        label = labels.get(origins.get(source.url, "web"), "资料")
        blocks.append(
            f"<<<原文 [{mapping[source.url]}] 开始>>>\n"
            f"标题: [{mapping[source.url]}] {source.title}\n范围: {label}\n"
            f"定位: {source.locator or (source.scholarly.section if source.scholarly else '')}\n"
            f"URL: {source.url}\n{passage.text}\n<<<原文结束>>>"
        )
    return "\n\n".join(blocks), urls


def _select_passages(
    sources: list[Source], query: str, origins: dict[str, str],
    budget: ContextBudget, system: str, dynamic: str,
) -> tuple[list[Passage], str, list[str], bool]:
    full = [Passage(source, i, 0, source.content.strip()) for i, source in enumerate(sources)]
    context, urls = _format(full, origins, False)
    if budget.fits(system, PrefixPrompt(context, dynamic), reserve_tokens=128):
        return full, context, urls, False

    # Query expansion and ranking are local; choosing a passage is not a model task.
    from ..library.search import _lexemes, _terms

    expanded = query + " " + " ".join(
        hint for pattern, hint in _SEARCH_HINTS if re.search(pattern, query)
    )
    terms = set(_terms(expanded))
    pieces: list[Passage] = []
    for i, source in enumerate(sources):
        text = source.content.strip()
        for offset in range(0, len(text), 3000):
            start = max(0, offset - 150)
            pieces.append(Passage(source, i, start, text[start:offset + 3000]))

    def score(passage: Passage) -> tuple[int, int, int]:
        source = passage.source
        counts = Counter(_lexemes(source.title + " " + source.locator + " " + passage.text))
        value = sum(min(counts[term], 3) for term in terms)
        return value, -passage.source_index, -passage.offset

    selected: list[Passage] = []
    for passage in sorted(pieces, key=score, reverse=True):
        candidate = sorted([*selected, passage], key=lambda p: (p.source_index, p.offset))
        context, urls = _format(candidate, origins, True)
        if budget.fits(system, PrefixPrompt(context, dynamic), reserve_tokens=128):
            selected = candidate
    if not selected:
        raise InputCapacityError("本轮问题与原文片段无法放入模型上下文，请缩小范围或调整模型容量")
    context, urls = _format(selected, origins, True)
    return selected, context, urls, True


async def answer_direct_question(
    question: str, *, history: History, ctx: Any,
    paper_sources: list[Source] | None = None,
    include_web: bool = False, extra_search: SearchTool | None = None,
    scope_kind: str = "paper", scope_query: str = "",
    conversation_memory: dict[str, Any] | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    from .qa import (
        _CASUAL_REPLY,
        QaAnswer,
        _contextual_query,
        _is_casual_question,
        _knowledge_answer,
    )

    thoughts: list[dict[str, Any]] = [
        {
            "tool": "direct_answer", "input": "",
            "observation": "依据本轮材料直接回答，未启动独立核验或自动修订",
        }
    ]
    if _is_casual_question(question):
        return QaAnswer(answer=_CASUAL_REPLY, citations=[], findings=[], thoughts=thoughts)
    model = ctx.llm_for("synthesizer")
    budget = ContextBudget.from_model(model, ctx.settings.llm_max_input_chars)
    memory = valid_memory(conversation_memory, history_turns(history))
    query = _contextual_query(
        question, history, max_chars=min(8192, budget.input_capacity_chars // 3), memory=memory,
    )
    if paper_sources is None and not include_web and extra_search is None:
        return await _knowledge_answer(question, query, history, ctx, memory, thoughts, on_delta)

    sources = list(paper_sources or [])
    origins = {source.url: scope_kind for source in sources}
    backends = []
    if include_web:
        backends.append(await ctx.search_for("researcher"))
    if extra_search is not None:
        backends.append(extra_search)
    retrieval_incomplete = False
    if backends:
        from ..tools.composite import MultiBackendSearch

        if on_event:
            on_event({"type": "status", "message": "正在检索所选的附加资料…"})
        search_query = _contextual_query(question, history, max_chars=512, memory=memory)
        thoughts.append({
            "tool": "search_query", "input": "", "observation": "按本轮问题检索所选资料",
            "queries": [search_query],
        })
        search = (
            backends[0] if len(backends) == 1 else MultiBackendSearch(backends, tracer=ctx.tracer)
        )
        parent_budget = getattr(ctx.tracer, "call_budget", None)
        search_budget = _SearchCallBudget(parent_budget) if parent_budget is not None else None
        if search_budget is not None:
            ctx.tracer.call_budget = search_budget
        try:
            found = await search.search(search_query, max_results=ctx.settings.results_per_search)
        except LeaseLostError:
            raise
        except Exception:
            found = []
            retrieval_incomplete = True
        finally:
            ctx.tracer.call_budget = parent_budget
        retrieval_incomplete = retrieval_incomplete or bool(
            search_budget and search_budget.rejected
        )
        for source in found:
            if source.url in origins:
                continue
            origins[source.url] = (
                "library" if source.url.startswith("https://workspace.invalid/sources/")
                or "dr_source=" in source.url else "web"
            )
            sources.append(source)
        if retrieval_incomplete:
            thoughts.append({
                "tool": "search_status", "input": "",
                "observation": "附加检索未全部完成，本轮只依据已获得的材料回答。",
            })

    policy = SourcePolicy()
    admitted: dict[str, Source] = {}
    for source in sources:
        if source.content.strip() and policy.evaluate(source).allowed:
            admitted.setdefault(source.url, source)
    sources = list(admitted.values())
    if not sources:
        return QaAnswer(
            answer="附加检索未能返回可用材料，请稍后再试。" if retrieval_incomplete
            else "本轮没有可用的原文材料，暂时无法据此回答。",
            citations=[], findings=[],
            thoughts=thoughts, fallback=True,
        )

    scope_contract = (
        "这是本次任务的追问；材料可能来自多篇论文，逐项保留作者、来源和实验条件。"
        if scope_kind == "research" else
        "这是针对本论文的提问；附加来源必须与本论文的结果区分。"
        if paper_sources is not None else "依据用户选择的附加资料回答。"
    )
    system = leaf_system_prompt(_SYSTEM + scope_contract, getattr(ctx, "global_rules", None))
    task = f"【原任务主题】\n{scope_query}\n\n" if scope_kind == "research" and scope_query else ""
    history_chars = min(8192, max(0, budget.remaining(system, task, question) // 6))
    dialogue = dialogue_context(
        history, history_chars, memory=memory, require_complete_constraints=True,
    )
    dynamic = f"\n\n{task}{dialogue}\n\n【本轮问题】\n{question}"
    if retrieval_incomplete:
        dynamic += (
            "\n【本轮范围】附加检索未全部完成；只回答已有材料，"
            "不声称已查全或已得到最新结论。"
        )
    # Ranking/token estimation scans potentially long papers. Keep this CPU
    # work off the API event loop so unrelated questions and cancellation stay
    # responsive; the existing bounded worker pool limits concurrent work.
    passages, context, citations, partial = await run_blocking(
        _select_passages, sources, query, origins, budget, system, dynamic,
    )
    thoughts.append({
        "tool": "source_context", "input": "", "observation": (
            f"本轮使用 {len(passages)} 段相关原文节选" if partial
            else f"本轮使用 {len(sources)} 份已提供的原文材料"
        ),
    })
    if on_event:
        on_event({"type": "status", "message": "正在根据原文回答…"})
    parts: list[str] = []
    async for delta in model.stream(system, PrefixPrompt(context, dynamic), temperature=0.3):
        parts.append(delta)
        if on_delta:
            on_delta(delta)
    answer = "".join(parts).strip()
    evidence = [
        {
            "support_id": "source-" + hashlib.sha256(
                (p.source.url + p.text).encode()
            ).hexdigest()[:24],
            "statement": p.source.title or "本轮原文", "source_url": p.source.url,
            "evidence_quote": p.text, "source_title": p.source.title,
            "source_reference": " · ".join(filter(None, [p.source.title, p.source.locator])),
            "origin": origins[p.source.url],
        }
        for p in passages
    ]
    return QaAnswer(
        answer=answer or "模型未返回正文，请重新尝试本轮问题。", citations=citations,
        findings=[], source_evidence=evidence, origins=origins, thoughts=thoughts,
        fallback=not bool(answer),
    )
