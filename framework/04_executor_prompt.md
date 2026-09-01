# 04 — Executor Prompt Template

每个 Step 执行时，将 step.prompt（来自 plan.json）与对应 SKILL.md 内容组合后发给 LLM API。

## 执行模板

```
<system>
你是一个自主执行 agent，正在执行一个多步任务中的一个步骤。

## 全局规则（已注入）

{global_rules_summary}

## 可用工具

你可以在执行过程中使用以下工具:
- 文件读写（创建/读取/修改文件）
- 网络搜索（如需要检索资料）
- 代码执行（如需要运行脚本）
- 图片生成（如需要出图）

## 运行环境

- 工作目录: <WORK_DIR>
- 当前时间: <TIMESTAMP>
- 本步骤是第 {STEP_INDEX}/{TOTAL_STEPS} 步: {STEP_NAME}

## 上一步产物（如有）

{PREVIOUS_OUTPUTS}
</system>

<user>
{STEP_PROMPT}

{INJECTED_SKILLS}
</user>
```

## 变量说明

| 变量 | 来源 | 说明 |
|------|------|------|
| `{global_rules_summary}` | `06_global_rules.md` 的摘要 | 目录约定、交付规范等 |
| `{WORK_DIR}` | 你的文件系统路径 | 当前工作目录 |
| `{TIMESTAMP}` | 系统时间 | ISO 8601 格式 |
| `{STEP_INDEX}` | plan.json 中的位置 | 从 1 开始 |
| `{TOTAL_STEPS}` | plan.json steps 长度 | 总步数 |
| `{STEP_NAME}` | step.name | UI 显示名 |
| `{PREVIOUS_OUTPUTS}` | 上一步的 output_paths 内容 | 如果 reset=false 则包含上文 |
| `{STEP_PROMPT}` | plan.json steps[i].prompt | Planner 生成的完整指令 |
| `{INJECTED_SKILLS}` | 由 `03_skill_router.md` 的逻辑注入 | SKILL.md 的完整内容 |

## 执行后处理

```python
def execute_step(step, previous_outputs, llm_api_call, skills_dir):
    """Execute a single step using the LLM API."""

    # 1. Build the full prompt
    system = EXECUTOR_SYSTEM_TEMPLATE.format(
        global_rules_summary=load_global_rules(),
        WORK_DIR=step.get('work_dir', '.'),
        TIMESTAMP=get_timestamp(),
        STEP_INDEX=step['index'],
        TOTAL_STEPS=step['total'],
        STEP_NAME=step['name']
    )

    user = step['prompt']
    if previous_outputs:
        user += f"\n\n## 上一步产物\n\n{previous_outputs}"

    # 2. Inject skills
    injected = inject_skills(user, skills_dir)

    # 3. Call LLM API
    response = llm_api_call(system=system, user=injected)

    # 4. Parse output (look for file writes, tool calls, or final text)
    output_files = parse_output_files(response)

    return response, output_files
```

## Step Prompt 内部结构（Planner 生成时遵循的模板）

每条 step.prompt 应该包含以下结构化段落:

```
[安全/合规边界]
本步骤只做...不实现或指导...（如适用）

[前置读取]
先读 `skills/<name>.md`、`skills/<name>.md`...
再读取以下上一步产物: `work/<slug>/<stage>/<file>`...

[执行动作]
1. 具体动作 A...
2. 具体动作 B...

[输出路径]
将结果写入: `work/<slug>/<stage>/<filename>`
格式: markdown / json / code / ...

[验收条件]
完成标志: 文件存在 + 格式正确 + 内容覆盖 [X] 和 [Y]
```

## 失败处理

| 情况 | 处理 |
|------|------|
| LLM 返回不完整/截断 | 重试一次，prompt 中加"继续" |
| LLM 拒绝执行 | 记录拒绝原因，标记 step 为 partial，说明缺口 |
| 文件写入失败 | 重试一次；仍失败则标记 partial |
| step 超时（如 4 小时） | 保存进度，标记 partial，继续下一步 |
