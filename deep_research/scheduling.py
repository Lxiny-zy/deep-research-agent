"""Deterministic, persistent deficit round robin for research admission.

Identity fairness charges estimated work, not the number of HTTP requests.
Priority changes ordering within an identity; aging eventually promotes old work.
All state returned here is committed together with the execution lease by repositories.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING, Literal

from .checkpoints import RUN_SETTINGS_KEY

if TYPE_CHECKING:
    from .orchestration import WorkflowRun

ScheduleKind = Literal["light", "heavy"]
MAX_COST = 8
QUANTUM = 1
AGING_SECONDS = 30.0
_LIGHT_WORKFLOWS = frozenset({"qa", "paper_qa", "quick"})


@dataclass(frozen=True)
class ScheduleSpec:
    cost: int
    kind: ScheduleKind


def validate_priority(priority: int) -> None:
    if type(priority) is not int or priority not in {0, 1, 2}:
        raise ValueError("schedule_priority must be an integer between 0 and 2")


def identity_key(owner_id: str | None) -> str:
    # Projects cannot manufacture additional shares; anonymous/legacy work
    # shares one bucket instead of obtaining a new identity for every run.
    return f"owner:{owner_id}" if owner_id is not None else "legacy"


def _has_explicit_execution_contract(execution: WorkflowRun, scratch: object) -> bool:
    # These modules depend on repository protocols; load their canonical keys
    # at classification time rather than introducing an import cycle.
    from .planner_runtime import PLAN_SCRATCH_KEY
    from .workbench.contract import CONTRACT_SCRATCH_KEY

    if isinstance(scratch, dict) and (
        PLAN_SCRATCH_KEY in scratch or CONTRACT_SCRATCH_KEY in scratch
    ):
        return True
    if not execution.definition:
        return False
    from .workflows import WORKFLOWS

    builtin = WORKFLOWS.get(execution.workflow_name)
    # Ordinary built-in quick has a frozen definition too. Only its exact
    # canonical definition is eligible; caller-authored DAGs/steps stay heavy.
    return builtin is None or execution.definition != builtin.model_dump(mode="json")


def schedule_for_execution(execution: WorkflowRun | None) -> ScheduleSpec:
    if execution is None:
        return ScheduleSpec(4, "heavy")
    scratch = execution.checkpoint.get("scratch", {})
    raw = scratch.get(RUN_SETTINGS_KEY, {}) if isinstance(scratch, dict) else {}
    settings = raw if isinstance(raw, dict) else {}
    tier = settings.get("research_tier")
    name = execution.workflow_name
    short_flow = (
        name in _LIGHT_WORKFLOWS
        and tier not in {"standard", "deep"}
        and not _has_explicit_execution_contract(execution, scratch)
    )
    # A light research tier estimates less work; it does not make an open
    # research/review/full-paper pipeline a short interactive task. In particular,
    # suffixes such as research_quick must never bypass the protected light slot.
    kind: ScheduleKind = "light" if short_flow else "heavy"
    base = 1 if tier == "light" or short_flow else 2 if tier == "standard" else 4
    seconds = settings.get("max_run_seconds", 0)
    if isinstance(seconds, (int, float)) and seconds > 7200:
        kind, base = "heavy", max(2, base)
    concurrency = settings.get("max_concurrency", 1)
    parallel = concurrency if type(concurrency) is int and concurrency > 0 else 1
    return ScheduleSpec(min(MAX_COST, base * max(1, ceil(parallel / 2))), kind)


@dataclass(frozen=True)
class ScheduleCandidate:
    run_id: str
    identity: str
    cost: int
    kind: ScheduleKind
    priority: int
    ready_at: float


@dataclass(frozen=True)
class ActiveRun:
    identity: str
    kind: ScheduleKind


@dataclass(frozen=True)
class IdentityCredit:
    deficit: int = 0
    last_round: int = 0


@dataclass(frozen=True)
class SchedulerState:
    cursor: str = ""
    round_no: int = 1


@dataclass(frozen=True)
class Selection:
    candidate: ScheduleCandidate
    credits: dict[str, IdentityCredit]
    state: SchedulerState


def eligible_candidates(
    candidates: Sequence[ScheduleCandidate], active: Sequence[ActiveRun], maximum: int | None
) -> list[ScheduleCandidate]:
    if maximum is None:
        return list(candidates)
    if len(active) >= maximum:
        return []
    # A one-slot installation cannot preempt a long task. For C >= 2 the
    # light slot is a strict reservation, even when no light work is queued.
    heavy_limit = max(1, maximum - 1)
    per_identity = max(1, (heavy_limit + 1) // 2)
    heavy = Counter(run.identity for run in active if run.kind == "heavy")
    return [
        candidate
        for candidate in candidates
        if candidate.kind == "light"
        or (sum(heavy.values()) < heavy_limit and heavy[candidate.identity] < per_identity)
    ]


def dispatch_info(candidate: ScheduleCandidate, *, now: float) -> dict[str, str | int | float]:
    return {
        "kind": candidate.kind,
        "cost": candidate.cost,
        "priority": candidate.priority,
        "ready_at": candidate.ready_at,
        "queue_seconds": max(0.0, now - candidate.ready_at),
    }


def select_next(
    candidates: Sequence[ScheduleCandidate],
    credits: Mapping[str, IdentityCredit],
    state: SchedulerState,
    *,
    now: float,
) -> Selection | None:
    if not candidates:
        return None
    heads: dict[str, ScheduleCandidate] = {}

    def rank(candidate: ScheduleCandidate) -> tuple[float, float, str]:
        aged = candidate.priority + max(0.0, now - candidate.ready_at) // AGING_SECONDS
        return -aged, candidate.ready_at, candidate.run_id

    for candidate in candidates:
        validate_priority(candidate.priority)
        if not 1 <= candidate.cost <= MAX_COST or candidate.kind not in {"light", "heavy"}:
            raise ValueError("invalid scheduling cost or class")
        if candidate.identity not in heads or rank(candidate) < rank(heads[candidate.identity]):
            heads[candidate.identity] = candidate
    identities = sorted(heads)
    index = bisect_left(identities, state.cursor)
    round_no = state.round_no
    if index == len(identities):
        index = 0
        round_no += 1
    updated = dict(credits)
    # At most MAX_COST rounds suffice for any head. A lone heavy task gets
    # its quantum within this call, without artificially waiting for poll ticks.
    for _ in range(len(identities) * (MAX_COST + 1)):
        identity = identities[index]
        credit = updated.get(identity, IdentityCredit())
        if credit.last_round < round_no:
            credit = IdentityCredit(min(MAX_COST, credit.deficit + QUANTUM), round_no)
            updated[identity] = credit
        candidate = heads[identity]
        if credit.deficit >= candidate.cost:
            updated[identity] = IdentityCredit(credit.deficit - candidate.cost, round_no)
            return Selection(candidate, updated, SchedulerState(identity, round_no))
        index += 1
        if index == len(identities):
            index = 0
            round_no += 1
    raise RuntimeError("bounded deficit round robin failed to select an eligible task")
