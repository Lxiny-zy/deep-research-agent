"""Durable writing drafts and reviews outside the enclosing workflow step."""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..artifacts import ArtifactStore
from ..blocking import run_blocking
from ..observability import Tracer
from ..persistence.repository import LeaseLostError
from .revision import Assessment
from .support import digest


class WritingProgressError(RuntimeError):
    """Do not repeat paid work after losing or corrupting its progress record."""


class SavedAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hard: list[str] = Field(default_factory=list)
    soft: list[str] = Field(default_factory=list)
    brief: str = ""
    can_revise: bool = True
    local_problems: list[tuple[str, str]] | None = None

    @classmethod
    def capture(cls, value: Assessment) -> SavedAssessment:
        return cls.model_validate(asdict(value))

    def restore(self) -> Assessment:
        return Assessment(**self.model_dump())


class SavedDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str
    assessment: SavedAssessment | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class WritingState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_revisions: int = Field(ge=0)
    drafts: list[SavedDraft] = Field(default_factory=list)
    complete: bool = False
    final: dict[str, Any] | None = None
    preparation: dict[str, Any] = Field(default_factory=dict)
    total_tokens: int = Field(0, ge=0)
    estimated_tokens: int = Field(0, ge=0)
    elapsed: float = Field(0.0, ge=0)

    @model_validator(mode="after")
    def valid_sequence(self) -> WritingState:
        if self.estimated_tokens > self.total_tokens or not math.isfinite(self.elapsed):
            raise ValueError("invalid cumulative writing metrics")
        if self.final is not None and not self.complete:
            raise ValueError("final writer output has no completed writing loop")
        if any(
            key not in {"analysis", "analysis_scope", "figure"} or not isinstance(value, dict)
            for key, value in self.preparation.items()
        ):
            raise ValueError("invalid writing preparation record")
        if "figure" in self.preparation:
            figure = self.preparation["figure"]
            versions = figure.get("versions")
            if (
                not isinstance(figure.get("body_hash"), str)
                or not isinstance(versions, list)
                or not versions
            ):
                raise ValueError("invalid figure preparation")
            if any(
                not isinstance(item, dict)
                or not isinstance(item.get("model"), dict)
                or (item.get("record") is not None and not isinstance(item["record"], dict))
                for item in versions
            ):
                raise ValueError("invalid figure review preparation")
        if len(self.drafts) > self.max_revisions + 1:
            raise ValueError("writing progress exceeds frozen revision budget")
        if any(draft.assessment is None for draft in self.drafts[:-1]):
            raise ValueError("an earlier draft has no assessment")
        if self.complete and (not self.drafts or self.drafts[-1].assessment is None):
            raise ValueError("completed writing progress has no final assessment")
        return self


class WritingProgress:
    def __init__(
        self,
        store: ArtifactStore,
        scope: dict[str, Any],
        *,
        capture: Callable[[], dict[str, Any]] | None = None,
        restore: Callable[[dict[str, Any]], Any] | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.store = store
        self.scope = digest({"version": 1, **scope})
        self.capture = capture
        self.restore = restore
        self.tracer = tracer
        self._state: WritingState | None = None
        self._key = ""

    def path(self, max_revisions: int) -> str:
        return "writing/" + digest([self.scope, max_revisions]) + ".json"

    async def load(self, max_revisions: int) -> WritingState:
        key = digest([self.scope, max_revisions])
        if self._state is not None and self._key == key:
            return self._state
        self._key = key
        try:
            raw = await run_blocking(self.store.read_control_text, self.path(max_revisions))
        except FileNotFoundError:
            self._state = WritingState(max_revisions=max_revisions)
            return self._state
        except LeaseLostError:
            raise
        except Exception as exc:
            raise WritingProgressError("写作进度读取失败，未重复调用模型") from exc
        try:
            record = json.loads(raw)
            if record["key"] != key or record["digest"] != digest(record["state"]):
                raise ValueError("writing progress binding mismatch")
            state = WritingState.model_validate(record["state"])
            if state.max_revisions != max_revisions:
                raise ValueError("writing revision budget changed")
        except Exception as exc:
            raise WritingProgressError("写作进度校验失败，不能复用或静默覆盖") from exc
        if self.tracer is not None:
            use_saved = state.total_tokens > self.tracer.total_tokens
            self.tracer.restore_metrics(
                total_tokens=max(state.total_tokens, self.tracer.total_tokens),
                estimated_tokens=state.estimated_tokens
                if use_saved
                else self.tracer.estimated_tokens,
                elapsed=max(state.elapsed, self.tracer.elapsed),
            )
            self.tracer.emit(
                "SYNTHESIZER",
                "info",
                "复用已保存的草稿与核验进度…",
                data={"category": "writing_progress", "reused": True},
            )
        self._state = state
        return state

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.capture()) if self.capture else {}

    async def restore_draft(self, draft: SavedDraft) -> bool:
        if "body" in draft.context and draft.context["body"] != draft.body:
            raise WritingProgressError("草稿与附带的核验或表格记录不一致")
        if self.restore:
            try:
                value = self.restore(deepcopy(draft.context))
                if inspect.isawaitable(value):
                    value = await value
                return value is not False
            except LeaseLostError:
                raise
            except Exception as exc:
                raise WritingProgressError("草稿的表格、结构或核验记录无法恢复") from exc
        return True

    async def save_context(self) -> None:
        """Keep completed review substeps while the draft is still being assessed."""
        state = self._state
        if state is None or not state.drafts or state.drafts[-1].assessment is not None:
            return
        context = self.snapshot()
        if context.get("body") != state.drafts[-1].body:
            raise WritingProgressError("核验进度不属于当前草稿")
        state.drafts[-1].context = context
        await self.save(state)

    async def save(self, state: WritingState) -> None:
        if self.tracer:
            state.total_tokens = self.tracer.total_tokens
            state.estimated_tokens = self.tracer.estimated_tokens
            state.elapsed = self.tracer.elapsed
        try:
            data = state.model_dump(mode="json")
            await run_blocking(
                self.store.write_control_json,
                self.path(state.max_revisions),
                {
                    "key": digest([self.scope, state.max_revisions]),
                    "digest": digest(data),
                    "state": data,
                },
            )
        except LeaseLostError:
            raise
        except Exception as exc:
            raise WritingProgressError("写作进度保存失败，已停止继续调用模型") from exc
        self._state = state


