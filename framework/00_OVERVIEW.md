# LLM 长任务编排框架 — 总览

从 Vela/Apevon 平台日志中逆向提取的完整长任务编排设计，可直接接入第三方 LLM API 复现。

## 架构图

```
用户输入原始诉求
    │
    ▼
┌─────────────────────────────────────────┐
│  Layer 1: Planner（规划器）              │
│  输入: planner_prompt + 用户诉求         │
│  输出: plan.json                        │
│  模型: 任意 LLM API                     │
├─────────────────────────────────────────┤
│  Layer 2: Executor（执行器）             │
│  输入: plan.json 中的每条 step.prompt    │
│  附加: 对应 SKILL.md 作为领域指令        │
│  输出: 磁盘产物 + 状态更新              │
│  模型: 任意 LLM API                     │
├─────────────────────────────────────────┤
│  Layer 3: Aggregator（汇总器）           │
│  输入: 全部中间产物路径                  │
│  输出: 最终报告（md/docx/pdf/html）      │
│  模型: 任意 LLM API                     │
└─────────────────────────────────────────┘
```

## 文件说明

| 文件 | 用途 | 你的接入点 |
|------|------|-----------|
| `01_planner_system_prompt.md` | Planner 的 system prompt 模板 | 直接作为 LLM API 的 system 消息 |
| `02_plan_schema.md` | plan.json 的 schema 定义和验证规则 | 解析 LLM 输出并验证 |
| `06_global_rules.md` | 全局编排规则（AGENTS.md 改造版） | 附加到每个 Agent 的 system prompt |
| `03_skill_router.md` | 技能路由表：什么任务读什么 SKILL.md | Planner 用它决定每步要注入哪个 SKILL |
| `04_executor_prompt.md` | 每个 Step 执行时的 prompt 模板 | 替换你的 step 执行逻辑 |
| `05_aggregator_prompt.md` | 最终汇总报告的 prompt 模板 | 最后一步的 system prompt |
| `07_example_plan.md` | 完整示例 plan.json | 参考格式 |
| `skills/` | 各领域技能指令 | 按 04_skill_router.md 注入到对应 step |
| `08_example_plan.json` | 完整示例 plan.json | 参考格式 |

## 执行流程

1. **接收用户输入** → 原始诉求文本
2. **调用 Planner** → 将用户诉求注入 `01_planner_system_prompt.md` 的插槽，发给 LLM API
3. **解析输出** → LLM 返回 plan.json，用 `02_plan_schema.md` 验证
4. **逐 Step 执行** → 对每条 step：
   a. 从 plan.json 取出 prompt
   b. 根据 prompt 中提到的 SKILL.md，从 `skills/` 目录注入对应内容
   c. 将 step prompt + skill 内容发给 LLM API
   d. 收集输出，写入磁盘产物
   e. 更新 plan.json 中该步的 status
5. **最终汇总** → 将全部产物路径注入 `06_aggregator_prompt.md`，发给 LLM API
6. **返回结果** → 最终报告

## 与 Vela 的对应关系

| Vela 概念 | 你的实现 |
|-----------|---------|
| `planner step` | 第一次 LLM API 调用 |
| `plan.json` | LLM 输出的 JSON，你解析后存入变量/文件 |
| `reset: true` | 每次调用 LLM API 时不带之前的 conversation history |
| `SKILL.md` | 额外注入的 system prompt 段落 |
| `work/<slug>/` | 你的本地/云端文件存储 |
| `status: pending/running/done/partial` | 你维护的状态变量 |
| `resource.gpu` | 你的推理资源标识（可忽略或映射到你自己的 GPU） |
| `delivery-contract` | 可选的格式验收逻辑 |
