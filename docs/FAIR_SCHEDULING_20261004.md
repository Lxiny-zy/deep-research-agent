# 公平调度与统一持久队列

日期：2026-10-04。依据本地提交 `2b204ea`（checkpoint，未推送、未部署）的代码和当日复核编写。
该提交的历史失败见下文；后续 P0 代码与测试已修复并通过检查，提交 `85062aa`，未推送。没有新增模型调用。

## 当前行为

- inline 与 worker 两种拓扑都先把任务保存为无租约的可领取记录，再走同一套领取协议。
  inline 模式在 API 生命周期内运行内嵌队列消费者（`deep_research/api.py` 的
  `_make_inline_worker`），API 请求不再直接领取本地名额和租约；周期恢复只唤醒消费者。
- 身份为任务所有者；匿名和历史任务共用一个 `legacy` 桶，项目不能产生额外份额。身份之间
  按持久化亏损轮转领取：每轮补 1 点额度、上限 8，选中时扣除任务成本。额度与游标保存在
  `scheduler_identity`、`scheduler_state`，与租约在同一事务提交；额度写入失败时租约和
  领取计数一起回滚。
- 任务成本在入队时冻结：轻量档或短流程 1、标准档 2、深度档及其他 4，再按冻结的单任务
  并发数放大，最高 8。资源类别另算：只有内置 `qa`、`paper_qa`、`quick` 使用原始定义、
  未带外部计划或工作台契约、档位不是标准/深度时才是轻任务；轻量档研究成本低但仍属重任务。
  冻结配置中的尝试期限超过 2 小时时一律按重任务计，成本至少 2。学术问答请求走独立的
  问答请求队列（`deep_research/workbench/qa_jobs.py`），不经过此调度；研究队列里实际归为
  轻任务的，主要是未带工作台契约的内置快速检索。
- 执行上限 C ≥ 2 时，重任务最多占 C−1 个槽，单个身份最多占其中约一半，至少给轻任务留
  一个槽；同一身份的轻任务不受其重任务配额阻挡。C = 1 时无法抢占已在运行的长任务，
  不提供轻任务即时响应保证。
- 同一身份内按优先级和等待时间排序，每等待 30 秒提升一级；执行中和恢复冷却时间不计入
  等待。仓储层支持 0–2 三级优先级（默认 1），API 尚未开放设置，目前所有任务都是默认优先级。
- 取消结算改由消费者的 `settle_cancellations` 分页处理，不占执行槽、不抢其他执行者的租约。
  worker 模式下 API 的恢复扫描不再结算取消。
- 领取时追加一条 `schedule_dispatch` 事件，记录类别、成本、优先级和实际排队秒数，
  不改动 checkpoint 内容。

## 迁移 0038

`research_run` 新增 `schedule_cost`、`schedule_class`、`schedule_priority`（带取值约束；
历史任务默认成本 4、重任务、优先级 1）、身份查询索引和两张调度状态表。升级时把带非空
checkpoint、尚未入队的 pending/running 历史任务标为可领取；未过期租约仍阻止接管。新代码
依赖这些列，发布时须先迁移，并与新的状态/事件契约一起整体升级 API、worker 和前端。

## 2026-10-04 复核

- 调度、仓储、迁移、HTTP 队列、内嵌消费者、worker、队列生命周期、完成状态及恢复仓储
  相关 11 个测试文件：156 项通过，26 项 PostgreSQL 分支未运行。
- 后端全量（不含 PostgreSQL）：**2,564 通过、4 失败**，单独重跑稳定复现：
  1. `tests/test_intent_api.py` 两项仍拦截 `api._execute`，但创建任务已改为入队后由消费者
     执行。用户原始工作流选择现保存在 checkpoint，由 `deep_research/worker.py` 读取，
     需要在新路径上改写断言。
  2. `tests/test_migrate.py::test_search_queries_migration_preserves_existing_questions_and_roundtrips`
     只升级到 0037 就用当前仓储写入，缺少 0038 新增的列。
  3. `tests/test_worker_mode_api.py::test_recovery_scan_still_settles_cancelling_runs`
     仍要求 worker 模式下由 API 结算取消。需先确认新设计（只由消费者结算）是否接受，
     再改测试或补回 API 侧结算。
- Ruff 3 项：`deep_research/worker.py:491` 行过长；`tests/queue_helpers.py:10`、
  `tests/test_fair_queue_http.py:75` 各一项异步写法告警。Mypy 207 个模块通过。
- 前端 61 个文件、436 项通过。
- 全量排除的 58 项 PostgreSQL 分支、0038 在 PostgreSQL 上的升级与回退、多 worker 并发
  领取均未验证。