OUTPUT_KEYS = frozenset(
    {
        "workbench",
        "prose_review",
        "_report_validation",
        "_evidence_tables",
        "_slide_deck",
        "_mindmap",
        "_review_score",
        "analysis",
        "analysis_scope",
    }
)


def for_writer(
    bb: Any, ctx: Any, role: str, system: str, *, inputs: Any = None
) -> WritingProgress | None:
    from ..agents.base import effective_require_corroboration
    from .analysis_review import STATISTICS_POLICY_VERSION
    from .mindmap_contract import MINDMAP_POLICY_VERSION
    from .quality import coerce_policy
    from .support import SUPPORT_POLICY_VERSION
    from .tables import TABLES_VERSION

    if not isinstance(ctx.artifact_store, ArtifactStore) or not ctx.run_id:
        return None

    def model(role_name: str) -> dict[str, str]:
        llm = ctx.llm_for(role_name)
        return {key: str(getattr(llm, key, "")) for key in ("model", "base_url")}

    return WritingProgress(
        ctx.artifact_store,
        {
            "run_id": ctx.run_id,
            "role": role,
            "query": bb.query,
            "system": ctx.system_prompt(system),
            "inputs": inputs,
            "results": [result.material_data() for result in bb.results],
            "sources": sorted(
                {digest(source.model_dump(mode="json")) for source in ctx.evidence_sources}
            ),
            "material": {
                key: bb.scratch.get(key)
                for key in (
                    "task_contract",
                    "attachments",
                    "intake_sources",
                    "paper_sources",
                    "paper_abstracts",
                    "review_coverage",
                    "content_revision",
                )
            },
            "seed": bb.report.model_dump(mode="json")
            if bb.report and bb.scratch.get("content_revision")
            else None,
            "quality": coerce_policy(ctx.settings.quality).model_dump(mode="json"),
            "capacity": ctx.settings.llm_max_input_chars,
            "corroboration": effective_require_corroboration(bb, ctx.settings),
            "writer": model(role),
            "verifier": model("evidence_verifier"),
            "support_policy": SUPPORT_POLICY_VERSION,
            "table_policy": TABLES_VERSION,
            "statistics_policy": STATISTICS_POLICY_VERSION,
            **({"mindmap_policy": MINDMAP_POLICY_VERSION} if role == "mindmap_writer" else {}),
        },
        tracer=ctx.tracer,
    )


async def restore_finished(progress: WritingProgress | None, bb: Any, max_revisions: int) -> bool:
    from ..models import Report

    if progress is None:
        return False
    state = await progress.load(max_revisions)
    if state.final is None:
        return False
    try:
        report = Report.model_validate(state.final["report"])
        scratch = state.final["scratch"]
        if (
            report.query != bb.query
            or not isinstance(scratch, dict)
            or not set(scratch) <= OUTPUT_KEYS
        ):
            raise ValueError("writer output binding mismatch")
    except Exception as exc:
        raise WritingProgressError("已完成的写作结果无法恢复") from exc
    bb.report = report
    for key in OUTPUT_KEYS:
        bb.scratch.pop(key, None)
    bb.scratch.update(deepcopy(scratch))
    if progress.tracer:
        progress.tracer.emit(
            "SYNTHESIZER", "token", data={"delta": report.markdown, "replace": True}
        )
    return True


async def finish(progress: WritingProgress | None, bb: Any, max_revisions: int) -> None:
    if progress is None or bb.report is None:
        return
    state = await progress.load(max_revisions)
    if not state.complete or not state.drafts:
        return
    last = state.drafts[-1].assessment
    if last is not None and last.hard and not last.can_revise:
        return
    state.final = {
        "report": bb.report.model_dump(mode="json"),
        "scratch": {key: deepcopy(bb.scratch[key]) for key in OUTPUT_KEYS if key in bb.scratch},
    }
    await progress.save(state)


def restore_prose(reviewer: Any, body: str, record: Any) -> bool:
    from .coverage_review import coverage_hash
    from .prose_review import body_text

    if record is not None and not isinstance(record, dict):
        raise WritingProgressError("已保存的正文核验记录格式无效")
    if reviewer is None or record is None:
        return True
    if not reviewer.prime(body, record):
        return False
    # Provider failures remain retryable; stable decisions can be reused after
    # finalization adds the program-owned bibliography.
    if not any(row.get("verdict") == "uncertain" for row in record.get("decisions", [])):
        key = reviewer.signature(body_text(body, strip_references=not reviewer.implicit))
        reviewer.records[key] = deepcopy(record)
    coverage = record.get("requirements_review")
    if reviewer.coverage and isinstance(coverage, dict) and not coverage.get("error"):
        signature = coverage_hash(reviewer.contract, body, reviewer.requirement_bases(body, record))
        if signature == coverage.get("input_hash"):
            reviewer.coverage.records[signature] = deepcopy(coverage)
    return True
