# 项目问题检查与分析

日期：2026-10-10。审查基线：`ed97c2772fac0e9540541cd4398ad27e5f193e56` 加当前未提交修改。

**修复状态：这四项问题已于 2026-10-10 完成本地代码修复，详见[修复与验证记录](project-issue-fixes-20261010.md)。** 下文保留修复前的分析、行号与复现结果。

审查时确认了 **4 个已离线复现的问题**：两处调用额度的作用范围不正确，一处幻灯片修订流程失效，一处配套图示重复生成。

上一轮优化记录中的 28 个代码与测试文件，SHA-256 均与当前文件一致。本次重点检查调用额度、恢复、写作与交付之间的组合路径。相关的 **16 个现有测试通过**；新增诊断脚本复现了下面四项行为，表明这些组合路径尚未被已有用例覆盖。

| 优先级 | 问题 | 触发范围 | 已观察到的结果 |
| --- | --- | --- | --- |
| P1 | 恢复任务时重新生成调用额度标识 | Python 调用方传入 `artifact_store`、不传 `run_id`，随后传入 `resume_execution` 恢复 | 上限为 1，恢复前后各获得 1 个名额，实际使用两个计数文件 |
| P1 | 共享检索客户端的任务归属被后建 Agent 覆盖 | 两个 Agent 注入同一个原生 `ModelSearch` 实例 | 第一个任务的上限为 1，却发送 2 次 HTTP 请求；第二次用量记入尚未运行的第二个 Agent |
| P2 | 幻灯片结构问题没有传入页面编辑器 | 文字和引用通过，但缺少讲稿等结构检查失败 | 记录消耗 1 次修订，实际没有发送页面编辑请求，并停止自动修订 |
| P2 | 配套图示未沿用正文的无改善停止机制 | 配图反复返回相同的非法结构，配置 `max_revisions=4` | 同一张错误图生成 5 次，最终仍是同一条结构错误 |

P1 表示触发后会破坏调用上限或任务用量归属，应优先修复；P2 表示交付和效率缺陷。前两项有明确的 Python 集成前提，不能据此断言默认 Web 服务的所有任务都会突破额度。

## 1. 自定义存储目录下的恢复会重置调用额度

