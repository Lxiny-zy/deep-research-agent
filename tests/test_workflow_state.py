from types import SimpleNamespace

from deep_research.workbench.workflow_state import classify_issue, derive_workflow_state


def _detail(*, results=True, report=True):
    return SimpleNamespace(
        query="q",
        results=[object()] if results else [],
        report=SimpleNamespace(markdown="body") if report else None,
        orchestration=SimpleNamespace(
            checkpoint={"scratch": {"workbench": {"template": "litReview"}}}
        ),
    )


def test_state_routes_issues_to_actions_and_keeps_phase_order():
    state = derive_workflow_state(
        _detail(), None, ["缺少章节：结论", "证据覆盖不足：缺少方法正文"]
    )
    assert state["version"] == 1
    assert state["current_phase"] == "evidence"
    assert state["next_action"] == "retrieve"
    assert state["phases"][-1]["issues"] == ["缺少章节：结论", "证据覆盖不足：缺少方法正文"]
    assert state["issue_counts"]["rewrite"] == 1
    assert state["issue_counts"]["retrieve"] == 1
    assert [phase["id"] for phase in state["phases"]] == [
        "intake", "evidence", "coverage", "draft", "review", "repair", "render", "acceptance"
    ]


def test_state_distinguishes_security_from_repairable_quality():
    assert classify_issue("引用编号越界") == "security"
    assert classify_issue("缺少章节：结论") == "rewrite"
    assert classify_issue("PDF 生成失败") == "render"
    assert classify_issue("全文核查需要补读原文") == "read"
