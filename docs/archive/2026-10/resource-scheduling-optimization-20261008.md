# 资源调度优化实施记录

日期：2026-10-08。基线：`main@ed97c27`。对应[前次审查](resource-scheduling-review-20261008.md)的 F1～F8。本次修改工作区代码、配置说明及回归测试；尚未提交或部署。

## 已实施的行为

| 审查项 | 实施内容 | 验证结果 |
|---|---|---|
| F1 供应商背压 | 托管执行中，`ProviderBusyError` 进入独立的 `_resource_wait` 路径，持久化可领取时间并释放执行租约；按向上取整的 `retry_after` 延期。跳过节点内部重试，不增加故障恢复、无进展或毒任务计数。 | 繁忙后状态保持 `running`，冷却期间无法领取，恢复时跳过已完成步骤；失败预算为 0 仍可等待资源。 |
| F2 入队公平性 | 新任务与失败任务重新入队均在准入事务内检查身份在途数。API 默认身份上限为服务总在途上限减 1，最小为 1；可显式调整。 | 总容量 40 时，A 最多进入 39 个任务，B 仍可进入一个 quick；幂等重放不重复计数，切换项目不能绕过身份额度。 |
| F3 闲置份额 | 增加 `strict` / `borrow` 策略。borrow 仅在没有等待身份需要其未用重任务份额时放宽身份限制。 | 默认 8 并发下，单身份 strict 允许 4 个重任务，borrow 允许 7 个；两种模式均保留轻任务名额。 |
| F4 worker 容量 | 服务全局上限与单进程上限分开配置，启动日志同时记录两者；本地满载后停止领取，数据库继续保护全局总量。 | 三个 worker 的本地上限为 2、3、4，全局上限 8，受控领取结果为 2、3、3。 |
| F5 领取数据量 | 候选 SQL 只读取 ID、身份、代价、类别、优先级、可领取时间和租约字段，选中后读取完整运行状态。 | 无效领取加载 0 份 workflow checkpoint；成功领取仅加载 1 份。保留完整身份候选集合，未用全局 LIMIT 截断队列。 |
| F6 供应商等待 | 同一事件循环、连接池和凭据共用一个等待队列与轮询者；释放后主动唤醒。跨连接池通过带抖动的渐进轮询发现变化。占满时只读检查，实际放行仍在数据库事务中原子确认。 | 同样 8 个等待者、0.25 秒窗口，本机复现从原 115～120 条 SQL 降为 2 条 SELECT，0 次写入。跨连接池准入、等待取消、租约回收与续期竞争均有测试。 |
| F7 工作流图 | 使用依赖就绪队列和节点并发上限。输入按祖先节点结果构造，输出按稳定拓扑顺序合并，保存基线和节点差量以支持恢复。 | successor 在无关 slow 分支结束前启动；宽图执行受上限约束；条件边、join、核验、失败隔离、取消和已完成节点恢复测试通过。 |
| F8 渲染隔离 | 渲染客户端与消费者分离。worker 模式的 API 不启动轮询或本地 drainer；worker 作用域启用消费，API 从共享队列等待结果。 | 内存队列与独立 SQLite 仓储均验证了 API 不执行渲染，独立消费者产出后 API 正常返回。inline 路径继续工作。 |

实现入口：[执行恢复](../deep_research/execution_policy.py)、[准入与公平调度](../deep_research/scheduling.py)、[SQL 仓储](../deep_research/persistence/sql_repository.py)、[worker](../deep_research/worker.py)、[供应商协调](../deep_research/provider_limits.py)、[工作流图](../deep_research/workflow.py)、[渲染服务](../deep_research/render_service.py)。

资源繁忙信号还贯通了 Key 池、多后端检索、子问题调度、抽取、语义核验、正文支持核验、公式及全文核验。健康的备用服务可继续提供结果；没有可用结果时，繁忙不会被当作空研究结果或核验质量失败。认证失败仍不进入资源等待。

## 配置与兼容性

配置已接入 [Settings](../deep_research/config.py)、[环境变量示例](../.env.example)和 [Compose](../docker-compose.yml)。

| 环境变量 | 默认行为 | 用途 |
|---|---|---|
| `MAX_ACTIVE_RUNS` | 原值 8 | 数据库协调的服务总执行上限。 |
| `MAX_QUEUED_RUNS` | 原值 32 | 与执行上限共同组成总在途容量。 |
| `MAX_INFLIGHT_RUNS_PER_IDENTITY` | 留空：总在途容量减 1，最小 1 | 保护其他身份的入队机会。单租户批处理可显式设为服务总在途容量，例如 40。 |
| `DR_WORKER_MAX_ACTIVE_RUNS` | 留空：沿用全局上限 | 限制单进程领取量，例如小 worker 设为 2；有效值不会超过全局上限。 |
| `DR_RUN_SCHEDULING_POLICY` | `strict` | `borrow` 可借用闲置重任务份额。 |
| `DR_WORKFLOW_MAX_CONCURRENCY` | 4 | 单张工作流图中同时执行的节点数，与研究子问题的 `MAX_CONCURRENCY` 分开。 |
| `DR_PROVIDER_MAX_WAITERS` | 128 | 每个本地凭据等待队列的上限；超额进入资源延期。 |

