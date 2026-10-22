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


def _small_workbench_contract(execution: WorkflowRun, scratch: object) -> bool:
    """Only canonical workflows with measured, bounded inputs qualify."""
    if not isinstance(scratch, dict):
        return False
    from .workbench.contract import contract_from_scratch
    from .workbench.templates import get_template
    from .workflows import WORKFLOWS

    contract = contract_from_scratch(scratch)
    if contract is None:
        return False
    import re

    request = contract.original_request
    if re.search(r"系统综述|全面调研|全面研究|systematic\s+review", request, re.I):
        return False
    if any(int(match) > 10 for match in re.findall(
        r"(\d+)\s*(?:篇|页|章|个(?:主题|问题)|papers?\b|pages?\b)", request, re.I
    )):
        return False
    template = get_template(contract.template)
    builtin = WORKFLOWS.get(execution.workflow_name)
    if (
        template is None or builtin is None
        or execution.workflow_name not in template.strategies.values()
        or execution.definition != builtin.model_dump(mode="json")
        or "_execution_plan" in scratch
        or scratch.get("project_id")
        or contract.tier != "light" or contract.strategy == "deep"
    ):
        return False
    attachments = scratch.get("attachments", [])
    if not isinstance(attachments, list):
        return False
    chars = 0
    pages = 0
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("truncated"):
            return False
        chunks = attachment.get("chunks", [])
        if not isinstance(chunks, list) or any(not isinstance(c, dict) for c in chunks):
            return False
        chars += sum(len(str(c.get("content", ""))) for c in chunks)
        pages += max((c.get("page") or 0 for c in chunks), default=0)
    if len(attachments) > 2 or chars > 20_000 or pages > 10:
        return False
    # Remote pointers have unknown size until intake, so cannot qualify.
    if contract.papers or contract.dataset_tables or len(contract.requested_items) > 5:
        return False
    if contract.template == "dataAnalysis":
        import csv
        import io

        data = contract.dataset_csv
        if not data or len(data) > 100_000:
            return False
        delimiter = "\t" if "\t" in data.splitlines()[0] else ","
        rows = list(csv.reader(io.StringIO(data), delimiter=delimiter))
        return len(rows) <= 301 and max((len(row) for row in rows), default=0) <= 12
    if contract.template in {"mindmap", "paperRead", "peerReview"}:
        return bool(chars or contract.original_request.strip()) and (
            len(contract.original_request) <= 12_000
        )
    if contract.template == "autoResearch" and contract.strategy == "quick":
        settings = scratch.get(RUN_SETTINGS_KEY, {})
        return (
            isinstance(settings, dict)
            and settings.get("max_sub_questions", 99) <= 3
            and settings.get("results_per_search", 99) <= 4
            and contract.min_citations <= 3
            and len(contract.original_request) <= 1000
        )
    return False


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


def schedule_for_execution(execution: WorkflowRun | None, *, frozen: bool = True) -> ScheduleSpec:
    if execution is None:
        return ScheduleSpec(4, "heavy")
    scratch = execution.checkpoint.get("scratch", {})
    saved = scratch.get("_dispatch_schedule") if isinstance(scratch, dict) else None
    if frozen and isinstance(saved, dict):
        cost, saved_kind = saved.get("cost"), saved.get("kind")
        if type(cost) is int and 1 <= cost <= MAX_COST and saved_kind in {"light", "heavy"}:
            return ScheduleSpec(cost, saved_kind)
    raw = scratch.get(RUN_SETTINGS_KEY, {}) if isinstance(scratch, dict) else {}
    settings = raw if isinstance(raw, dict) else {}
    tier = settings.get("research_tier")
    name = execution.workflow_name
    short_flow = (
        ((name in _LIGHT_WORKFLOWS and not _has_explicit_execution_contract(execution, scratch))
         or _small_workbench_contract(execution, scratch))
        and tier not in {"standard", "deep"}
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
    candidates: Sequence[ScheduleCandidate],
    active: Sequence[ActiveRun],
    maximum: int | None,
    *,
    policy: str = "strict",
) -> list[ScheduleCandidate]:
    if policy not in {"strict", "borrow"}:
        raise ValueError("scheduling policy must be strict or borrow")
    if maximum is None:
        return list(candidates)
    if len(active) >= maximum:
        return []
    # A one-slot installation cannot preempt a long task. For C >= 2 the
    # light slot is a strict reservation, even when no light work is queued.
    heavy_limit = max(1, maximum - 1)
    per_identity = max(1, (heavy_limit + 1) // 2)
    heavy = Counter(run.identity for run in active if run.kind == "heavy")
    # Borrow only when no queued heavy identity has an unused guaranteed share.
    # Running work is never preempted; a later arrival waits for a slot to finish.
    borrow = policy == "borrow" and not any(
        c.kind == "heavy" and heavy[c.identity] < per_identity for c in candidates
    )
    heavy_count = sum(heavy.values())
    return [
        candidate
        for candidate in candidates
        if candidate.kind == "light"
        or (heavy_count < heavy_limit and (borrow or heavy[candidate.identity] < per_identity))
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
        # Prefer bounded short work within an identity for one aging quantum;
        # older research still wins so continuous short arrivals cannot starve it.
        aged += int(candidate.kind == "light")
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
