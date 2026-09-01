#!/usr/bin/env python3
"""
LLM 长任务编排框架 — 主执行脚本

将你的第三方 LLM API 接入此框架，复现 Vela 平台的长任务编排流程。

用法:
    python run.py "你的任务描述"
"""

import json
import os
import re
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# 配置
# ============================================================

FRAMEWORK_DIR = Path(__file__).parent
SKILLS_DIR = FRAMEWORK_DIR / "skills"

# TODO: 接入你的 LLM API
# 替换此函数为你的 API 调用逻辑
def llm_api_call(system: str, user: str, max_tokens: int = 8192) -> str:
    """
    调用你的第三方 LLM API。

    示例（OpenAI 兼容接口）:

    import openai
    client = openai.OpenAI(
        api_key="YOUR_KEY",
        base_url="YOUR_BASE_URL"
    )
    response = client.chat.completions.create(
        model="your-model",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        max_tokens=max_tokens
    )
    return response.choices[0].message.content
    """
    raise NotImplementedError("请在此函数中接入你的 LLM API")


# ============================================================
# 工具函数
# ============================================================

def load_file(path: Path) -> str:
    """读取文件内容"""
    if path.exists():
        return path.read_text(encoding='utf-8')
    return ""


def generate_slug(title: str) -> str:
    """从标题生成 slug"""
    # 保留中文字符和英文单词
    slug = re.sub(r'[^\w\u4e00-\u9fff]+', '-', title.lower()).strip('-')
    # 截断到合理长度
    if len(slug) > 40:
        slug = slug[:40]
    # 加上时间戳后缀
    ts = datetime.now().strftime('%Y%m%d')
    return f"{slug}-{ts}"


def inject_skills(step_prompt: str) -> str:
    """解析 step prompt 中的 SKILL.md 引用并注入内容"""
    skill_refs = re.findall(
        r'(?:读|read|参见|Read)\s*[`\'"]?(?:\.claude/skills/|skills/)(\w[\w-]*)/SKILL\.md[`\'"]?',
        step_prompt
    )

    injected = step_prompt
    for skill_name in skill_refs:
        skill_file = SKILLS_DIR / f"skill_{skill_name}.md"
        if skill_file.exists():
            skill_content = skill_file.read_text(encoding='utf-8')
            injected += f"\n\n---\n## 注入技能: {skill_name}\n\n{skill_content}"

    return injected


def validate_plan(plan_json_str: str) -> dict:
    """验证 plan.json 格式"""
    plan = json.loads(plan_json_str)

    assert isinstance(plan, dict), "plan must be dict"
    assert 'title' in plan and isinstance(plan['title'], str), "missing title"
    assert len(plan['title']) <= 40, f"title too long: {len(plan['title'])}"

    steps = plan.get('steps')
    assert isinstance(steps, list) and len(steps) > 0, "steps must be non-empty list"

    ids = [s['id'] for s in steps]
    assert len(ids) == len(set(ids)), "duplicate ids"

    for s in steps:
        for k in ('id', 'name', 'prompt'):
            assert isinstance(s.get(k), str) and s[k].strip(), f"bad {k}"
        assert len(s['name']) <= 20, f"name too long: {s['name']}"
        assert s.get('status', 'pending') == 'pending', "initial status must be pending"
        r = s.get('reset', True)
        assert isinstance(r, bool), "reset must be bool"

    return plan


# ============================================================
# Prompt 模板加载
# ============================================================

def load_planner_prompt(user_request: str) -> str:
    """加载并填充 planner prompt"""
    template = load_file(FRAMEWORK_DIR / "01_planner_system_prompt.md")
    return template.replace('<USER_REQUEST>', user_request)


def load_global_rules() -> str:
    """加载全局规则"""
    return load_file(FRAMEWORK_DIR / "06_global_rules.md")


# ============================================================
# 核心执行流程
# ============================================================

def run_planner(user_request: str) -> dict:
    """Step 1: 调用 Planner 生成 plan.json"""
    print("=" * 60)
    print("PHASE 1: PLANNER")
    print("=" * 60)

    planner_prompt = load_planner_prompt(user_request)

    system = planner_prompt
    user = f"用户诉求: {user_request}\n\n请产出 plan.json。"

    print(f"Planner prompt length: {len(system)} chars")

    response = llm_api_call(system=system, user=user)

    print(f"Planner response length: {len(response)} chars")

    # 尝试提取 JSON（LLM 可能用 markdown 包裹）
    json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', response, re.DOTALL)
    if json_match:
        plan_str = json_match.group(1)
    else:
        # 尝试直接解析
        plan_str = response.strip()

    plan = validate_plan(plan_str)

    print(f"Plan validated: {len(plan['steps'])} steps")
    for i, s in enumerate(plan['steps']):
        print(f"  Step {i}: {s['id']} - {s['name']}")

    return plan


