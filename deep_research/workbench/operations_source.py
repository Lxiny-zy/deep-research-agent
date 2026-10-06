"""Read only operational metadata; never load report, prompt or reasoning bodies."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, true

from ..persistence import orm

MAX_ITEMS = 500
MAX_CALL_EVENTS = 10000


def _owner(column: Any, owner: str | None) -> Any:
    if owner is None:
        return true()
    if owner == "local":
        return or_(column == owner, column.is_(None))
    return column == owner


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


async def sql_snapshot(
    sessionmaker: Any, *, owner: str | None, since: datetime, until: datetime,
) -> dict[str, Any]:
    run, workflow = orm.ResearchRun, orm.WorkflowRunRow
    qa, conversation = orm.QaMessageRow, orm.QaConversationRow
    async with sessionmaker() as session:
        run_query = (
            select(
                run.id, run.status, run.created_at, run.elapsed,
                workflow.attempt,
                workflow.checkpoint["scratch"]["workbench"]["template"].as_string().label("scenario"),
                workflow.checkpoint["scratch"]["content_revision"]["parent_run_id"].as_string().label("parent"),
                workflow.checkpoint["scratch"]["_completion"].label("completion"),
            )
            .outerjoin(workflow, workflow.research_run_id == run.id)
            .where(_owner(run.owner_id, owner), run.created_at >= since, run.created_at <= until)
            .order_by(run.created_at.desc(), run.id)
            .limit(MAX_ITEMS + 1)
        )
        raw_runs = [dict(row) for row in (await session.execute(run_query)).mappings()]
        qa_query = (
            select(
                qa.id, qa.status, qa.created_at, qa.request_id,
                conversation.run_id,
                qa.request_payload["resume_message_id"].as_string().label("parent"),
                qa.request_payload["revision_message_id"].as_string().label("revision"),
            )
            .join(conversation, conversation.id == qa.conversation_id)
            .where(
                _owner(conversation.owner_id, owner),
                qa.created_at >= since, qa.created_at <= until,
            )
            .order_by(qa.created_at.desc(), qa.id)
            .limit(MAX_ITEMS + 1)
        )
        raw_qa = [dict(row) for row in (await session.execute(qa_query)).mappings()]
        runs, messages = raw_runs[:MAX_ITEMS], raw_qa[:MAX_ITEMS]
        run_ids, message_ids = [r["id"] for r in runs], [q["id"] for q in messages]
        calls: list[dict[str, Any]] = []
        if run_ids:
            rows = await session.execute(
                select(orm.EventRow.data["model_call"])
                .where(
                    orm.EventRow.run_id.in_(run_ids),
                    orm.EventRow.data["model_call"].as_string().is_not(None),
                )
                .order_by(orm.EventRow.run_id, orm.EventRow.seq)
                .limit(MAX_CALL_EVENTS + 1)
            )
            calls.extend(_object(row[0]) for row in rows)
        if message_ids:
            # Project only model_call entries inside the database. Fetching all
            # thoughts here would also read private reasoning and large text.
            dialect = session.bind.dialect.name
            tool: Any
            payload: Any
            if dialect == "postgresql":
                entries = func.json_array_elements(qa.thoughts).table_valued("value")
                tool = entries.c.value.op("->>")("tool")
                payload = entries.c.value.op("->")("call")
            else:
                entries = func.json_each(qa.thoughts).table_valued("key", "value")
                tool = func.json_extract(entries.c.value, "$.tool")
                payload = func.json_extract(entries.c.value, "$.call")
            rows = await session.execute(
                select(payload).select_from(qa).join(entries, true())
                .where(qa.id.in_(message_ids), tool == "model_call")
                .limit(MAX_CALL_EVENTS + 1)
            )
            calls.extend(_object(row[0]) for row in rows)
            rows = await session.execute(
                select(orm.QaStreamEventRow.payload["model_call"])
                .where(
                    orm.QaStreamEventRow.message_id.in_(message_ids),
                    orm.QaStreamEventRow.kind == "model_call",
                )
                .order_by(orm.QaStreamEventRow.message_id, orm.QaStreamEventRow.sequence)
                .limit(MAX_CALL_EVENTS + 1)
            )
            calls.extend(_object(row[0]) for row in rows)
        rendering = []
        if run_ids:
            rows = await session.execute(
                select(
                    orm.RenderJobRow.status, orm.RenderJobRow.queued_at,
                    orm.RenderJobRow.attempts, orm.RenderJobRow.stalls,
                ).where(orm.RenderJobRow.run_id.in_(run_ids)).limit(MAX_ITEMS + 1)
            )
            rendering = [dict(row) for row in rows.mappings()]
    return {
        "backend": "database", "runs": runs, "qa": messages,
        "calls": calls[:MAX_CALL_EVENTS], "rendering": rendering[:MAX_ITEMS],
        "truncated": len(raw_runs) > MAX_ITEMS or len(raw_qa) > MAX_ITEMS
        or len(calls) > MAX_CALL_EVENTS or len(rendering) > MAX_ITEMS,
        "unknown_dates": 0,
    }


async def memory_snapshot(
    repo: Any, qa_store: Any, *, owner: str | None, since: datetime, until: datetime,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    unknown_dates = 0

    def in_window(created: datetime | None) -> bool:
        nonlocal unknown_dates
        if created is None:
            unknown_dates += 1
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return since <= created <= until

    summaries = await repo.list_runs(
        limit=MAX_ITEMS + 1, owner_id=None if owner == "local" else owner,
    )
    for summary in summaries[:MAX_ITEMS]:
        if owner == "local" and summary.owner_id not in {None, "local"}:
            continue
        if not in_window(summary.created_at):
            continue
        detail = await repo.get_run(summary.id)
        if detail is None:
            continue
        execution = detail.orchestration
        scratch = execution.checkpoint.get("scratch", {}) if execution else {}
        runs.append({
            "id": detail.id, "status": detail.status, "created_at": detail.created_at,
            "elapsed": detail.elapsed, "attempt": execution.attempt if execution else None,
            "scenario": scratch.get("workbench", {}).get("template"),
            "parent": scratch.get("content_revision", {}).get("parent_run_id"),
            "completion": scratch.get("_completion"),
        })
        calls.extend((event.data or {}).get("model_call", {}) for event in detail.events)
    # The in-memory adapter already owns the objects; inspect only selected
    # metadata fields, rather than serializing conversations or private bodies.
    for conversation in reversed(getattr(qa_store, "_items", {}).values()):
        if len(messages) > MAX_ITEMS:
            break
        if owner is not None and conversation.owner_id != owner:
            continue
        for message in reversed(conversation.messages):
            if len(messages) > MAX_ITEMS:
                break
            if not in_window(message.created_at):
                continue
            messages.append({
                "id": message.id, "status": message.status, "created_at": message.created_at,
                "request_id": message.request_id, "run_id": conversation.run_id,
                "parent": message.request_payload.get("resume_message_id"),
                "revision": message.request_payload.get("revision_message_id"),
            })
            calls.extend(
                t.get("call", {}) for t in message.thoughts if t.get("tool") == "model_call"
            )
            calls.extend(
                payload.get("model_call", {})
                for _, kind, payload in qa_store._stream_events.get(message.id, [])
                if kind == "model_call"
            )
    return {
        "backend": "memory", "runs": runs[:MAX_ITEMS], "qa": messages[:MAX_ITEMS],
        "calls": calls[:MAX_CALL_EVENTS], "rendering": None,
        "truncated": len(summaries) > MAX_ITEMS or len(messages) > MAX_ITEMS
        or len(calls) > MAX_CALL_EVENTS,
        "unknown_dates": unknown_dates,
    }
