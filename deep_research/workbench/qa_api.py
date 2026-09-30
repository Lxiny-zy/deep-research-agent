"""学术问答 HTTP 接口：会话的增删查与「提问一轮」。

提问是同步请求（一次检索 + 一次作答，通常十秒量级），不走 run / worker 队列：
问答没有需要崩溃恢复的长流程，引入租约与 checkpoint 只会增加复杂度。
每次提问仍受全局的创建限流保护，并把问题长度限制在 2000 字以内。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..http.auth import principal_for
from .qa import MAX_HISTORY_TURNS, answer_question
from .qa_store import ConversationFullError, InMemoryQaStore, QaConversation, QaMessage, QaStore

router = APIRouter(prefix="/api/qa", tags=["qa"])


class CreateConversation(BaseModel):
    title: str = Field("", max_length=200)
    # 论文精读工作区的对话绑定所属任务；为空即学术问答页的独立会话
    run_id: str | None = Field(None, max_length=64)


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # 仅对绑定任务的精读对话生效：本论文始终参与，这里勾选额外叠加的来源
    sources: list[Literal["web", "library"]] = Field(default_factory=list, max_length=2)
    project_id: str | None = Field(None, max_length=64)


def _store(request: Request) -> QaStore:
    store = getattr(request.app.state, "qa_store", None)
    if store is None:
        store = InMemoryQaStore()
        request.app.state.qa_store = store
    return store


def _serialize(conversation: QaConversation) -> dict[str, Any]:
    data = asdict(conversation)
    for key in ("created_at", "updated_at"):
        if data.get(key) is not None:
            data[key] = data[key].isoformat()
    for message in data.get("messages", []):
        if message.get("created_at") is not None:
            message["created_at"] = message["created_at"].isoformat()
    data.pop("owner_id", None)
    return data


async def _check_run(request: Request, run_id: str) -> None:
    """精读对话绑定的任务必须对当前身份可见（与 run 路由相同的归属规则）。"""
    principal = principal_for(request)
    owner = await request.app.state.repo.get_run_owner(run_id)
    detail = await request.app.state.repo.get_run(run_id) if principal.can_manage else None
    if (principal.can_manage and detail is None) or (
        not principal.can_manage and owner != principal.id
    ):
        raise HTTPException(404, "run not found")


async def _owned(request: Request, conversation_id: str) -> QaConversation:
    conversation = await _store(request).get(conversation_id)
    principal = principal_for(request)
    if conversation is None or (not principal.can_manage and conversation.owner_id != principal.id):
        raise HTTPException(404, "conversation not found")
    return conversation


@router.get("/conversations")
async def list_conversations(
    request: Request, run_id: str | None = Query(None, max_length=64)
) -> list[dict[str, Any]]:
    principal = principal_for(request)
    if run_id is not None:
        await _check_run(request, run_id)
    items = await _store(request).list(None if principal.can_manage else principal.id, run_id)
    return [_serialize(item) for item in items]


@router.post("/conversations", status_code=201)
async def create_conversation(body: CreateConversation, request: Request) -> dict[str, Any]:
    principal = principal_for(request)
    if not principal.can_research:
        raise HTTPException(403, "当前身份为只读，无法发起问答")
    if body.run_id is not None:
        await _check_run(request, body.run_id)
    conversation = await _store(request).create(
        principal.id, body.title or "新的学术问答", run_id=body.run_id
    )
    return _serialize(conversation)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    return _serialize(await _owned(request, conversation_id))


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request) -> None:
    await _owned(request, conversation_id)
    await _store(request).delete(conversation_id)


@router.post("/conversations/{conversation_id}/messages", status_code=201)
async def ask(conversation_id: str, body: AskRequest, request: Request) -> dict[str, Any]:
    from .. import api as api_module

    principal = principal_for(request)
    if not principal.can_research:
        raise HTTPException(403, "当前身份为只读，无法发起问答")
    conversation = await _owned(request, conversation_id)
    await api_module._check_rate_limit(request)
    history = [
        {"query": message.query, "answer": message.answer}
        for message in conversation.messages[-MAX_HISTORY_TURNS:]
    ]
    settings = request.app.state.settings
    scope = await _paper_scope(request, conversation, body)
    agent, search_tool = await api_module._build_agent(request.app, settings)
    try:
        ctx = await agent.one_shot_context()
        result = await answer_question(body.query, history=history, ctx=ctx, **scope)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, {"code": "qa_failed", "message": f"问答失败：{exc}"}) from exc
    finally:
        await agent.aclose()
        if search_tool is not None:
            await search_tool.aclose()
    evidence = [
        {
            "statement": finding.statement,
            "source_url": finding.source_url,
            "evidence_quote": finding.evidence_quote,
            "source_title": finding.verification.source_title,
            "source_reference": finding.verification.source_reference,
            "origin": result.origins.get(finding.source_url, "web"),
        }
        for finding in result.findings[:30]
    ]
    try:
        stored = await _store(request).append(
            conversation_id,
            QaMessage(
                id="",
                position=0,
                query=body.query,
                answer=result.answer,
                citations=result.citations,
                evidence=evidence,
                thoughts=result.thoughts,
                status="fallback" if result.fallback else "done",
            ),
        )
    except ConversationFullError as exc:
        raise HTTPException(409, str(exc)) from exc
    message = asdict(stored)
    if message.get("created_at") is not None:
        message["created_at"] = message["created_at"].isoformat()
    message["tokens"] = agent.tracer.total_tokens
    return message


async def _paper_scope(
    request: Request, conversation: QaConversation, body: AskRequest
) -> dict[str, Any]:
    """精读对话的证据范围：本论文片段 + 用户勾选的资料库 / 联网检索。"""
    if conversation.run_id is None:
        return {}
    from .reader import paper_sources

    await _check_run(request, conversation.run_id)
    detail = await request.app.state.repo.get_run(conversation.run_id)
    if detail is None:
        raise HTTPException(404, "run not found")
    scope: dict[str, Any] = {
        "paper_sources": paper_sources(detail),
        "include_web": "web" in body.sources,
    }
    if "library" in body.sources:
        if not body.project_id:
            raise HTTPException(422, "勾选资料库时请选择一个资料库项目")
        from ..library.api import _owned_project
        from ..library.search import ProjectCorpusSearch

        project = await _owned_project(request, body.project_id)
        scope["extra_search"] = ProjectCorpusSearch(
            request.app.state.library, project.id, project.owner_id
        )
    return scope


__all__ = ["router"]
