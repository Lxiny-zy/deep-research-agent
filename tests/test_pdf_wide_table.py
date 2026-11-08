from deep_research.workbench.delivery.pdf import render_pdf

LONG = "f2c690509f51372d264c701d10ad66b00c61d5c"


def test_over_wide_table_renders_instead_of_failing() -> None:
    header = "| " + " | ".join(f"列{i}" for i in range(6)) + " |"
    rule = "|" + "---|" * 6
    row = "| " + " | ".join(f"{LONG}{i} 512×512 (CAVE)" for i in range(6)) + " |"
    markdown = f"# 表格\n\n正文开头。\n\n{header}\n{rule}\n{row}\n{row}\n\n正文结尾一段。\n"
    data = render_pdf(markdown, title="超宽表")
    assert data.startswith(b"%PDF")


def test_hyphenated_names_get_their_full_width() -> None:
    header = "| 方法 | 数据集 | PSNR | SSIM | 参数量 | 备注 |"
    rule = "|---|---|---|---|---|---|"
    rows = "\n".join(
        f"| Ours-{n}stg | KAIST 仿真场景 | 38.{n} | 0.96{n} | 1.{n} M | 同一研究的仿真结果 |"
        for n in (3, 5, 9)
    )
    markdown = f"# 表\n\n正文。\n\n{header}\n{rule}\n{rows}\n\n结尾段落。\n"
    assert render_pdf(markdown, title="连字符").startswith(b"%PDF")
