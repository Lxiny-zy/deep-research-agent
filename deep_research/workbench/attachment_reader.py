"""附件阅读角色：让模型读取用户上传的文件，并把结论变成可逐字核验的发现。

它是所有任务工作流的第一步（没有附件时什么都不做、零开销）。读到的内容与检索来源、
指定论文完全同权：同样经过来源门禁、逐字核验与语义核验，写作阶段按同一套 [n]
引用编号标注；交付物里的参考来源会显示为文件名与定位（「第 3 页」「第 2 张幻灯片」）。

按模型容量组批，扣除真实系统规则、结构化输出 Schema、问题和修订空间；
保留完整片段及原始顺序，不以固定片段数量限制大上下文模型。
"""

from __future__ import annotations

from ..agents.base import Blackboard, RunContext, direct_system_prompt
from ..agents.researcher import Researcher, source_context
from ..guardrails import verify_claim_consistency
from ..models import ExtractedFindingList, Source
from ..persistence.repository import LeaseLostError
from ..prompting import structured_system_prompt
from ..registry import register
from .attachments import attachments_from_scratch
from .contract import contract_from_scratch
from .intake import _FixedSources

ATTACHMENT_READER_ROLE = "attachment_reader"


def source_batches(
    sources: list[Source], researcher: Researcher, question: str
) -> list[list[Source]]:
    capacity = getattr(
        researcher.llm, "input_capacity_chars", researcher.settings.llm_max_input_chars
    )
    rules = len(
        structured_system_prompt(direct_system_prompt(researcher.system), ExtractedFindingList)
    )
    available = max(0, capacity - rules - len(question) - 128)
    # Repair/framing space scales to the real remaining context, not a token budget.
    room = max(0, available - min(8192, max(256, available // 8)))
    batches: list[list[Source]] = []
    batch: list[Source] = []
    for source in sources:
        if len(source_context([source])) > room:
            raise ValueError("模型输入容量无法容纳一个完整附件片段，请调整模型容量；未截断原文")
        if batch and len(source_context([*batch, source])) > room:
            batches.append(batch)
            batch = []
        batch.append(source)
    if batch:
        batches.append(batch)
    return batches


def _question(query: str, focus: str) -> str:
    target = focus or query
    return f"依据上传文件回答任务，逐条抽取与任务相关的事实、数据、方法、结论与局限；任务：{target}"


@register(ATTACHMENT_READER_ROLE)
class AttachmentReader:
    name: str

    async def step(self, bb: Blackboard, ctx: RunContext) -> Blackboard:
        attachments = attachments_from_scratch(bb.scratch)
        if not attachments:
            return bb
        contract = contract_from_scratch(bb.scratch)
        question = _question(bb.query, contract.focus if contract is not None else "")
        total = sum(len(item.chunks) for item in attachments)
        ctx.tracer.emit(
            "RESEARCHER",
            "start",
            f"阅读上传文件：{len(attachments)} 个文件、{total} 个片段",
            data={
                "category": "attachments",
                "files": [item.summary() for item in attachments],
            },
        )
        researcher = Researcher()
        researcher.llm = ctx.llm_for("researcher")
        researcher.verification_llm = ctx.llm_for("evidence_verifier")
        researcher.tracer = ctx.tracer
        researcher.settings = ctx.settings
        researcher.system = ctx.system_prompt(researcher.system)
        before = len(bb.results)
        for attachment in attachments:
            sources = attachment.sources()
            batches = source_batches(sources, researcher, question)
            ctx.tracer.emit(
                "RESEARCHER",
                "info",
                f"「{attachment.filename}」按模型容量分为 {len(batches)} 批读取",
            )
            for index, batch in enumerate(batches, 1):
                researcher.search = _FixedSources(batch)
                try:
                    result = await researcher.run(question)
                except LeaseLostError:
                    raise  # 租约被接管：不能当作单批失败继续读下一批
                except Exception as exc:  # 单批失败隔离：其余片段照常阅读
                    ctx.tracer.emit(
                        "RESEARCHER",
                        "error",
                        f"阅读「{attachment.filename}」第 {index} 批失败：{exc}",
                    )
                    continue
                if result is not None and (result.findings or result.extraction_audit):
                    bb.results.append(
                        result.model_copy(
                            update={
                                "sub_question": (
                                    f"上传文件「{attachment.filename}」（第 {index} 组片段）"
                                )
                            }
                        )
                    )
        if len(bb.results) > before:
            await verify_claim_consistency(
                bb.results,
                researcher.consistency_verifier,
                researcher.verification_llm,
                ctx.tracer,
                stage="RESEARCHER",
            )
        gained = sum(len(r.findings) for r in bb.results[before:])
        ctx.tracer.emit(
            "RESEARCHER",
            "info",
            f"上传文件阅读完成：得到 {gained} 条待核验发现",
            data={"category": "attachments", "findings": gained},
        )
        return bb


__all__ = ["ATTACHMENT_READER_ROLE", "AttachmentReader"]
