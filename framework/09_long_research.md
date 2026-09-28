# 长研究流程：当前项目实现

这是从 Vela 提取资料扩展的项目专用规范，不是恢复出来的原始平台代码。
`vela_original` 中的截断文本、无效 JSON、缺失脚本保留作历史参考，不进入生产提示词。

## 使用方式

服务 API 已接受 `POST /api/runs` 中的 `execution_plan` 对象（兼容 `plan` 别名）。
将 `10_research_plan.json` 的内容作为该字段，与研究 query 一起提交即可。
认证、模型和检索配置沿用项目现有设置。

命令行可直接使用同一执行器：

```powershell
python -m deep_research.cli "比较现有检索增强生成方法的适用条件和评估局限" --plan framework/10_research_plan.json -o research_report.md
```

CLI 使用环境配置，服务端使用其数据库配置；它们不是同一个设置来源。
需要加载本地 dotenv 时沿用项目已有的 `python -m dotenv run -- ...` 启动方式。
`--plan` 与 `--workflow` 互斥。计划在构造模型客户端前解析；真实运行会调用已配置的服务。

## 示例中的实际链路

```mermaid
flowchart LR
    P[内置 Planner：研究子问题] --> R[内置 Researcher：检索与证据验证]
    R --> F[内置 Reflector：证据缺口]
    F --> A[文本执行器：比较、反证和交接]
    A --> D[文本执行器：终稿与验收]
```

内置角色使用黑板上的 query/plan/results/reflections，外部 step.prompt 不替换内置角色提示词。
需要定制判断标准的步骤放在文本执行器中。示例执行一次反思，不自动无限补洞；
原生 `deep` 工作流已经提供受 `max_rounds` 限制的反思循环，仍可直接使用。

## 输入与交接

计划使用 `deep_research.planning.ExecutionPlan` 严格模型，兼容边界由 `coerce_execution_plan` 处理。
`depends_on` 控制执行和交接：全部省略时按列表串行；声明依赖后按完整 DAG 调度。
每个文本步骤收到祖先阶段的已声明产物及状态，不读取无关分支。
长文件按预算节选并标注 `truncated`；大任务应输出紧凑交接，而不是依赖整篇资料一直驻留上下文。

每个交接至少应包含：范围和纳入标准；事实及来源；冲突或反例；失败路线；
未解决问题及其影响；下一步动作和验收标准。避免把事实数量当作证据充分性的替代品。
模型声明的来源尚不等于已验证证据，真正的检索和核验由 researcher 完成。

## 输出与状态

单文件兼容直接文本；多个文件自动要求 `research-step-v1`。
也可在单文件步骤显式设置 `metadata.result_contract`，要求摘要、缺口和下一步字段。
详见 `04_executor_prompt.md`。不存在任意 shell/tool-call 的解析与执行。

| 情况 | 行为 |
|---|---|
| 全部必需产物有效且无缺口 | 计划步骤 `done` |
| 有有效产物但不完整，响应明确列出 gaps | 计划步骤 `partial`，交接保留缺口 |
| 无效 JSON、未声明路径、缺失必需文件却声称 done | 步骤失败，按既有重试策略处理 |
| 声明 PDF 等文本执行器不支持的格式 | 模型调用前失败；改用真实转换/导出能力 |
| 上游研究失败但后续可交付 | 缺口进入交接及最终报告；全计划不伪装 done |
| 存储、完整性或注册操作错误 | 保持 failed，不伪装成研究局限 |

工作流内部的 succeeded 表示步骤已提交；计划层额外根据结构化结果区分 done/partial。
部分完成是可继续消费的终态，不会自动无限重试。需要补充研究时应形成新阶段或新任务。
多前置的汇合点默认要求执行成功；终稿可显式设置 `allow_partial_dependencies: true`，
允许失败分支留下缺口后继续汇总。该选项不跳过文件完整性检查。

## 持久化与续跑

实际 artifact root 下通常按 run ID 隔离，不同研究不会共享同一组工作文件。

- `work/<slug>/...`：中间成果、原始响应和现有黑板投影。
- `output/<slug>/...`：可交付文本和真实转换结果。
- `.framework/plans/<slug>.json`：执行计划状态。
- `.framework/steps/<slug>/<step-id>.json`：尝试次数、时间、状态、指纹、哈希、摘要和缺口。

每步先保存 running，模型返回后保存原始响应，再整批校验、写出文件、提交完成记录。
完成记录允许恢复“文件已提交、工作流 checkpoint 尚未保存”的中断窗口。
恢复时必须匹配请求/规则/技能/依赖/设置/证据指纹，且产物哈希有效；否则重新执行或报告损坏。
进行中的模型响应没有逐 token 检查点，进程硬退出后从步骤边界重试。
失败尝试的文件会留作诊断，但不进入下游成功交接。

已有服务恢复接口 `/api/runs/{run_id}/resume` 仍遵守任务状态和租约检查，
只对允许恢复的执行状态生效；不保证任何已终结任务都能强制复活。

## 全局限制

运行时设置决定 `max_run_seconds`、`max_tokens`、`max_concurrency`、`max_rounds` 等。
设置快照随运行保存；计划的 `resource.timeout_seconds` 和 `max_attempts` 限制单步执行。
当前工作流单步 timeout 上限为 3600 秒，注册操作还受自己的超时、进程数及隔离限制。
不预设 4 小时窗口、48 小时运行、GPU 调度或服务器上传；这些不是提示词可授予的能力。
需要更长研究时，使用更多有明确验收的主题阶段及已有 worker/checkpoint 机制，
并配置合理的总预算。实际超长运行和真实提供商行为仍需在部署环境中验证。
