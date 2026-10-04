"""Quality terminal states must never leave the public narrative running."""

import pytest

from deep_research.observability import Event
from deep_research.workbench.narrative import build_narrative


@pytest.mark.parametrize("analysed", [False, True])
@pytest.mark.parametrize("has_terminal_event", [False, True])
def test_review_required_narrative_is_terminal_without_claiming_completion(
    analysed, has_terminal_event
):
    events = [
        Event(seq=0, stage="RESEARCHER", type="finding", data={"verified_count": 3}),
        Event(
            seq=1,
            stage="SYNTHESIZER",
            type="start",
            data={"category": "analysis"} if analysed else None,
        ),
    ]
    if has_terminal_event:
        events.append(
            Event(
                seq=2,
                stage="ORCHESTRATOR",
                type="needs_review",
                elapsed=4,
                message="必需交付尚待复核",
            )
        )

    narrative = build_narrative(events, run_status="needs_review")

    assert narrative["headline"].startswith("待复核")
    assert "进行中" not in narrative["headline"]
    assert "已完成" not in narrative["headline"]
    assert all(section["status"] != "active" for section in narrative["sections"])
    finish = next(section for section in narrative["sections"] if section["key"] == "finish")
    assert finish["status"] == "needs_review"
    assert finish["title"] == "待复核"
    assert any("待复核" in line for line in finish["lines"])
    assert all("研究完成" not in line for line in finish["lines"])
    if has_terminal_event:
        assert finish["first_seq"] == finish["last_seq"] == 2
        assert finish["elapsed"] == 4
