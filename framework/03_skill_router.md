# 03 — Skill Router（技能路由表）

Planner 根据任务类型决定每个 step 需要注入哪个 SKILL.md。

## 路由表

| 技能 ID | 触发条件 | 注入时机 | 文件 |
|---------|---------|---------|------|
| `academic-search-v2` | 需要学术文献检索/深读 | 检索类 step | `skills/skill_academic-search-v2.md` |
| `deep-read` | 需要读论文正文细节 | 深读类 step | `skills/skill_deep-read.md` |
| `research-explorer` | 用户方向模糊，需要选题 | 第一步（探索） | `skills/skill_research-explorer.md` |
| `experiment-suite` | 需要设计并跑实验 | 实验类 step | `skills/skill_experiment-suite.md` |
| `literature-survey` | 需要写综述 | 综述类 step | `skills/skill_literature-survey.md` |
| `paper-writer` | 需要写论文 | 论文类 step | `skills/skill_paper-writer.md` |
| `image-gen` | 需要生成架构图/概念图 | 出图 step | `skills/skill_image-gen.md` |
| `docx` | 需要输出 Word 文档 | 交付 step | `skills/skill_docx.md` |
| `pdf` | 需要输出 PDF | 交付 step | `skills/skill_pdf.md` |
| `pptx` | 需要输出幻灯片 | 交付 step | `skills/skill_pptx.md` |
| `xlsx` | 需要输出表格 | 交付 step | `skills/skill_xlsx.md` |
| `ai4s-agent` | 全流程学术研究 | 元编排（可选） | `skills/skill_ai4s-agent.md` |

## 路由逻辑

```python
SKILL_ROUTER = {
    "search": {
        "skills": ["academic-search-v2", "deep-read"],
        "trigger": lambda task: any(kw in task.lower() for kw in ["论文", "文献", "research", "paper", "survey"])
    },
    "explore": {
        "skills": ["research-explorer"],
        "trigger": lambda task: any(kw in task.lower() for kw in ["探索", "选题", "方向", "explore", "topic"])
    },
    "experiment": {
        "skills": ["experiment-suite"],
        "trigger": lambda task: any(kw in task.lower() for kw in ["实验", "训练", "跑", "experiment", "benchmark", "train"])
    },
    "survey": {
        "skills": ["literature-survey", "academic-search-v2"],
        "trigger": lambda task: any(kw in task.lower() for kw in ["综述", "survey", "literature review"])
    },
    "paper": {
        "skills": ["paper-writer"],
        "trigger": lambda task: any(kw in task.lower() for kw in ["论文", "paper", "write up"])
    },
    "deliver_doc": {
        "skills": ["docx", "pdf"],
        "trigger": lambda task: any(kw in task.lower() for kw in ["报告", "分析", "方案", "report", "analysis"])
    },
    "deliver_ppt": {
        "skills": ["pptx"],
        "trigger": lambda task: any(kw in task.lower() for kw in ["ppt", "幻灯片", "presentation", "slides"])
    },
    "figure": {
        "skills": ["image-gen"],
        "trigger": lambda task: any(kw in task.lower() for kw in ["图", "架构", "diagram", "figure", "architecture"])
    },
}
```

## 使用方式

1. Planner 在设计每个 step.prompt 时，在 prompt 中明确写出 "读 `<skill_name>/SKILL.md`"
2. Executor 在执行该 step 时，根据 prompt 中提到的 SKILL.md 名称，从 `skills/` 目录读取内容
3. 将 skill 内容作为额外的 system prompt 段落注入

```python
def inject_skills(step_prompt, skills_dir="framework/skills/"):
    """Parse skill references from step prompt and inject their content."""
    import re
    skill_refs = re.findall(r'(?:读|read|参见)\s*`?\.claude/skills/(\w[\w-]*)/SKILL\.md`?', step_prompt)

    injected = step_prompt
    for skill_name in skill_refs:
        skill_file = os.path.join(skills_dir, f"skill_{skill_name}.md")
        if os.path.exists(skill_file):
            skill_content = open(skill_file, 'r', encoding='utf-8').read()
            injected += f"\n\n---\n## 注入技能: {skill_name}\n\n{skill_content}"

    return injected
```