## P0 后续验证（2026-10-04）

- 先复现旧问题：针对性检查 4 失败、58 通过，Ruff 3 项；修复后相关四个测试文件 68 项通过。
- 意图路由断言覆盖 checkpoint 中的原始选择和真实队列领取后的消费者传参；没有显式选择时保持缺省，
  不能把自动路由结果当成用户选择。0036/0037 的历史迁移夹具改用对应版本 SQL，不调用当前 ORM。
- API 不参与 worker 模式取消结算；消费者结算后重复扫描返回 0。HTTP 调度测试改为等待执行事件，
  不再 sleep 轮询；测试队列 helper 保留 `asyncio.timeout` 并改名参数为 `seconds`。
- 非 PostgreSQL 全量：`python -m pytest -q -m "not pg" -p no:cacheprovider --basetemp "$TEMP/dr-p0-full-20261004"`
  → **2,568 passed, 58 deselected**，258.33 秒；唯一 warning 来自 ziamath 的弃用 API。
- `python -m ruff check .` 通过；`python -m mypy deep_research` 207 个模块通过。
- 前端 `npm test`：61 文件、436 项通过；`npm run lint`、`npm run build`、`npm run api:check` 退出码均为 0。
- 隔离 `postgres:16-alpine` 容器 `dr-p0-pg-20261004`：首次空库直接测试为 54 通过、4 失败，
  原因是共享测试库未初始化（缺少 alembic_version/coordination 等表）；执行 `python -m deep_research.migrate`
  后重跑 `python -m pytest -q -m pg -p no:cacheprovider --basetemp "$TEMP/dr-p0-postgres-final"`，
  **59 passed, 2568 deselected**，79.82 秒。包括原有并发领取测试和新增的 0038 两次升级/回退、
  历史 checkpoint、可领取状态及累计指标保留检查。此段保存了失败原因、命令与最终结果；生产库未访问。
  验证后已删除该隔离容器及其匿名数据卷。
- `git diff --check` 通过。`/.t*/` 已加入忽略；52 个临时目录的删除被自动审批拒绝，目录仍在。

## 仍未完成

1. 后续质量门与问答收敛仍需按最新计划继续，P0 验证通过不代表真实场景质量已通过。
2. 清理被自动审批拒绝的临时目录，以及按用户指令提交和推送。
3. 是否开放任务优先级由产品决定；开放前，“按优先级调度”只在仓储层生效。
4. 逐格式检查点、持久化交付队列、生产切换到独立 worker 及双副本故障验收，仍按
   [后续改进计划](IMPROVEMENT_PLAN.md) 推进。

## P0.2 取消超时提示实现（2026-10-04）

- 新增 `research_run.cancel_requested_at`（0039，可空）；与状态从 pending/running 转入 cancelling
  一起写入。重复取消及终态请求不改时间。旧记录保持 NULL，不使用任务创建时间推断取消耗时。
- 默认执行租约时长统一为 `EXECUTION_LEASE_SECONDS=120`；详情接口只对超过该时长、仍 cancelling
  且有可靠请求时间的任务返回 `status_notice`。页面显示提示并保持取消按钮禁用；结算后提示消失。
  提示只说明仍在等待执行服务，不断言 worker 已宕机，API 也不代替消费者改变状态。
- 先运行 16 项后端回归和 1 项页面回归，均复现字段/提示缺失；实现后通过。
- 新增 SQLite/PostgreSQL 0039 往返测试：旧记录保留、并发重复取消不覆盖时间、新连接读取时间一致；
  回退删除时间字段而保留状态和累计指标，重新升级不伪造已丢失的历史时间。
- 首轮后端全量发现 `create_all` 历史库已含新字段，迁移重复添加导致 2 失败、2,583 通过。
  已按现有迁移约定检查列后再添加；19 项针对性回归通过。
- 最终非 PG 全量（`--basetemp "$TEMP/dr-p02-full-final"`）：**2,585 passed, 60 deselected**，
  263.72 秒；Ruff 通过，Mypy 208 模块通过。前端 437 项、Lint、构建及 API 契约检查通过。
- 最终 PG 全量（`--basetemp "$TEMP/dr-p02-postgres-final"`）：**60 passed, 2585 deselected**，
  76.18 秒；隔离 `postgres:16-alpine` 的 `dr-p02-final-pg` 容器及匿名卷已清理，生产库未访问。
  首次验证使用的 `dr-p02-pg-20261004` 容器及匿名卷也已清理。
- P0 技术阶段门全通过；52 个临时目录实际清理仍未完成。缩小到单一明确路径的删除也被
  自动审批拒绝（`blocked by policy`），不再重试其他删除渠道，保留在计划中为搁置事项。
