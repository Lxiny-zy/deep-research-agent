# 05 — Aggregator Prompt（最终汇总）

所有 step 完成后，用此 prompt 调用 LLM API 生成最终报告。

## System Prompt

```
你是一个报告汇总器。你已经收到一个多步任务的全部中间产物路径。
你的职责是将这些产物整合成一份完整的、可独立使用的最终报告。

## 汇总规则

1. 以 Markdown 为单一内容真源
2. 读取全部指定产物，提取关键结论和数据
3. 报告结构:
   - 摘要（一段话概括核心发现）
   - 方法论（简要描述调研/实验过程）
   - 主要发现/结论（按主题分节）
   - 证据与引用（每条关键判断标注来源）
   - 局限与缺口（诚实记录未覆盖的部分）
   - 建议（按优先级列出可执行建议）
4. 事实、推断和建议三层分离，明确标注
5. 语言与用户原始输入一致
6. 不添加未在中间产物中出现的信息
7. 如果某些产物缺失或不完整，在报告中明确说明

## 可用工具

- 文件读取（读取中间产物）
- 格式转换（如需要输出 docx/pdf/html）

## 输出格式

用户未指定格式时，输出 .md 文件。
如需四件套（md + docx + pdf + html），按交付契约执行。
```

## User Prompt

```
## 任务标题

{PLAN_TITLE}

## 已完成的步骤及其产物

{COMPLETED_STEPS_SUMMARY}

## 需要读取的产物文件

{OUTPUT_FILE_PATHS}

## 用户原始诉求

{ORIGINAL_REQUEST}

## 汇总指令

请阅读以上全部产物文件，按 system prompt 中的汇总规则生成最终报告。
将报告写入: work/{SLUG}/final/report.md
```

## 变量说明

| 变量 | 来源 |
|------|------|
| `{PLAN_TITLE}` | plan.json 的 title 字段 |
| `{COMPLETED_STEPS_SUMMARY}` | 每个 step 的 name + status + output_paths + gap_note（如有） |
| `{OUTPUT_FILE_PATHS}` | 所有已完成步骤的 output_paths 列表 |
| `{ORIGINAL_REQUEST}` | 用户最开始的原始输入 |
| `{SLUG}` | 任务的 slug（从 plan title 生成） |

## 执行代码

```python
def run_aggregator(plan, all_output_paths, original_request, llm_api_call):
    """Run the final aggregation step."""

    # Build step summary
    steps_summary = []
    for s in plan['steps']:
        if s['status'] in ('done', 'partial'):
            entry = f"- **{s['name']}** ({s['status']}): 产物 {s.get('output_paths', ['无'])}"
            if s['status'] == 'partial' and s.get('gap_note'):
                entry += f" | 缺口: {s['gap_note']}"
            steps_summary.append(entry)

    system = AGGREGATOR_SYSTEM_PROMPT
    user = AGGREGATOR_USER_PROMPT.format(
        PLAN_TITLE=plan['title'],
        COMPLETED_STEPS_SUMMARY='\n'.join(steps_summary),
        OUTPUT_FILE_PATHS='\n'.join(all_output_paths),
        ORIGINAL_REQUEST=original_request,
        SLUG=generate_slug(plan['title'])
    )

    response = llm_api_call(system=system, user=user)
    return response
```

## 可选：格式转换

如果需要四件套交付，在最终报告生成后追加格式转换步骤:

```python
def convert_to_deliverables(report_md_path, output_dir):
    """Convert md to docx/pdf/html (you implement these based on your infra)."""
    # Option A: Use pandoc
    # subprocess.run(['pandoc', report_md_path, '-o', f'{output_dir}/report.docx'])
    # subprocess.run(['pandoc', report_md_path, '--pdf-engine=xelatex', '-o', f'{output_dir}/report.pdf'])

    # Option B: Use your own conversion service
    # ...

    # Option C: Skip format conversion, just deliver md
    pass
```
