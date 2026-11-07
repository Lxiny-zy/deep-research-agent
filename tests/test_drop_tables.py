from deep_research.workbench.tables import drop_unrendered_tables

SPEC = '```evidence-table\n{"id": "t2"}\n```\n\n'


def test_unrendered_table_spec_is_dropped_but_prose_kept() -> None:
    body = "## 方法对比\n\n正文段落 [1]。\n\n" + SPEC + "后续段落 [2]。\n"
    out, count = drop_unrendered_tables(body)
    assert count == 1
    assert "evidence-table" not in out
    assert "正文段落 [1]。" in out and "后续段落 [2]。" in out


def test_body_without_specs_unchanged() -> None:
    body = "## 节\n\n段落 [1]。\n"
    assert drop_unrendered_tables(body) == (body, 0)
