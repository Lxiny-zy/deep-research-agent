"""The same queue/stop invariants against independent real PostgreSQL pools."""

import pytest

from deep_research.persistence.db import make_engine, make_sessionmaker
from deep_research.workbench.qa_requests import SqlQaRequests
from deep_research.workbench.qa_store import SqlQaStore
from tests import test_qa_admission as admission
from tests import test_qa_queue_fairness as fairness
from tests import test_qa_stop_recovery as recovery
from tests.test_migrations_pg import _isolated_database, _postgres_url, _run_migration


@pytest.mark.pg
@pytest.mark.parametrize(
    "scenario",
    [
        fairness.test_blocked_backlogs_do_not_hide_an_independent_ready_conversation,
        fairness.test_recovery_yields_one_head_and_can_settle_an_expired_predecessor,
        admission.test_reservations_obey_global_and_owner_caps_without_breaking_idempotency,
        admission.test_concurrent_claims_respect_shared_caps_and_recovery_skips_saturated_owner,
        recovery.test_stop_pending_and_running_preserves_history_and_fences_late_work,
        recovery.test_cross_instance_stop_interrupts_active_call_without_reexecution,
        recovery.test_finish_cancel_race_has_one_terminal_result,
    ],
    ids=lambda scenario: scenario.__name__,
)
async def test_postgres_qa_reliability(scenario):
    async with _isolated_database(_postgres_url()) as url:
        await _run_migration(url, "head")
        first, second = make_engine(url), make_engine(url)
        try:
            store = SqlQaStore(make_sessionmaker(first))
            jobs = SqlQaRequests(make_sessionmaker(first))
            other = SqlQaRequests(make_sessionmaker(second))
            cid = (await store.create("local", "test")).id
            await scenario((store, jobs, other, cid))
        finally:
            await first.dispose()
            await second.dispose()