这些示例不是生产容量测量。本次没有修改 `.env` 或线上配置；运维文档记载的生产并发 1、渲染并发 1、1 GiB 容器配置不在本次变更范围。

借用模式不会抢占正在运行的长任务。借满后到来的新身份，需要等待已有任务结束；要求严格交互延迟的部署继续使用默认 strict。轻任务保留名额适用于全局并发至少为 2 的部署，单并发无法抢占已运行的任务。身份默认额度保护有限的共享入队空间，并不保证全队列已被多个身份占满后仍能接受新请求。

资源等待保留任务总截止时间，不无限延长任务生命；达到总截止时间时结算失败。等待状态继续使用现有运行状态与 `claimable_at`，无需新增数据库状态或迁移。

执行中的图 checkpoint 在顶层保存版本化 `_graph_state`，图成功完成后移除临时调度日志。节点只读取其依赖链数据，隐式读取其他分支输出的自定义图应补上依赖边。结果和反思记录按生产节点及位置标识合并，重复问题文本也不会让一个分支覆盖另一个分支的结果。恢复时，历史引文修复和任务契约升级优先于旧日志。旧 checkpoint 没有日志时，以已保存黑板为基线继续执行；没有序列位置元数据的日志可在读取时补齐。

图 checkpoint 的步骤状态与输出在异步写入前一起冻结，避免将尚未合并输出的节点保存为已完成。

本轮未加入 worker CPU/内存自适应领取、按历史实际耗时校准代价或跨进程通知系统；当前改动提供显式容量限制、空闲份额借用及有界轮询。

## 验证

当前已完成：定向回归、真实 SQLite 独立连接池竞争、内存仓储语义、假模型执行恢复及记录型渲染隔离。未调用真实付费模型或线上服务。

- `ruff check deep_research tests alembic scripts eval`：通过。
- `mypy deep_research`：274 个源文件通过。
- 本次修改的 Python 文件格式检查：通过；全仓仍有既有的未格式化文件，未为本次优化统一改写。
- 当前非 PostgreSQL 测试集共 **3750 项，按用例 ID 去重后的最终结果全部通过**；不存在漏测或仍失败的用例。
- 覆盖率 **82.43%**，达到项目要求的 80%。最后修改的供应商异常与 Key 池模块已清除旧覆盖数据并重新测量。
- 完整流程组 51 项通过；后续补跑包含剩余用例及变更模块。引文恢复和 Key 池提示兼容性两个中途失败均已修复并通过复测；最后的兼容性组 95 项通过。
- PostgreSQL 与真实混合负载压测：尚未执行。本机 Docker 的 Linux daemon 未运行。
- Compose 配置解析通过，新环境变量可传入服务。

worker 模式的 HTTP 测试现在显式启动独立渲染消费者，不再依赖 API 隐式渲染。Windows 下使用项目内的短目录 `.rt` / `.ru` 存放 pytest 临时文件，避免深层渲染路径超过本机文件路径限制；这两个专用目录会由 pytest 重建。

本轮临时文件已保留到 Git 忽略的 `.pytest_cache/resource-scheduling-20261008/`，工作区根目录不再留有 `.rt` / `.ru`。

验证产物：[最终去重汇总](validation/resource-scheduling-summary-20261008.json)、[覆盖率](validation/resource-scheduling-coverage-20261008.xml)、[完整流程组](validation/resource-scheduling-integration-20261008.xml)、[剩余及变更模块](validation/resource-scheduling-final-20261008.xml)、[最终兼容性复测](validation/resource-scheduling-compatibility-20261008.xml)。分组原始 XML 保留当次结果，最终去重汇总采用同一用例最近一次执行的结果。

回归入口：

```powershell
python -m pytest -q -m "not pg" -p no:cacheprovider --basetemp=.rt --tb=short tests/test_resource_scheduling.py tests/test_scheduling_repository.py tests/test_service_coordination.py tests/test_execution_recovery.py tests/test_run_recovery_repository.py tests/test_graph_ready_scheduling.py tests/test_graph_workflow_run.py tests/test_checkpoint_resume.py tests/test_step_policies.py tests/test_recovery_graph_progress.py tests/test_worker.py tests/test_worker_mode_api.py tests/test_render_service.py
```

完整复跑命令：

```powershell
$env:COVERAGE_FILE = "docs/validation/.coverage-resource-scheduling-20261008"
python -m pytest -q -m "not pg" -p no:cacheprovider --basetemp=.ru --tb=short --cov=deep_research --cov-report=term --cov-report=xml:docs/validation/resource-scheduling-coverage-20261008.xml --cov-fail-under=80 --junitxml=docs/validation/resource-scheduling-full-20261008.xml
```

前次审查报告与离线探针保留为基线记录，当前版本的验收以以上回归测试为准。SQL 次数与延迟均为本机合成负载结果，不能直接推算线上延迟分位数或 CPU 利用率。
