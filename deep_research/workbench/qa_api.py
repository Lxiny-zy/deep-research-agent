"""学术问答 HTTP 接口：会话的增删查与「提问一轮」。

提问通过 SSE 推送正文增量，空闲期间每 15 秒发送 keep-alive；
完成后返回核验并持久化的消息，替换前端的生成中正文。
客户端断线不会取消后台任务，刷新会话仍可读到结果；旧的同步 JSON 接口保留给兼容调用方。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, replace
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import StreamingResponse

from ..http.auth import principal_for
from ..observability import Event
from .qa import answer_question
from .qa_admission import QaLimits, QaQueueFull
from .qa_cache import PaperEvidenceCache
from .qa_checkpoint import (
    MAX_CHECKPOINT_BYTES,
    CheckpointWriter,
    QaCheckpoint,
    environment_hash,
    read_checkpoint,
)
from .qa_jobs import start_turn
from .qa_requests import INTERRUPTED, MemoryQaRequests, RequestConflict, SqlQaRequests
from .qa_store import (
    ConversationFullError,
    InMemoryQaStore,
    QaConversation,
    QaMessage,
    QaStore,
    SqlQaStore,
    message_payload,
)

router = APIRouter(prefix="/api/qa", tags=["qa"])
_SSE_HEARTBEAT_SECONDS = 15.0


class CreateConversation(BaseModel):
    title: str = Field("", max_length=200)
    # 论文与开放研究的对话绑定所属任务；为空即学术问答页的独立会话
    run_id: str | None = Field(None, max_length=64)


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # 普通问答中来源全部可选；绑定任务的对话另有固定的任务来源
    sources: list[Literal["web", "library"]] = Field(default_factory=list, max_length=2)
    project_id: str | None = Field(None, max_length=64)
    request_id: str | None = Field(None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    revision_message_id: str | None = Field(None, min_length=1, max_length=64)
    resume_message_id: str | None = Field(None, min_length=1, max_length=64)

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


def _store(request: Request) -> QaStore:
    store = getattr(request.app.state, "qa_store", None)
    if store is None:
        store = InMemoryQaStore()
        request.app.state.qa_store = store
    return store


def _serialize(conversation: QaConversation) -> dict[str, Any]:
    data = asdict(replace(conversation, messages=[]))
    for key in ("created_at", "updated_at"):
        if data.get(key) is not None:
            data[key] = data[key].isoformat()
    data["messages"] = [message_payload(message) for message in conversation.messages]
    data.pop("owner_id", None)
    return data


def _requests(request: Request) -> Any:
    store = _store(request)
    requests = getattr(request.app.state, "qa_requests", None)
    if requests is None or getattr(request.app.state, "qa_requests_store", None) is not store:
        limits = QaLimits.from_settings(request.app.state.settings)
        if isinstance(store, SqlQaStore):
            requests = SqlQaRequests(store._sm, limits)
        elif isinstance(store, InMemoryQaStore):
            requests = MemoryQaRequests(store, limits)
        else:
            raise TypeError("问答存储未实现请求生命周期")
        request.app.state.qa_requests = requests
        request.app.state.qa_requests_store = store
    return requests


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
    runtime = await _prepare_turn(conversation_id, body, request)
    result = await asyncio.shield(runtime.task)
    if result["status"] == "error":
        raise HTTPException(
            502,
            {
                "code": "qa_request_failed",
                "message": result["error"],
                "request_id": result["request_id"],
            },
        )
    return result


async def _prepare_turn(cid: str, body: AskRequest, request: Request) -> Any:
    from .. import api as api_module

    principal = principal_for(request)
    if not principal.can_research:
        raise HTTPException(403, "当前身份为只读，无法发起问答")
    conversation = await _owned(request, cid)
    request_id = body.request_id or str(uuid4())
    body = body.model_copy(update={"request_id": request_id, "sources": sorted(set(body.sources))})
    payload = body.model_dump(exclude={"request_id"}, mode="json")
    if body.revision_message_id is None:
        # Preserve hashes of requests reserved before revision support existed.
        payload.pop("revision_message_id", None)
    if body.resume_message_id is None:
        payload.pop("resume_message_id", None)
    digest = hashlib.sha256(
        json.dumps({"actor": principal.id, **payload}, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    requests = _requests(request)
    existing = await requests.get(cid, request_id)
    if existing is None:
        await api_module._check_rate_limit(request)
        if body.resume_message_id is not None:
            await _resume_state(request, conversation, body)
        elif body.revision_message_id is not None:
            await _revision_seed(request, conversation, body)
        else:
            await _paper_scope(request, conversation, body)
    try:
        await requests.reserve(cid, request_id, digest, {**payload, "_actor": principal.id})
    except QaQueueFull as exc:
        raise HTTPException(
            429, {"code": "qa_queue_full", "message": str(exc)}, headers={"Retry-After": "5"}
        ) from exc
    except (RequestConflict, ConversationFullError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "conversation not found") from exc

    async def execute(**callbacks: Any) -> dict[str, Any]:
        return await _answer(cid, body, request, **callbacks)

    from ..execution_policy import attempt_seconds

    return start_turn(
        request.app,
        requests,
        cid,
        request_id,
        execute,
        attempt_seconds(request.app.state.settings, "qa"),
        checkpoints=True,
    )


@router.get("/conversations/{conversation_id}/requests/{request_id}")
async def request_status(conversation_id: str, request_id: str, request: Request) -> dict[str, Any]:
    await _owned(request, conversation_id)
    row = await _requests(request).get(conversation_id, request_id)
    if row is None:
        raise HTTPException(404, "request not found")
    return message_payload(row)


@router.post("/conversations/{conversation_id}/requests/{request_id}/cancel")
async def cancel_request(conversation_id: str, request_id: str, request: Request) -> dict[str, Any]:
    await _owned(request, conversation_id)
    if not principal_for(request).can_research:
        raise HTTPException(403, "当前身份为只读，无法停止问答")
    row = await _requests(request).cancel(conversation_id, request_id)
    if row is None:
        raise HTTPException(404, "request not found")
    runtime = getattr(request.app.state, "qa_live_turns", {}).get((conversation_id, request_id))
    if row.status == "cancelled" and runtime is not None and runtime.work is not None:
        runtime.stop_requested.set()
        runtime.work.cancel()
    return message_payload(row)


async def _answer(
    conversation_id: str,
    body: AskRequest,
    request: Request,
    *,
    on_delta: Callable[[str], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    on_checkpoint: CheckpointWriter | None = None,
) -> dict[str, Any]:
    from .. import api as api_module

    principal = principal_for(request)
    if not principal.can_research:
        raise HTTPException(403, "当前身份为只读，无法发起问答")
    conversation = await _owned(request, conversation_id)
    history_messages = [
        message for message in conversation.messages if message.status in {"done", "fallback"}
    ]
    settings = request.app.state.settings
    scope: dict[str, Any]
    if body.revision_message_id is not None:
        scope = {"revision_seed": await _revision_seed(request, conversation, body)}
        parent_index = next(
            i
            for i, message in enumerate(conversation.messages)
            if message.id == body.revision_message_id
        )
        history_messages = [
            message
            for message in conversation.messages[:parent_index]
            if message.status in {"done", "fallback"}
        ]
    else:
        scope = await _paper_scope(request, conversation, body)
    history = [
        {
            "id": message.id,
            "position": message.position,
            "query": message.query,
            "answer": message.answer,
        }
        for message in history_messages
    ]
    environment = await _recovery_environment(request, body, scope, history)
    if body.resume_message_id is not None:
        scope["resume_checkpoint"] = await _resume_state(request, conversation, body)
    execution_context = ""

    async def save_checkpoint(raw: dict[str, Any]) -> None:
        if on_checkpoint is None:
            return
        saved = (
            QaCheckpoint.model_validate(raw)
            .model_copy(update={"environment": environment, "execution_context": execution_context})
            .sealed()
        )
        if len(json.dumps(saved, ensure_ascii=False).encode()) > MAX_CHECKPOINT_BYTES:
            if on_event:
                on_event(
                    {"type": "status", "message": "本轮材料较大，阶段快照未保存；原始资料仍保留。"}
                )
            return
        await on_checkpoint(saved)

    previous_memory = next(
        (
            thought["memory"]
            for message in reversed(history_messages)
            for thought in reversed(message.thoughts)
            if thought.get("tool") == "conversation_memory"
            and isinstance(thought.get("memory"), dict)
        ),
        None,
    )
    if previous_memory is not None:
        scope["conversation_memory"] = previous_memory
    if conversation.run_id is not None and body.revision_message_id is None:
        cache = getattr(request.app.state, "paper_evidence_cache", None)
        if cache is None:
            cache = PaperEvidenceCache()
            request.app.state.paper_evidence_cache = cache
        scope.update(paper_cache=cache, cache_scope=f"{principal.id}/{conversation.run_id}")
    agent, search_tool = await api_module._build_agent(request.app, settings)
    from ..call_budget import ModelCallBudget, ModelCallLimitExceeded
    from .quality import coerce_policy

    call_budget = ModelCallBudget(coerce_policy(settings.quality).qa_max_model_calls)
    agent.tracer.call_budget = call_budget
    agent.tracer.cache_scope = f"qa:{principal.id}:{conversation.run_id or conversation.id}"
    reasoning: dict[str, dict[str, Any]] = {}
    usages: list[dict[str, Any]] = []

    def observe(event: Event) -> None:
        data = event.data or {}
        delta = data.get("reasoning_delta")
        call_id = data.get("call_id")
        if isinstance(delta, str) and isinstance(call_id, str):
            thought = reasoning.setdefault(
                call_id,
                {
                    "tool": "model_reasoning",
                    "input": str(data.get("model", "")),
                    "observation": "",
                    "call_id": call_id,
                },
            )
            thought["observation"] += delta
            if on_event:
                on_event({"type": "reasoning", **data})
        elif isinstance(data.get("llm_usage"), dict):
            usages.append(
                {"tool": "model_usage", "input": "", "observation": "", "usage": data["llm_usage"]}
            )
            if on_event:
                on_event({"type": "usage", "llm_usage": data["llm_usage"]})
        elif event.type == "start" and on_event:
            # Trace messages retain internal query/context for auditing. Product
            # status text must not render that prompt or the conversation history.
            message = (
                "正在检索与本轮问题相关的资料…"
                if event.stage == "RESEARCHER"
                else "正在处理本轮问题…"
            )
            on_event({"type": "status", "message": message})

    agent.tracer.add_sink(observe)
    try:
        ctx = await agent.one_shot_context()
        roles = ("planner", "researcher", "synthesizer", "evidence_verifier")
        runtime = getattr(agent, "_catalog_runtime", None)
        execution_context = environment_hash(
            {
                "settings": asdict(ctx.settings),
                "global_rules": getattr(ctx, "global_rules", None),
                "catalog": runtime.snapshot(roles).model_dump(mode="json")
                if runtime is not None
                else None,
                "models": [
                    {
                        "role": role,
                        **{
                            key: getattr(ctx.llm_for(role), key, None)
                            for key in (
                                "model",
                                "base_url",
                                "parameter_mode",
                                "context_window_tokens",
                                "reasoning_effort",
                            )
                        },
                    }
                    for role in roles
                ],
            }
        )
        if (
            body.resume_message_id is not None
            and scope["resume_checkpoint"].get("execution_context") != execution_context
        ):
            raise HTTPException(
                409,
                {
                    "code": "qa_recovery_unavailable",
                    "message": "实际模型或角色配置已变化，原阶段结果不可继续",
                },
            )
        result = await answer_question(
            body.query,
            history=history,
            ctx=ctx,
            on_delta=on_delta,
            on_event=on_event,
            on_checkpoint=save_checkpoint,
            **scope,
        )
    except HTTPException:
        raise
    except ModelCallLimitExceeded as exc:
        from .qa import QaAnswer

        result = QaAnswer(answer=str(exc), citations=[], findings=[], fallback=True)
        if on_event:
            on_event({"type": "status", "message": str(exc)})
    except Exception as exc:
        raise HTTPException(502, {"code": "qa_failed", "message": f"问答失败：{exc}"}) from exc
    finally:
        agent.tracer.remove_sink(observe)
        await agent.aclose()
        if search_tool is not None:
            await search_tool.aclose()
    from .support import evidence_id

    if call_budget.rejected and result.fallback and "调用上限" not in result.answer:
        result.answer = (
            f"本轮已达到模型调用上限（{call_budget.limit} 次），以下只保留可核验内容。\n\n"
            + result.answer
        )
    result.thoughts.append(
        {
            "tool": "model_budget",
            "input": "",
            "observation": f"本轮使用 {call_budget.used}/{call_budget.limit} 次模型请求。",
            "used": call_budget.used,
            "limit": call_budget.limit,
            "stopped": call_budget.rejected,
        }
    )

    evidence = [
        {
            "support_id": evidence_id(finding),
            "statement": finding.statement,
            "source_url": finding.source_url,
            "evidence_quote": finding.evidence_quote,
            "source_title": finding.verification.source_title,
            "source_reference": finding.verification.source_reference,
            "origin": result.origins.get(finding.source_url, "web"),
        }
        for finding in result.findings
    ]
    from .qa_revision_state import PRIVATE_REVISION_TOOL

    private = (
        [{"tool": PRIVATE_REVISION_TOOL, "state": result.revision_state}]
        if result.revision_state is not None
        else []
    )
    stored = QaMessage(
        id="",
        position=0,
        query=body.query,
        answer=result.answer,
        citations=result.citations,
        evidence=evidence,
        thoughts=[*result.thoughts, *reasoning.values(), *usages, *private],
        status="fallback" if result.fallback else "done",
        tokens=agent.tracer.total_tokens,
    )
    return message_payload(stored, include_private=True)


@router.post("/conversations/{conversation_id}/messages/stream")
async def ask_stream(conversation_id: str, body: AskRequest, request: Request) -> StreamingResponse:
    """Run a normal persisted QA turn while keeping idle HTTP connections alive."""
    runtime = await _prepare_turn(conversation_id, body, request)
    task = runtime.task

    async def events() -> AsyncIterator[str]:
        queue = runtime.attach()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                kind, payload = item
                yield (f"event: {kind}\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n")
            try:
                message = task.result()
            except asyncio.CancelledError:
                payload = {
                    "status": 503,
                    "detail": {"code": "qa_interrupted", "message": INTERRUPTED},
                }
                yield "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            except HTTPException as exc:
                payload = {"status": exc.status_code, "detail": exc.detail}
                yield "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            except Exception as exc:
                payload = {"status": 502, "detail": {"code": "qa_failed", "message": str(exc)}}
                yield "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            else:
                # The final, validated and persisted answer replaces provisional text.
                if message["status"] == "error":
                    payload = {
                        "status": 502,
                        "detail": {"code": "qa_request_failed", "message": message["error"]},
                    }
                    yield "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                else:
                    yield (
                        "event: complete\ndata: " + json.dumps(message, ensure_ascii=False) + "\n\n"
                    )
        finally:
            runtime.listeners.discard(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


class RevisionRequest(BaseModel):
    request_id: str | None = Field(None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


async def _revision_seed(
    request: Request, conversation: QaConversation, body: AskRequest
) -> dict[str, Any]:
    from .qa_revision_state import legacy_revision_state, read_revision_state, stored_revision_state

    parent = next(
        (message for message in conversation.messages if message.id == body.revision_message_id),
        None,
    )
    if parent is None:
        raise HTTPException(404, "原回答不存在")
    if conversation.run_id is not None:
        await _check_run(request, conversation.run_id)
    if parent.status != "fallback" or body.query != parent.query or body.sources or body.project_id:
        raise HTTPException(409, "只能复用未通过回答的原问题和材料继续修订")
    try:
        raw = stored_revision_state(parent.thoughts)
        if raw is not None:
            return read_revision_state(raw).sealed()
        if conversation.run_id is None:
            return legacy_revision_state(parent, sources=[])
        from .qa_scope import task_qa_scope

        detail = await request.app.state.repo.get_run(conversation.run_id)
        if detail is None:
            raise ValueError("原任务已不可用")
        frozen = task_qa_scope(detail)
        return legacy_revision_state(
            parent,
            sources=frozen.sources,
            scoped=True,
            scope_kind=frozen.kind,
            scope_query=frozen.query,
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, {"code": "qa_revision_unavailable", "message": str(exc)}) from exc


async def _revision_request(
    cid: str, mid: str, body: RevisionRequest, request: Request
) -> AskRequest:
    conversation = await _owned(request, cid)
    parent = next((message for message in conversation.messages if message.id == mid), None)
    if parent is None:
        raise HTTPException(404, "原回答不存在")
    return AskRequest(query=parent.query, request_id=body.request_id, revision_message_id=mid)


@router.post("/conversations/{conversation_id}/messages/{message_id}/revise", status_code=201)
async def revise_answer(
    conversation_id: str, message_id: str, body: RevisionRequest, request: Request
) -> dict[str, Any]:
    prepared = await _revision_request(conversation_id, message_id, body, request)
    return await ask(conversation_id, prepared, request)


@router.post("/conversations/{conversation_id}/messages/{message_id}/revise/stream")
async def revise_answer_stream(
    conversation_id: str, message_id: str, body: RevisionRequest, request: Request
) -> StreamingResponse:
    prepared = await _revision_request(conversation_id, message_id, body, request)
    return await ask_stream(conversation_id, prepared, request)


async def _paper_scope(
    request: Request, conversation: QaConversation, body: AskRequest
) -> dict[str, Any]:
    """Resolve the explicit source scope for general and task-bound Q&A.

    General Q&A defaults to model knowledge.  The web search model and the
    private library are opt-in through ``sources``; task conversations always
    include their frozen source snapshots and may add either source.
    """
    if conversation.run_id is None:
        scope: dict[str, Any] = {"include_web": "web" in body.sources}
    else:
        from .qa_scope import task_qa_scope

        await _check_run(request, conversation.run_id)
        detail = await request.app.state.repo.get_run(conversation.run_id)
        if detail is None:
            raise HTTPException(404, "run not found")
        if detail.status not in {"done", "needs_review"}:
            raise HTTPException(
                409,
                {
                    "code": "paper_not_ready",
                    "message": "任务仍在执行或未成功完成，完成后才能基于任务提问",
                    "status": detail.status,
                },
            )
        task_scope = task_qa_scope(detail)
        if not task_scope.sources:
            raise HTTPException(
                409,
                {
                    "code": f"{task_scope.kind}_sources_unavailable",
                    "message": "这次任务没有可回查的原文材料，暂时不能基于任务提问",
                },
            )
        scope = {
            "paper_sources": task_scope.sources,
            "paper_evidence": task_scope.findings,
            "scope_kind": task_scope.kind,
            "scope_query": task_scope.query,
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


async def _recovery_environment(
    request: Request, body: AskRequest, scope: dict[str, Any], history: list[dict]
) -> str:
    from ..catalog.runtime import create_catalog_runtime_snapshot
    from ..guardrails import SemanticEvidenceVerifier
    from ..prompting import load_global_rules
    from .prose_review import SUPPORT_POLICY_VERSION

    catalog = getattr(request.app.state, "catalog", None)
    catalog_state = None
    if catalog is not None:
        cards = await catalog.list_agents()
        catalog_state = (
            await create_catalog_runtime_snapshot(
                catalog,
                [card.name for card in cards],
                request.app.state.settings,
            )
        ).model_dump(mode="json")
    library = []
    if "library" in body.sources and body.project_id:
        library = [
            source.model_dump(mode="json")
            for source in await request.app.state.library.list_sources(body.project_id)
        ]
        library.sort(key=lambda source: source["id"])
    material = {
        key: [item.model_dump(mode="json") for item in value] if isinstance(value, list) else value
        for key, value in scope.items()
        if key
        in {
            "paper_sources",
            "paper_evidence",
            "scope_kind",
            "scope_query",
            "include_web",
            "revision_seed",
        }
    }
    return environment_hash(
        {
            "contract": 1,
            "support_policy": SUPPORT_POLICY_VERSION,
            "evidence_policy": environment_hash(SemanticEvidenceVerifier._SYSTEM),
            "actor": principal_for(request).id,
            "request": body.model_dump(exclude={"request_id", "resume_message_id"}),
            "settings": asdict(request.app.state.settings),
            "catalog": catalog_state,
            "global_rules": load_global_rules(),
            "scope": material,
            "history": history,
            "library": library,
        }
    )


async def _resume_state(
    request: Request, conversation: QaConversation, body: AskRequest
) -> dict[str, Any]:
    parent = next(
        (message for message in conversation.messages if message.id == body.resume_message_id), None
    )
    if parent is None:
        raise HTTPException(404, "原问题不存在")
    if parent.request_id:
        parent = await _requests(request).get(conversation.id, parent.request_id)
    try:
        if parent is None or parent.status not in {"error", "cancelled"}:
            raise ValueError("只能继续已停止或中断的问题")
        original = AskRequest(**parent.request_payload)
        if body.model_dump(exclude={"request_id", "resume_message_id"}) != original.model_dump(
            exclude={"request_id", "resume_message_id"}
        ):
            raise ValueError("继续时须保留原问题和来源范围")
        checkpoint = read_checkpoint(parent.request_payload.get("_checkpoint"))
        scope = (
            {"revision_seed": await _revision_seed(request, conversation, body)}
            if body.revision_message_id
            else await _paper_scope(request, conversation, body)
        )
        candidates = conversation.messages
        if body.revision_message_id:
            parent_index = next(
                i for i, message in enumerate(candidates) if message.id == body.revision_message_id
            )
            candidates = candidates[:parent_index]
        history = [
            {
                "id": message.id,
                "position": message.position,
                "query": message.query,
                "answer": message.answer,
            }
            for message in candidates
            if message.status in {"done", "fallback"}
        ]
        if checkpoint.environment != await _recovery_environment(request, body, scope, history):
            raise ValueError("模型配置、来源范围或对话已变化，原阶段结果不可继续；请重新提问")
        return checkpoint.sealed()
    except ValueError as exc:
        raise HTTPException(409, {"code": "qa_recovery_unavailable", "message": str(exc)}) from exc


__all__ = ["router"]
