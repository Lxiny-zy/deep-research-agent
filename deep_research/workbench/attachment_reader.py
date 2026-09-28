"""附件阅读角色：让模型读取用户上传的文件，并把结论变成可逐字核验的发现。

它是所有任务工作流的第一步（没有附件时什么都不做、零开销）。读到的内容与检索来源、
指定论文完全同权：同样经过来源门禁、逐字核验与语义核验，写作阶段按同一套 [n]
引用编号标注；交付物里的参考来源会显示为文件名与定位（「第 3 页」「第 2 张幻灯片」）。

批量阅读：每 4 个片段一次抽取调用。整个文件塞进一次调用会超出上下文，也会让模型
只盯住开头；分批让每一段都被读到。
"""

from __future__ import annotations

from ..agents.base import Blackboard, RunContext
from ..agents.researcher import Researcher
from ..guardrails import verify_claim_consistency
from ..models import ResearchResult
from ..registry import register
from .attachments import attachments_from_scratch
from .contract import contract_from_scratch
from .intake import _FixedSources

ATTACHMENT_READER_ROLE = "attachment_reader"
_BATCH = 4


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
            for index in range(0, len(sources), _BATCH):
                batch = sources[index : index + _BATCH]
                researcher.search = _FixedSources(batch)
                try:
                    result = await researcher.run(question)
                except Exception as exc:  # 单批失败隔离：其余片段照常阅读
                    ctx.tracer.emit(
                        "RESEARCHER",
                        "error",
                        f"阅读「{attachment.filename}」第 {index // _BATCH + 1} 批失败：{exc}",
                    )
                    continue
                if result is not None and result.findings:
                    bb.results.append(
                        ResearchResult(
                            sub_question=f"上传文件「{attachment.filename}」"
                            f"（第 {index // _BATCH + 1} 组片段）",
                            findings=result.findings,
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
