from deep_research.workbench import writers
from deep_research.workbench.templates import get_template
from deep_research.workbench.writing_prompt import HARD_RULES, compose_writer_prompt


def _template(key: str):
    template = get_template(key)
    assert template is not None
    return template


def test_v2_prompt_orders_sections_and_lists_every_hard_rule() -> None:
    prompt = compose_writer_prompt(_template("peerReview"), brief="简述")
    titles = ("任务", "工作方法", "硬性规则", "文体", "输出格式")
    order = [prompt.index(f"# {title}") for title in titles]
    assert order == sorted(order)
    for index, rule in enumerate(HARD_RULES, 1):
        assert f"{index}. {rule.text}" in prompt


def test_v2_table_spec_only_for_table_templates() -> None:
    assert "需要对比多项数值时用表格" in compose_writer_prompt(
        _template("litReview"), brief="", table_instructions="规格"
    )
    for key in ("peerReview", "slides", "mindmap"):
        assert "需要对比多项数值时用表格" not in compose_writer_prompt(
            _template(key), brief="", table_instructions="规格"
        )


def test_v2_is_default_and_legacy_is_opt_out(monkeypatch) -> None:
    template = _template("paperRead")
    monkeypatch.delenv("DR_PROMPT_VARIANT", raising=False)
    v2 = writers.PaperReader().system_prompt(template, None)
    monkeypatch.setenv("DR_PROMPT_VARIANT", "legacy")
    legacy = writers.PaperReader().system_prompt(template, None)
    assert "# 硬性规则" in v2 and "# 硬性规则" not in legacy
