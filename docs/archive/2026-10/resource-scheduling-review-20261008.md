# 资源调度与任务分配审查

审查日期：2026-10-08。代码基线：`main@ed97c27`。范围：研究任务准入、持久化公平调度、worker 分配、供应商限流、工作流 DAG、渲染执行与恢复。

**优先修复供应商背压被当作终止错误的问题，再完善准入公平性、进程容量和数据库调度开销。** 本次确认 1 处错误处理缺陷、1 处多身份准入缺口，以及 6 处策略或性能优化点。现有数据库租约、调度记账、取消保护和共享并发限制可继续复用。

本文的“8 并发”算例使用源码默认值。[运维记录](IMPROVEMENT_PLAN.md#L10)记载生产为 inline、任务并发 1、渲染并发 1、应用容器 1 GiB；本次没有连接生产，因此不能把默认配置算例当作线上容量测量。容量调整应沿用该生产基线，先完成混合负载验证。

## 结论与优先级

P1 表示应优先处理的正确性或可用性问题；P2 表示有明确收益的调度、容量或性能改进。条件性问题均在下文说明。

| 编号 | 优先级 | 发现 | 证据 |
|---|---|---|---|
| F1 | P1 | 内部供应商限流未进入恢复路径，任务直接变成 error | 真实协调器异常分类 + RunExecutor 注入复现 |
| F2 | P1，多身份部署 | 单一身份可占满总在途额度，公平调度无法帮助尚未入队的其他身份 | 临时 SQLite 复现 |
| F3 | P2，策略取舍 | 无竞争时仍执行身份硬配额，默认 8 个名额只允许同一身份启动 4 个重任务 | 临时 SQLite 复现；代码与现有测试明确要求此策略 |
| F4 | P2 | 全局并发与单 worker 容量共用一个配置，缺少按进程能力分配 | 两个 Worker 的受控领取复现 |
| F5 | P2 | 领取前加载所有候选任务完整状态，并持有统一调度锁 | ORM 加载事件测量 |
| F6 | P2 | 供应商等待以短间隔写数据库轮询，竞争越多，协调开销越大 | SQL 语句计数 |
| F7 | P2 | 工作流图仍有整层屏障，已满足依赖的后继被无关慢节点拖住 | 事件门控复现 |
| F8 | P2，worker 部署 | worker 模式下 API 仍能启动本地渲染消费者 | API 调用链 + 记录型消费者复现 |

### F1：供应商背压会变成任务终止错误

**位置：** [provider_limits.py:28](../deep_research/provider_limits.py#L28)、[provider_limits.py:59](../deep_research/provider_limits.py#L59)、[llm.py:553](../deep_research/llm.py#L553)、[execution_policy.py:84](../deep_research/execution_policy.py#L84)、[execution.py:721](../deep_research/execution.py#L721)。

供应商进入冷却、RPM 额度耗尽或并发等待超时，都会产生 `ProviderBusyError`。它继承 `RuntimeError`，虽然带有 `status_code=429`，但 LLM 重试和任务恢复按异常类型识别，均未包含它。无其他可恢复异常链时，两处判断都返回 false。

默认研究分支并发为 4，同凭据供应商并发为 2，协调器等待上限为 `min(30, request_timeout)`。合法并发负载就可能触发这条路径。

**验证：** 真实 `ProviderCoordinator` 在名额被占用时产生的 8 个繁忙异常，全部被判断为不可重试、不可恢复。向真实 `RunExecutor` 的假 agent 注入同类型异常后，运行状态为 `error`，checkpoint 不含自动恢复记录。全过程没有调用外部模型。

**改进：** 将“等待资源”作为独立的可延期状态，持久化原因和 `not_before`，遵循供应商的 `retry_after` 后重新准入，及时释放运行名额。纯资源等待不应消耗故障次数或“无进展”恢复次数。只把异常加入现有重试列表还不充分，因为现有恢复退避也未使用该异常的等待时间。

**验收：** 超过供应商并发数的正常请求最终继续执行；本地额度等待不会生成终止错误；取消、租约失效仍优先，冷却期间不重复发送请求。

### F2：公平性只覆盖出队，未覆盖入队

**位置：** [api.py:1079](../deep_research/api.py#L1079)、[sql_repository.py:222](../deep_research/persistence/sql_repository.py#L222)、[sql_repository.py:289](../deep_research/persistence/sql_repository.py#L289)。

创建任务只检查全局 `max_active_runs + max_queued_runs`，没有按身份或任务类别保留的入队额度。后续 DRR 调度虽然按身份分配机会，但其他身份可能先在入队阶段被拒绝。

**验证：** 按默认总在途额度 40，身份 A 提交 40 个重任务，领取 4 个后仍有 36 个等待；身份 B 再提交一个 quick 任务，被 `RunQueueFullError` 拒绝。此时全局 8 个运行名额中仍有 4 个空闲。HTTP 创建路径会把该异常转换为 503。

**影响边界：** 这是多个身份共享研究队列时的可用性缺口。独立的论文问答准入路径不在这项复现范围内。

**改进：** 在同一个准入事务中加入身份配额与共享额度，并给新身份或交互类任务保留有限的入队空间。配额应支持单租户批处理场景，避免再次制造长期闲置；全局容量上限继续保留。

**验收：** 一个身份的积压不能消耗所有受保护的入队机会；幂等重放仍返回原任务，多 API 并发创建仍满足全局上限。

### F3：身份硬配额压低无竞争场景的名额利用率

**位置：** [scheduling.py:127](../deep_research/scheduling.py#L127)、[test_scheduling_repository.py:94](../tests/test_scheduling_repository.py#L94)。

当前规则为：

- 重任务总上限：`max(1, maximum - 1)`。
- 单身份重任务上限：`max(1, (heavy_limit + 1) // 2)`。
- 这些限制不检查是否存在其他等待身份，也不检查是否有轻任务等待。

**验证：** `maximum=8` 时，重任务总上限为 7，单身份上限为 4。单一身份持续积压仍只能启动 4 个重任务。这是运行名额的利用率，不等于 CPU 利用率；生产记录中的 `maximum=1` 不触发该 4/8 算例。

**改进：** 把严格交互保护和批处理吞吐明确做成可配置策略。可先保留一个轻任务名额，对其他身份暂未使用的重任务配额支持按需借用；出现竞争后收紧后续准入。

**取舍：** 现有注释与测试明确要求硬保留，本项是策略优化。借出的长任务若不能暂停，新身份可能要等它结束才能运行。严格低延迟保证需要保留名额，或先实现可在 checkpoint 边界让出资源的任务分块；不能同时承诺无条件借满和即时回收。

### F4：全局并发与单 worker 容量耦合

**位置：** [worker.py:125](../deep_research/worker.py#L125)、[worker.py:191](../deep_research/worker.py#L191)、[sql_repository.py:316](../deep_research/persistence/sql_repository.py#L316)、[docker-compose.yml:149](../docker-compose.yml#L149)。

`Worker.capacity` 使用 `settings.max_active_runs` 计算本地余量；向仓储领取时，又把同一个值当作整个服务的并发上限。心跳只登记名称、活动数和时间，没有进程容量或执行能力。

**验证：** 在内存仓储上使用真实 Worker，8 个 quick 任务已就绪，让一个热 worker 连续领取，再让第二个 worker 领取，活动数得到 `8 / 0`。第二个 worker 的本地余量为 8，但全局名额已经用完。这说明代码允许这种分布，不代表所有并发启动都会必然分布成 8/0。

**改进：** 拆分服务总上限和 `worker_max_active_runs`；进一步按 worker 声明的执行能力和本地 CPU、内存压力控制领取。研究、渲染和命令执行可以有不同的本地额度。一个低内存 worker 应能限制自己领取 2 个任务，同时保留服务总上限 8。

**验收：** 异构 worker 各自遵守本地上限；总租约仍遵守数据库全局上限；worker 增减不会破坏租约 fencing 或重复执行保护。

### F5：无效领取也会加载大量 checkpoint

**位置：** [sql_repository.py:1010](../deep_research/persistence/sql_repository.py#L1010)、[sql_repository.py:1040](../deep_research/persistence/sql_repository.py#L1040)、[sql_repository.py:1073](../deep_research/persistence/sql_repository.py#L1073)、[orm.py:500](../deep_research/persistence/orm.py#L500)。

取得 `run-admission` 锁后，查询选择整个 `ResearchRun` 和 `WorkflowRunRow`。所有候选的 input、output、definition、checkpoint 都会加载和反序列化，然后才进行身份配额筛选。任务创建也使用同一个协调锁。

**验证：** 40 个任务各带 128 KiB 的合成 checkpoint，4 个已领取。下一次领取因身份配额返回空，但 ORM 仍加载 36 个 workflow，共 4,718,592 字节（4.5 MiB）的合成 payload。这还没有计入 JSON 包装和其他状态字段。

**改进：** 候选查询只投影调度需要的小字段：任务 ID、身份、类别、代价、优先级、可领取时间、租约字段。选中并取得租约后，再加载唯一任务的完整状态。先做这一改动即可缩短锁持有时间，无需更换队列基础设施。

**验收：** 返回空领取结果时不加载候选 checkpoint；成功领取只加载选中任务的完整状态。保持现有“某身份大量排队不能遮住另一身份”的测试，避免用简单的全局 `LIMIT N` 破坏公平性。

### F6：供应商名额等待放大数据库写压力

**位置：** [provider_limits.py:59](../deep_research/provider_limits.py#L59)、[provider_limits.py:100](../deep_research/provider_limits.py#L100)、[coordination.py:11](../deep_research/persistence/coordination.py#L11)。

等待者每隔最多 0.1 秒重新开启事务，执行协调行 INSERT/UPDATE、删除过期租约、读取状态和统计活动租约。即使没有可用名额，也会走数据库写锁路径；过期清理还扫描所有凭据的租约。

**验证：** 占用唯一名额后启动 8 个等待者，把等待阈值缩短为 0.25 秒。最终保存脚本的一次运行执行了 115 条 SQL，8 个请求全部因名额繁忙退出；其中有 23 次 INSERT、23 次 UPDATE、23 次 DELETE、46 次 SELECT。另一次运行是 120 条，具体次数和耗时受机器调度影响。

**改进：** 同一进程对同一凭据合并等待者，使用有界队列；释放时主动唤醒，跨进程采用通知或带抖动的渐进轮询。按最近租约到期时间或冷却结束时间安排下一次检查；将过期清理从每个等待者的热路径移走，并让活动租约计数直接过滤未过期记录。真实请求的最终准入仍需数据库原子确认。

**验收：** 固定真实请求吞吐下，增加等待者不会线性增加短周期协调写事务；供应商硬并发、RPM、崩溃后租约回收和取消释放仍正确。

### F7：工作流图仍等待整层完成

**位置：** [workflow.py:858](../deep_research/workflow.py#L858)、[workflow.py:902](../deep_research/workflow.py#L902)、[workflow.py:999](../deep_research/workflow.py#L999)、[workflow.py:1023](../deep_research/workflow.py#L1023)。

子问题调度 [scheduler.py:75](../deep_research/scheduler.py#L75) 已采用依赖就绪策略；工作流图 `WorkflowEngine._run_graph` 仍逐层创建任务，并等待整层 `asyncio.gather`。两层调度的行为不一致。

**验证：** 构造 `root → fast、slow`，并且只有 `fast → successor`。fast 已完成，但 successor 直到无关的 slow 被释放并结束后才启动。该复现用事件门控，没有真实网络请求。

**改进：** 工作流层也维护就绪队列和未完成前驱计数，节点完成后只唤醒其后继。需要同时保留条件边、join_mode、失败策略、checkpoint 恢复与稳定的结果合并语义，避免仅替换 gather 后让不同节点读取到不一致的黑板。

**资源补充：** 当前每层和每个分支都深拷贝完整黑板；宽图还会同时创建全部节点任务。现有 run 级信号量由 researcher 使用，不能直接限制任意 agent 节点。可在改造时加入节点执行上限，并逐步改为不可变输入快照加增量结果。

**验收：** 后继在自身全部必要前驱完成后就能启动；无关慢分支不阻挡它；并行结果、条件跳过、失败恢复和取消测试继续通过。

### F8：worker 模式没有隔离 API 的渲染消费

**位置：** [api.py:915](../deep_research/api.py#L915)、[api.py:929](../deep_research/api.py#L929)、[render_service.py:90](../deep_research/render_service.py#L90)、[render_service.py:329](../deep_research/render_service.py#L329)、[render_service.py:201](../deep_research/render_service.py#L201)。

API 生命周期无条件启动渲染轮询；只有研究任务的嵌入式 worker 受 execution_mode 判断控制。渲染 `reserve()` 和 `request()` 还会直接调用 `wake()`，启动本进程消费者。消费者通过 `RenderProcess` 启动渲染子进程，仍使用该 API 容器的资源额度。

**验证：** 对 `Settings(execution_mode="worker")` 构造同一渲染服务，调用 reserve，记录型消费者确实在本地启动。该场景用假的执行函数，不执行真实 PDF/PPT 渲染；API 接入点由上述源码确认。

**改进：** 明确区分渲染队列客户端和消费者。worker 部署中的 API 可以提交、查询和等待结果，只有获准的 worker 可以消费。应同时控制生命周期轮询与 reserve/request 的本地唤醒，单独关闭 API 的 poll_pending 不够。

**验收：** worker 模式下 API 处理导出请求时不启动渲染执行；独立消费者完成任务后 API 正常取得结果；inline 模式仍可独立完成渲染。已有数据库租约和共享文件锁容量保护应继续保留。

## 建议实施顺序

1. **先修恢复语义和减少调度开销：F1、F5。** 两者源码边界明确，可以分别提交。F1 补“本地容量繁忙 → 延期 → 继续”的执行级验证；F5 保留现有公平选择，只改变候选数据读取。
2. **补齐共享服务的准入和容量配置：F2、F3、F4。** 明确交互延迟与批处理吞吐目标，拆开全局上限、worker 本地上限和身份策略。保持生产当前限制，先用受控负载验证配置语义。
3. **为独立 worker 部署落实渲染边界：F8。** 在启用或扩大 worker 拓扑前完成，同时验证 API 等待跨进程结果的行为。
4. **按测量结果优化等待和任务图：F6、F7。** 前者降低共享数据库压力，后者缩短含并行支路任务的完成时间；两者都需要覆盖取消与恢复。

现有 `queue_seconds`、模型 `provider_wait_ms` 和服务状态统计可作为基础。建议补充“等待原因”、每个 worker 的本地活动数与容量、领取查询耗时及载入量、运行/渲染内存峰值，并按工作流类别观察完成时间和首份可用结果时间。调度代价可逐步用历史实际耗时与请求用量校准。

## 验证记录与复现方式

| 验证 | 结果 |
|---|---|
| 调度、仓储公平性、供应商协调、worker、渲染容量、子问题 DAG，共 9 个测试文件 | 114 passed，23 个 PostgreSQL 用例被筛除 |
| 工作流图、执行恢复、恢复边界、worker 模式 API，共 4 个测试文件 | 50 passed |
| 保存后的离线复现脚本 | 正常退出，输出 8 条 JSON 记录 |
| 业务代码 | 本次仅新增审查报告与复现脚本 |

合计 **164 项现有测试通过**。使用系统 Python 3.11 环境；仓库的 .venv 缺少 pytest，因此采用已有可用环境，没有安装或更新依赖。测试通过说明既有约束得到满足，新增复现揭示的是现有覆盖以外的行为和策略代价。

复现脚本：[resource_scheduling_probe_20261008.py](validation/resource_scheduling_probe_20261008.py)。脚本使用临时 SQLite、内存仓储、假模型/搜索客户端和记录型渲染消费者，运行结束清理临时数据。SQL 次数和耗时属于本机合成场景测量，不是生产压测数据。

```powershell
python docs/validation/resource_scheduling_probe_20261008.py
```

本次现有测试命令：

```powershell
python -m pytest -q -m "not pg" -p no:cacheprovider --tb=short tests/test_scheduling.py tests/test_scheduling_repository.py tests/test_memory_fair_scheduling.py tests/test_service_coordination.py tests/test_worker_embedded.py tests/test_worker_claim_tokens.py tests/test_worker_admission_cleanup.py tests/test_render_capacity.py tests/test_dag_schedule.py

python -m pytest -q -m "not pg" -p no:cacheprovider --tb=short tests/test_graph_workflow_run.py tests/test_execution_recovery.py tests/test_execution_recovery_edges.py tests/test_worker_mode_api.py
```

本次未测 PostgreSQL 多进程竞争、真实模型与渲染混合负载、线上延迟分位数。这些验证应在后续容量或部署拓扑变更前补齐。
