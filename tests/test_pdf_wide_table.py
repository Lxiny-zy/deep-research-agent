from deep_research.workbench.delivery.pdf import render_pdf

LONG = "f2c690509f51372d264c701d10ad66b00c61d5c"


def test_over_wide_table_renders_instead_of_failing() -> None:
    header = "| " + " | ".join(f"列{i}" for i in range(6)) + " |"
    rule = "|" + "---|" * 6
    row = "| " + " | ".join(f"{LONG}{i} 512×512 (CAVE)" for i in range(6)) + " |"
    markdown = f"# 表格\n\n正文开头。\n\n{header}\n{rule}\n{row}\n{row}\n\n正文结尾一段。\n"
    data = render_pdf(markdown, title="超宽表")
    assert data.startswith(b"%PDF")
