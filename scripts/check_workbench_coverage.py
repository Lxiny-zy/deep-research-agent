"""从参考资料包本身抽取功能清单，逐项核对本项目的实现。

与人工撰写的覆盖文档不同，这里的清单**不由作者列举**，而是从资料包的原始文件
机械抽取：

* 任务类型：``tasks/_tasklist.json`` 与 ``tasks_all/*/task.json`` 里出现过的全部 ``template_key``；
* 平台接口：设计文档架构图里列出的全部 HTTP 端点；
* 验收门：``repo/.claude/skills/_shared/scripts/`` 下的全部门脚本；
* 交付技能：``repo/.claude/skills/`` 下的全部技能目录；
* 计划契约字段：真实任务 ``plan.json`` 中步骤与资源信封的全部字段；
* 步骤状态：真实 ``vela-steps.json`` 中出现过的全部状态值。

每一项都映射到本项目中的一个可检查实体（模板键、路由、门函数、模块、模型字段），
映射表 ``MAPPING`` 是唯一需要人写的部分——若资料包新增了某类条目而映射表里没有，
脚本直接报「未映射」，而不是悄悄漏掉。

用法：``python scripts/check_workbench_coverage.py``（退出码 0 = 全部覆盖）。
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "apevon-full-package"
sys.path.insert(0, str(ROOT))

# 资料包条目 → 本项目实体。实体写法：
#   template:<key>        workbench 模板键
#   route:<METHOD> <path> FastAPI 路由
#   symbol:<module>:<name> 可导入的 Python 符号
#   field:<module>:<Model>.<field> Pydantic / dataclass 字段
#   status:<value>        planning.StepStatus 成员值
#   na:<理由>             明确不适用（必须写理由）
MAPPING: dict[str, str] = {
    # 任务类型
    "template_key:autoResearch": "template:autoResearch",
    "template_key:litReview": "template:litReview",
    "template_key:peerReview": "template:peerReview",
    "template_key:dataAnalysis": "template:dataAnalysis",
    "template_key:paperRead": "template:paperRead",
    "template_key:slides": "template:slides",
    "template_key:mindmap": "template:mindmap",
    # 平台接口
    "endpoint:/tasks": "route:GET /api/runs",
    "endpoint:/tasks/{id}/vela-steps": "route:GET /api/runs/{run_id}/workspace",
    "endpoint:/tasks/{id}/timeline": "route:GET /api/runs/{run_id}/events",
    "endpoint:/tasks/{id}/narrative": "route:GET /api/runs/{run_id}/narrative",
    "endpoint:/tasks/{id}/artifacts": "route:GET /api/runs/{run_id}/deliverables",
    "endpoint:/tasks/{id}/artifacts/{path}": "route:GET /api/runs/{run_id}/workspace/file",
    "endpoint:/credit-costs": "na:不做积分计费；用量只按运行次数与 token 额度统计（/api/usage）",
    "endpoint:/v1/billing/quota": "route:GET /api/usage",
    "endpoint:/v1/billing/subscription": (
        "na:自部署工作台没有收费主体，不做订阅；额度由 /api/usage（运行次数与 token）承担"
    ),
    # 验收门脚本
    "gate:citation_gate.py": "symbol:deep_research.workbench.gates:citation_gate",
    "gate:deliver_gate.py": "symbol:deep_research.workbench.publish:build_bundle",
    "gate:final_figure_manifest.py": "symbol:deep_research.workbench.gates:consistency_gate",
    "gate:glyph_check.py": "symbol:deep_research.workbench.delivery.pdf:verify_pdf",
    "gate:length_gate.py": "symbol:deep_research.workbench.gates:length_gate",
    "gate:manuscript_qa.py": "symbol:deep_research.workbench.gates:structure_gate",
    "gate:markdown_gate.py": "symbol:deep_research.workbench.gates:markdown_gate",
    "gate:package_latex_sources.py": (
        "symbol:deep_research.report.bundle:render_reproducibility_bundle"
    ),
    "gate:publish_research_output.py": "symbol:deep_research.workbench.publish:publish",
    "gate:renumber_final_figures.py": "symbol:deep_research.workbench.analysis:analyse",
    "gate:setup_env.sh": "na:依赖由 requirements 锁文件与镜像构建统一安装，不在运行期装包",
    "gate:statistical_table_qa.py": "symbol:deep_research.workbench.analysis:check_numbers",
    "gate:style_gate.py": "na:不复制参考产品的品牌样式基底；DOCX 统一由 _base_document 生成",
    "gate:territory_gate.py": "symbol:deep_research.workbench.gates:territory_gate",
    "gate:wait_for.py": "na:等待外部进程的脚本；本项目的超时与等待由引擎和 runner 统一管理",
    # 交付技能
    "skill:academic-search-v2": "symbol:deep_research.workbench.intake:fetch_paper",
    "skill:ai4s-agent": "symbol:deep_research.planning:ExecutionPlan",
    "skill:docx": "symbol:deep_research.workbench.delivery.docx:render_docx",
    "skill:experiment-suite": (
        "na:部署只通过 API 调用云端 LLM，不在本地调度 GPU 训练/实验；计划中的 GPU 步骤入口即拒绝"
    ),
    "skill:image-gen": "symbol:deep_research.workbench.figures:concept_figure",
    "skill:literature-survey": "template:litReview",
    "skill:mindmap-render": "symbol:deep_research.workbench.delivery.mindmap:render_mindmap_html",
    "skill:paper-writer": "symbol:deep_research.report.latex:render_latex",
    "skill:pdf": "symbol:deep_research.workbench.delivery.pdf:render_pdf",
    "skill:pptx": "symbol:deep_research.workbench.delivery.pptx:render_pptx",
    "skill:research-explorer": "template:autoResearch",
    "skill:xlsx": "symbol:deep_research.report.xlsx:render_xlsx",
    "skill:_shared": "symbol:deep_research.workbench.gates:overall",
}

# 计划契约字段与步骤状态按「字段名是否存在于对应模型」自动核对，不需要映射。
PLAN_FIELD_ALIASES = {
    # 参考 plan.json 字段 → 本项目模型字段
    "max_runtime_hours": "timeout_seconds",
    "memory": "memory_mb",
    # 补救步骤指向被补救步骤：由重规划日志的 target 字段记录（workbench/replan.py）
    "remediates": "metadata",
    "origin": "metadata",
    "elapsed_seconds": "metadata",
    "runtime_env_credential_id": "metadata",
    "title": "title",
}


def extract() -> dict[str, set[str]]:
    items: dict[str, set[str]] = {k: set() for k in ("template_key", "endpoint", "gate", "skill")}
    for path in [PACKAGE / "tasks/_tasklist.json", *PACKAGE.glob("tasks_all/*/task.json")]:
        data = json.loads(path.read_text(encoding="utf-8"))
        for task in data if isinstance(data, list) else [data]:
            if task.get("template_key"):
                items["template_key"].add(task["template_key"])
    manifest = (PACKAGE / "repo/MANIFEST.md").read_text(encoding="utf-8")
    items["template_key"].update(re.findall(r"\| `(\w+)` \|", manifest))
    design = (PACKAGE / "prompts/Apevon研究链路复刻设计.md").read_text(encoding="utf-8")
    items["endpoint"].update(re.findall(r"│\s*(/[\w/{}\-]+)", design))
    items["gate"].update(
        p.name for p in (PACKAGE / "repo/.claude/skills/_shared/scripts").iterdir()
    )
    items["skill"].update(p.name for p in (PACKAGE / "repo/.claude/skills").iterdir() if p.is_dir())
    return items


def plan_fields() -> tuple[set[str], set[str]]:
    step_fields: set[str] = set()
    statuses: set[str] = set()
    for path in PACKAGE.glob("tasks*/**/vela-steps.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for step in data.get("steps", []):
            step_fields.update(step.keys())
            step_fields.update(f"resource.{key}" for key in (step.get("resource") or {}))
            if step.get("status"):
                statuses.add(step["status"])
    return step_fields, statuses


def check(entity: str) -> str | None:
    kind, _, target = entity.partition(":")
    if kind == "na":
        return None if target.strip() else "na 缺少理由"
    if kind == "template":
        from deep_research.workbench.templates import TASK_TEMPLATES

        return None if target in TASK_TEMPLATES else "模板不存在"
    if kind == "route":
        from deep_research.api import app

        # 新版 FastAPI 把 include_router 的路由嵌套在内部对象里；OpenAPI 文档是
        # 全部已注册路由的权威清单，按它核对不依赖框架内部结构。
        method, path = target.split(" ", 1)
        operations = app.openapi().get("paths", {}).get(path, {})
        return None if method.lower() in operations else "路由不存在"
    if kind == "symbol":
        module, name = target.split(":")
        return None if hasattr(importlib.import_module(module), name) else "符号不存在"
    return f"未知实体类型 {kind}"


def main() -> int:
    import deep_research.agents  # noqa: F401  注册全部角色
    from deep_research.planning import ExecutionStep, ResourceSpec, StepStatus

    failures: list[str] = []
    rows: list[tuple[str, str, str]] = []
    for group, names in extract().items():
        for name in sorted(names):
            key = f"{group}:{name}"
            entity = MAPPING.get(key)
            if entity is None:
                failures.append(f"未映射：{key}")
                rows.append((key, "—", "未映射"))
                continue
            problem = check(entity)
            rows.append(
                (key, entity, problem or ("不适用" if entity.startswith("na:") else "覆盖"))
            )
            if problem:
                failures.append(f"{key} → {entity}：{problem}")
    step_fields, statuses = plan_fields()
    known = set(ExecutionStep.model_fields) | {f"resource.{f}" for f in ResourceSpec.model_fields}
    for field in sorted(step_fields):
        leaf = field.split(".")[-1]
        alias = PLAN_FIELD_ALIASES.get(leaf, leaf)
        target = f"resource.{alias}" if field.startswith("resource.") else alias
        ok = target in known or alias in known
        rows.append((f"plan_field:{field}", target, "覆盖" if ok else "缺失"))
        if not ok:
            failures.append(f"计划字段 {field} 无对应")
    values = {member.value for member in StepStatus}
    for status in sorted(statuses):
        ok = status in values
        rows.append((f"step_status:{status}", "planning.StepStatus", "覆盖" if ok else "缺失"))
        if not ok:
            failures.append(f"步骤状态 {status} 无对应")

    width = max(len(row[0]) for row in rows)
    for key, entity, verdict in rows:
        print(f"{verdict:<4}  {key:<{width}}  {entity}")
    covered = sum(1 for row in rows if row[2] in {"覆盖", "不适用"})
    print(f"\n{covered}/{len(rows)} 项覆盖或已说明不适用")
    for failure in failures:
        print("FAIL", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