源码位置：[orchestrator.py](../deep_research/orchestrator.py#L811)，特别是第 814、815、823–826 行。

创建额度时，作用域使用 `run_id`、黑板中的 `_artifact_run_id`，最后回退到新 UUID。但新 UUID 只在 `self._artifact_store is None` 分支里写入黑板。调用方已经传入存储对象时，标识没有保存；恢复同一个工作流时，又产生一个 UUID，因而读取另一个空的额度记录。

复现通过真实的 `DeepResearchAgent.run()` 和 `resume_execution` 路径进行：同一 `ArtifactStore`、同一执行快照、`task_max_model_calls=1`；第一次运行后耗尽持久化额度，恢复后仍能预留一个新名额。结果为：

```json
{
  "limit": 1,
  "first_used": 1,
  "restored_used": 1,
  "same_counter_path": false,
  "checkpoint_has_budget_scope": false
}
```

诊断使用 FakeLLM 完成工作流，再显式耗尽真实的 `TaskCallBudget`，验证的是作用域和恢复机制，没有发送付费请求。现有测试覆盖了同一 scope 的恢复，以及带 `run_id` 的任务入口，没有覆盖这个调用方自带存储的组合。

建议将调用额度标识作为独立的任务状态持久化，首次运行时保存，恢复时读取，避免其生命周期依赖是否由编排器创建存储对象。补充“自定义存储、不传运行 ID、恢复后额度仍耗尽”的入口测试。普通服务路径有稳定 `run_id` 时，不触发本次复现的问题。

## 2. 共享检索实例会串用任务额度与用量记录

源码位置：[orchestrator.py](../deep_research/orchestrator.py#L419)、[tools/base.py](../deep_research/tools/base.py#L64)、[model_search.py](../deep_research/tools/model_search.py#L148)。

Agent 构造函数对注入的检索客户端直接执行 `set_tracer(self.tracer)`。这个赋值修改共享对象本身。第二个 Agent 使用相同客户端时，第一个 Agent 的检索也开始读取第二个 Agent 的 tracer，包括调用额度和 token 记录。

复现使用原生 `ModelSearch` 和 HTTP Mock：

1. 第一个 Agent 的额度为 1，第一次检索正常消费该名额。
2. 此时再次检索正确抛出 `ModelCallLimitExceeded`，Mock 收到的请求仍为 1 次。
3. 构造第二个 Agent，并注入同一个检索客户端。
4. 再从第一个 Agent 发起检索，请求成功发出；它自己的额度记录仍为 1，而第二个 Agent 增加了 3 个测试 token。

这既影响费用限制，也影响任务用量的可追溯性。若第二个任务自身也启用了额度，第一个任务会消费第二个任务的名额。该赋值行为在 Git HEAD 中已经存在；新增任务额度功能扩大了它的影响。

建议为每个运行绑定独立的检索包装器，或把 tracer 和额度显式置于请求上下文；底层 HTTP 连接池可以共享。避免仅在调用前重新赋值共享对象，这在并发请求中仍然存在竞争。复合检索、密钥池也应传递同一运行上下文。

## 3. 幻灯片结构检查与局部编辑之间缺少问题传递

源码位置：[writers.py](../deep_research/workbench/writers.py#L456)、[slide_edit.py](../deep_research/workbench/slide_edit.py#L54)、[completion_repair.py](../deep_research/workbench/completion_repair.py#L108)。

写作者先根据正文检查结果开启 `local_revision`，之后把 `slide_quality()` 发现的缺讲稿、标题过长、内容密度等问题加入 `assessment.hard`。这些新增问题没有进入 `local_problems`，也没有相应的页面定位信息。

页面编辑器只从正文核验决定和 `local_problems` 选择页面。因此，当正文和引用都通过、只有结构问题时，页面集合为空，编辑器直接抛出“没有可定位的待修订页面”。上层把它记为 `revision_failed`；交付前修复也会因这个停止原因跳过。

复现使用真实 `SlideWriter._write_checked()`、结构检查和页面选择代码；仅将已知正确的正文核验替换为通过结果。一页缺少讲稿的幻灯片，配置允许修订 2 次，结果是：

```json
{
  "schemas": ["SlideDeck"],
  "revisions_used": 1,
  "budget_limit": 2,
  "stop_reason": "revision_failed"
}
```

这里只有初稿的 `SlideDeck` 请求，没有 `SlideTextEdits` 请求。最终仍保留“缺少有效演讲备注”和“分页后缺少独立讲稿”两条问题。这是此次局部修订扩展带来的回归：HEAD 中幻灯片没有进入这个局部编辑分支。

建议让结构检查返回带页码的问题对象，将单页问题送入局部编辑；对于必须调整页数或整体时长的问题，选择明确、受预算约束的结构修订流程。补充写作者入口测试，覆盖“正文通过、仅缺讲稿”，而不只测试页面编辑器本身。

## 4. 配套图示仍在相同错误下重复整图生成

源码位置：[writers.py](../deep_research/workbench/writers.py#L820)、[figure_edit.py](../deep_research/workbench/figure_edit.py#L104)。

正文的 `write_with_revisions()` 已具备无改善停止机制，但配套图示仍使用独立的 `for` 循环。其退出条件只有通过、不可修订、达到次数上限，没有比较相邻图示内容或错误集合。

当图示含重复节点 ID 时，真实结构检查能够确定失败；局部编辑返回 `None`，调用方继续请求完整图示。诊断模型每次都返回完全相同的错误结构，`max_revisions=4` 时仍进行了 5 次整图生成，最终错误始终为“图示节点为空或存在重复标识”。这里的 5 次是离线逻辑模型调用数，未测量真实费用。

该循环在 HEAD 中已经存在，是上一轮收敛优化尚未覆盖的场景。建议在图示循环中加入内容指纹和问题签名，首次发现重复且没有改善时停止，并将停止原因及已消耗修订次数持久化。选择最终图示时也应保留问题较少的版本。

## 验证材料与修复顺序

修复前的离线诊断脚本：[probe.py](../artifacts/project-issue-review-20261010/probe.py)。历史结果及测试选择：[validation.json](../artifacts/project-issue-review-20261010/validation.json)。该脚本保留旧行为断言，不作为当前回归脚本；修复后的用例见[修复记录](project-issue-fixes-20261010.md)。

在项目根目录执行：

```powershell
& 'C:/Users/Administrator/AppData/Local/Programs/Python/Python311/python.exe' artifacts/project-issue-review-20261010/probe.py
```

本次回归选择了任务额度、原生检索共用额度、幻灯片页面边界、正文无改善停止和配图绑定相关用例，合计 16 项通过。诊断脚本的 4 项复现全部完成；Ruff 和差异格式检查结果见验证记录。

上述两处调用额度问题、幻灯片的问题传递和配套图示收敛机制均已完成修复，并补充了入口及组合场景的回归用例。

本次检查范围为这些后端运行路径；验证使用离线模型替身及 HTTP Mock，没有进行真实模型质量、费用和时延测量，也未验证 PostgreSQL、浏览器端或部署环境。
