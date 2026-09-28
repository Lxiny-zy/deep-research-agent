"""工作台终端角色名（纯常量，无运行期依赖，供引擎与目录层共同引用）。

这些角色都「产出报告」：预算耗尽时引擎仍执行它们，保证尽力而为的交付。
单独成文件是为了让 ``workflow`` 与 ``catalog.runtime`` 引用同一份名单，
又不必导入工作台的写作者实现（那会把 LLM / 文档依赖拖进引擎模块）。
"""

from __future__ import annotations

WORKBENCH_WRITER_ROLES: frozenset[str] = frozenset(
    {
        "research_writer",
        "survey_writer",
        "peer_reviewer",
        "paper_reader",
        "slide_writer",
        "mindmap_writer",
        "data_analyst",
    }
)

# 从用户给定的论文 / 链接 / 资料库来源建立证据（而非开放检索）的角色。
PAPER_INTAKE_ROLE = "paper_intake"