def run_executor(plan: dict, user_request: str) -> dict:
    """Step 2: 逐个执行 plan 中的步骤"""
    print("\n" + "=" * 60)
    print("PHASE 2: EXECUTOR")
    print("=" * 60)

    slug = generate_slug(plan['title'])
    work_dir = Path(f"work/{slug}")
    work_dir.mkdir(parents=True, exist_ok=True)

    global_rules = load_global_rules()
    total_steps = len(plan['steps'])

    for idx, step in enumerate(plan['steps']):
        step_id = step['id']
        step_name = step['name']
        step_prompt = step['prompt']

        print(f"\n--- Step {idx+1}/{total_steps}: {step_name} ---")

        # Mark as running
        step['status'] = 'running'

        # Build executor system prompt
        system = f"""你是一个自主执行 agent，正在执行一个多步任务中的一个步骤。

## 全局规则

{global_rules}

## 当前步骤

- 步骤 {idx+1}/{total_steps}: {step_name}
- 步骤 ID: {step_id}

## 工作目录

{work_dir}

## 上一步产物

如上一步有产物，请读取并在此基础上继续。
"""

        # Inject skills
        full_prompt = inject_skills(step_prompt)

        # Call LLM
        try:
            response = llm_api_call(system=system, user=full_prompt)

            # Parse output files from response
            output_files = []
            # Look for file write patterns in the response
            file_patterns = re.findall(r'工作目录[：:]\s*([^\n]+)', response)
            output_files = file_patterns

            # Mark as done (or partial if we detect gaps)
            step['status'] = 'done'
            step['output_paths'] = output_files

            print(f"  Status: done")
            print(f"  Response length: {len(response)} chars")

        except Exception as e:
            step['status'] = 'partial'
            step['gap_note'] = str(e)
            print(f"  Status: partial ({e})")

    return plan


def run_aggregator(plan: dict, user_request: str) -> str:
    """Step 3: 最终汇总报告"""
    print("\n" + "=" * 60)
    print("PHASE 3: AGGREGATOR")
    print("=" * 60)

    slug = generate_slug(plan['title'])

    # Collect outputs
    all_outputs = []
    steps_summary = []
    for s in plan['steps']:
        if s['status'] in ('done', 'partial'):
            outputs = s.get('output_paths', [])
            all_outputs.extend(outputs)
            entry = f"- **{s['name']}** ({s['status']}): {outputs if outputs else '无明确产物路径'}"
            if s['status'] == 'partial' and s.get('gap_note'):
                entry += f" | 缺口: {s['gap_note']}"
            steps_summary.append(entry)

    # Build aggregator prompt
    system = """你是一个报告汇总器。请将多步任务的中间产物整合成一份完整的最终报告。

规则:
1. 以 Markdown 为单一内容真源
2. 报告结构: 摘要 → 方法论 → 主要发现 → 证据与引用 → 局限 → 建议
3. 事实、推断和建议三层分离
4. 语言与用户原始输入一致
5. 不添加未在中间产物中出现的信息
6. 缺失产物要明确说明"""

    user = f"""## 任务标题
{plan['title']}

## 已完成步骤

{chr(10).join(steps_summary)}

## 产物文件

{chr(10).join(all_outputs) if all_outputs else '（无明确产物路径，请基于各步骤的描述进行汇总）'}

## 用户原始诉求
{user_request}

## 汇总指令
请基于以上信息生成最终报告。"""

    response = llm_api_call(system=system, user=user)

    # Save report
    report_dir = Path(f"work/{slug}/final")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"
    report_path.write_text(response, encoding='utf-8')

    print(f"\nFinal report saved: {report_path}")
    print(f"Report length: {len(response)} chars")

    return response


def main():
    if len(sys.argv) < 2:
        print("用法: python run.py \"你的任务描述\"")
        print("示例: python run.py \"帮我调研一下2025年Rust编译器优化技术的进展\"")
        sys.exit(1)

    user_request = ' '.join(sys.argv[1:])
    print(f"任务: {user_request}")
    print(f"时间: {datetime.now(timezone.utc).isoformat()}")

    # Phase 1: Plan
    plan = run_planner(user_request)

    # Phase 2: Execute
    plan = run_executor(plan, user_request)

    # Phase 3: Aggregate
    report = run_aggregator(plan, user_request)

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == '__main__':
    main()
