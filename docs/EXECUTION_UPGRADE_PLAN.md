# 提升执行方案（Execution Upgrade Plan）

> 承接 `UPGRADE_PLAN.md`（已完成 4 项）与 `docs/QUALITY_UPGRADE.md`（Phase 1/2 已交付）。
> 本方案针对当前架构**剩余的真实短板**，不再增加功能面，只补结构与可信度。
> 五个工作项互相解耦，可独立发布；总量约 4.5~5.5 天。
>
> 执行顺序：**W1 → W2 → W3 → W4 → W5**。W1 是唯一的架构级变更，先做；
> W5（拆 router）放最后，避免与 W1 在 `api.py` 上产生大面积冲突。

---

## 交付状态（2026-08-20）

| 工作项 | 状态 | 实测结果 |
|---|---|---|
| W1 执行层解耦 | ✅ 完成 | 杀 worker：API 全程 `/healthz` 200，新 worker **4.7s** 接管，断点续跑省 **66.7%** token |
| W2 第二检索后端 | ✅ 完成 | Brave 后端 + 并发合并去重；A/B 命令就绪，真实数字待 `BRAVE_API_KEY` |
| W3 judge 校准 | ✅ 完成（工具链） | 分层抽样导出 + κ/混淆矩阵报告；**数字待人工标注** |
| W4 意图回放 + 漂移 | ✅ 完成 | 四段式回放可定位到层；3 个漂移指标进 `/metrics` |
| W5 `api.py` 拆分 | ⚠️ 部分完成 | 抽出 admission/SSE 两个基础设施模块；**路由拆分未做**，原因见下 |

全量测试 **838 通过**，覆盖率 **87%**（门槛 80%），ruff + mypy 全绿。

过程中修掉两个被顺带暴露的真实缺陷：

1. **进程内迁移会静默关闭全部应用日志**（`alembic/env.py` 的 `fileConfig` 默认
   `disable_existing_loggers=True`）。API 启动时就在进程内跑迁移，因此线上
   `deep_research.*` 日志在迁移之后集体失声，且无任何报错。已修 + 加回归测试。
2. **`checkpoint.saved` 的 token 数不落库**（`Event.tokens` 顶层字段按设计不持久化），
   导致跨进程回放时「断点续跑省了多少」永远读到 0。已改为写进会持久化的 `data`。

---

## 现状判定（写方案的依据）

| 维度 | 判定 | 依据 |
|---|---|---|
| 编排引擎 / 图执行 | 健康 | `orchestration/` ↔ `workflow.py` ↔ `orchestrator.py` 分层清晰 |
| 恢复与 fencing | 健康 | checkpoint + `recovery_lease` + 可续期租约，chaos demo 有实测数字 |
| 证据门禁 | 健康（确定性部分） | 逐字匹配 + registrable domain 去重，对抗集可信 |
| **执行拓扑** | **短板** | 任务是 API 进程内 asyncio task，准入 `RunAdmission` 进程本地 |
| **检索多样性** | **短板** | `tools/` 只有 Tavily 一个真实实现，交叉印证上限受单一索引约束 |
| **judge 可信度** | **短板** | 语义判定与 LLM-as-judge 均无人工标注基线 |
| **意图可观测** | **短板** | 上下文消解不可回放；L2 本地模型无漂移信号 |
| **`api.py` 内聚** | **短板** | 2618 行，路由/SSE/准入/恢复/agent 构建混在一起 |

---

## W1 · 执行层解耦为独立 worker（约 2 天，最高优先级）

**一句话卖点**：API 与执行分离，worker 可 `--scale 3` 水平扩展；杀任意 worker，任务被其他
worker 在 X 秒内接管续跑，API 全程无感。

### 为什么这是最值得做的

它把简历叙事从「单机 Agent 运行时」升级为「API/执行分离的可水平扩展运行时」，而且**复用
现有全部可靠性设施**——租约 fencing、checkpoint、断点续跑、chaos demo 全部原地生效，
只是把 `kill -9` 的目标从 API 换成 worker，恢复故事反而更硬（杀 API 不影响进行中的研究）。

### 已经具备的前提（无需改动）

| 能力 | 现状 | 位置 |
|---|---|---|
| 跨进程事件订阅 | **已支持**。本地 hub 不存在时 SSE 自动降级为仓储轮询，含终态补发、attempt 回绕、指纹去重 | `api.py:1222` 起的 durable tail 分支 |
| 跨进程取消 | **已支持**。执行侧监控循环轮询 `get_run_status() == "cancelling"`，`task.cancel()` 只是同进程加速 | `api.py:1487`、`api.py:1552` |
| 抢占式领取 | **已有原型**。恢复扫描就是 claim loop：`acquire_lease` → fence 后重读 → `prepare_resume` → 执行 | `api.py:747 _recover_orphaned_runs` |
| 租约续期与过期接管 | 已支持 | `renew_lease` / `_LEASE_RENEW_INTERVAL_SECONDS` |

### 唯一的真实障碍

`_recover_orphaned_runs` 当前的两条分支在 worker 模式下会误判**刚入队、尚未开始**的任务：

- `execution is None`（还没有 `workflow_run` 行）→ `continue`，永远不被领取；
- `not execution.checkpoint` → **`set_status(..., "error")`**，新任务会被直接判死。

原因是现在无法区分「从未开始，等待执行者」和「首个 checkpoint 前崩溃」。必须引入显式状态。

### 实施步骤

1. **数据模型：新增可领取标记**（`alembic/versions/2026xxxx_0019_run_queue.py`）
   - `research_run` 增列 `claimable_at: DateTime|None`（入队时间；`None` = 不可领取）
   - `research_run` 增列 `claim_attempts: int = 0`（领取次数，用于毒任务熔断）
   - 索引 `ix_research_run_claimable (status, claimable_at)`，供 worker 轮询
   - 语义定义（写进迁移 docstring 与 `repository.py`）：
     - `status=pending` 且 `claimable_at` 非空 → 待领取，**不是孤儿**
     - `status=running` 且租约过期 → 崩溃，走现有恢复路径
     - `status=pending` 且 `claimable_at` 为空 且无 checkpoint → 才是旧逻辑的 error 分支

2. **仓储接口：抽出领取原语**（`persistence/repository.py` + 两个实现）
   ```python
   async def claim_next_run(self, owner: str, *, now: datetime) -> str | None: ...
   ```
   - SQL 实现用 `SELECT ... FOR UPDATE SKIP LOCKED`（PostgreSQL）单语句领取并写租约；
     SQLite 走 `BEGIN IMMEDIATE` + 条件 UPDATE（单机开发够用，在 docstring 里注明差异）
   - InMemory 实现保持同语义，供单测使用
   - `claim_attempts` 超过 `max_claim_attempts`（默认 3）→ 置 `error`，原因 `poison_run`

3. **抽出可复用的执行入口**（新建 `deep_research/worker.py`）
   - 把 `api.py` 的 `_build_agent` / `_execute` / `_execute_with_admission` 中**不依赖
     FastAPI 的部分**下沉为 `RunExecutor`，以 `repo + settings + catalog` 构造，
     `app.state` 依赖改为显式参数（`live` hub 变成可选注入，worker 侧传 `None`）
   - `api.py` 保留薄封装，行为零变化——这一步必须先跑通全量测试再继续
   - worker 主循环：`claim_next_run` → `RunExecutor.execute` → 释放租约 → 继续；
     空闲时退避轮询（默认 1s，可配 `DR_WORKER_POLL_SECONDS`）
   - 复用现有 `RunAdmission` 作为**单 worker 并发上限**；全局上限 = worker 数 × `max_active_runs`
   - `SIGTERM` 优雅退出：停止领取新任务，**不取消**在跑任务，等待或超时后释放租约留给恢复

4. **API 侧改为入队**（`api.py:1973 create_run`）
   - 新增配置 `DR_EXECUTION_MODE = inline | worker`，**默认 `inline`**（保持现有行为，零回归风险）
   - `worker` 模式下：创建 run 行 + 写 `claimable_at`，直接返回 202，不 `create_task`
   - `inline` 模式下：走现有路径（桌面版 / 单文件部署必须保留）
   - `resume_run` 在 worker 模式下同样改为置 `claimable_at` 并返回

5. **恢复扫描归位**
   - 恢复循环从 API 移入 worker（`inline` 模式仍留在 API）
   - 修正上面「唯一的真实障碍」中的两条误判分支

6. **部署**（`docker-compose.yml` + `Dockerfile`）
   - 新增 `worker` service，同镜像不同 entrypoint（`python -m deep_research.worker`）
   - API service 不再执行迁移竞争：迁移仍由 API 启动时做，worker 等待迁移完成再领取

### 验证与验收

- 新增 `tests/test_worker.py`：
  - 两个 worker 并发领取 20 个任务，**无重复执行**（断言每个 run 的 attempt 与事件不重叠）
  - 领取后立刻杀 worker A，worker B 在租约过期后接管，**断点续跑**（复用现有 checkpoint 断言）
  - 毒任务连续领取失败 3 次 → `error` + `poison_run` 原因
  - `inline` 模式行为与改造前逐条一致（回归）
- `scripts/chaos_demo.py` 增加 `--target worker` 模式：杀 worker 而非 API，
  输出**接管耗时**与**续跑节省**两个数字；README 亮点区更新
- `make test` 覆盖率门 80% 不下降

### 风险与回滚

- 全部新行为在 `DR_EXECUTION_MODE=worker` 之后；默认 `inline` 意味着**回滚 = 改一个环境变量**
- 步骤 3 的重构是最大风险点，要求「行为零变化」独立成一个 commit，先绿测再往下

---

## W2 · 第二检索后端与召回对照（约 1 天）

**一句话卖点**：交叉印证门禁不再受单一索引召回约束；用双后端对照量化「独立发布方数」的提升。

### 问题

`tools/` 只有 Tavily（+ key 主备池）。「≥2 个独立发布方」这一最强卖点的**上限完全由一个
检索索引决定**：同源伪双源拦得住，但「两家独立媒体都没被召回」这类漏报当前测不出来。

### 实施步骤

1. `tools/base.py` 的 `SearchTool` 补 `backend_name` 抽象属性（`tavily_pool.py` 已有实现，
   `tavily_search.py` 需补），使 manifest 中的后端标识对所有实现一致
2. 新增第二后端 `tools/brave_search.py`（或 SerpAPI / DuckDuckGo，取决于可获得的 key），
   实现同一 `SearchTool` 协议
3. 新增 `tools/composite.py`：并发调用 N 个后端 → 按归一化 URL + 内容哈希去重合并 →
   保留每条 Source 的 `backend` 来源标记（写入现有 source snapshot，供审计）
   - 单后端失败不阻断（记审计事件），全部失败才向上抛
4. 配置：`DR_SEARCH_BACKENDS=tavily,brave`，为空时行为与现在完全一致
5. **对照实验**（这一步才是产出）：`eval/run_eval.py` 同一数据集分别跑
   `tavily` / `tavily+brave`，对比现有确定性指标中的
   **独立发布方数 / 交叉印证率 / 报告准入率**，结果落 `eval/results/`

### 验收

- 双后端下「独立发布方数」中位数相对单后端的提升幅度（数字写进 README）
- 单后端故障注入测试：一个后端抛异常，合并结果仍返回另一个后端的 Source 且有审计事件
- 去重测试：两后端返回同一 URL 的不同抓取版本 → 快照按内容哈希版本化，不重复计独立发布方

---

## W3 · judge 校准集（约 1 天）

**一句话卖点**：LLM 判定不是「我说它对」，而是与人工标注对齐率 X%、κ = Y。

### 问题

逐字匹配是确定性的（所以对抗集 100% 可信），但 `semantic_status: supported` 与
`eval/run_eval.py` 的四维打分都依赖 LLM judge，**judge 自己没有基线**。
面试必问「你怎么知道 judge 判对了」，当前无答案。

### 实施步骤

1. 从已持久化的 benchmark run 中抽样 **60~80 条 finding**（分层抽样：
   supported / unsupported / conflicted 各占一定比例，避免全是易判样本）
2. 导出为 `eval/calibration/semantic_cases.jsonl`：
   `claim / evidence_quote / source_excerpt / judge_label`，**人工补 `human_label`**
3. 新建 `eval/judge_calibration.py`：计算 judge 与人工的
   **accuracy / precision / recall / Cohen's κ** + 混淆矩阵，输出
   `eval/results/judge-calibration-<date>.md`
4. 对报告四维打分做**小规模一致性检查**：同一批报告让 judge 跑 3 次，
   报告**评分自一致性**（同一报告多次打分的标准差），量化 judge 噪声
5. 并入 pytest：κ 低于阈值（建议 0.7）时**告警而非失败**（数据集小，避免脆性 CI）

### 验收

- `eval/results/judge-calibration-<date>.md` 存在且含混淆矩阵与 κ
- README 的评测章节从「四维打分」改为「四维打分（judge 与人工一致率 X%，κ=Y，
  用于版本回归而非绝对断言）」——**数字不好看也照写**，这本身是可信度的体现

---

## W4 · 意图消解可回放与模型漂移监控（约 1 天）

**一句话卖点**：用户投诉「你把刚才那个数据库理解错了」时，能定位到是 L1 依赖检测错、
还是 L3 改写错；本地意图模型的分布漂移有信号而非静默降级。

### 4.1 上下文消解可回放（落地 `docs/TODO_INTENT_CONTEXT_AUDIT.md` 的既有设计）

1. 按该 TODO 文档给出的结构，在 `intent/types.py` 增加 `ContextResolution` 模型，
   挂到 `IntentDecision.context_resolution`
2. `intent/context.py` 的 `resolve_followup()` 返回值扩展为携带
   `history_used`（`render_history()` **实际使用**的最近 3 轮，非客户端传入的条数）、
   `dependency_signal`、`resolver_tier`、`reason`
3. 持久化：随现有意图审计事件落库；`GET /api/runs/{id}` 的意图段返回该结构
4. 前端 RunPage 意图卡片增加「消解回放」折叠区：原问题 → 使用的历史 → 改写结果 → 层级与原因

### 4.2 L2 本地模型漂移监控

1. 推理时记录**预测置信度分布**与**级联分流占比**（L1 命中 / L2 命中 / L3 兜底）为指标，
   接入现有 `/metrics`（Prometheus）
2. 新增滚动窗口告警条件（先只记录，不阻断）：
   - L3 兜底占比超过基线（`eval/intent_eval.py` 产出的离线分流占比）一定倍数
   - L2 平均置信度低于阈值
   - `unknown` 弃权率突增
3. `eval/intent_eval.py` 输出中固化基线分流占比，写入 `eval/baselines/intent.json`

### 验收

- 新增测试：构造一次指代消解，断言 `context_resolution` 四类错误（输入错 / L1 错 /
  L3 错 / 正常）都能从回放结构中区分出来
- `/metrics` 暴露三个新指标，且在无流量时不产生除零

---

## W5 · `api.py` 拆分（部分完成）

**目的**：消除项目里唯一明显违背自身分层水准的地方。**纯结构调整，零行为变化。**

### 已完成

```
deep_research/http/
├── admission.py    # RunAdmission / RunAdmissionLease / RunAdmissionLimit（151 行）
└── sse.py          # _sse / _stream_run_sse + 全部 SSE 调参常量（300 行）
```

`api.py` 2618 → **1969 行**。两个模块经 `api` 命名空间再导出，既有调用方不变；
测试里只改了 monkeypatch 的目标模块（断言一条未动）。

### 未做：路由拆分（以及为什么）

原计划把 route handler 拆进 `http/routers/*.py`。动手前评估后**主动停在这里**：

- 路由处理函数依赖 `_execute` / `_execute_with_admission` / `_track_run_task` 等
  一批 app-aware 的模块级函数。要拆路由，必须先把它们也搬进新模块；
- 而**测试是按 `monkeypatch.setattr(api, "_execute", …)` 写的**（约 20 处）。函数一旦搬家，
  路由调用的就不再是 `api` 命名空间里的那个名字，这些桩全部失效——不是「断言不变的搬运」，
  而是要重写一批测试的注入点；
- 收益是可读性，风险是给一条已经全绿的关键路径引入回归。**这笔交换在当前阶段不划算。**

真要做，正确的顺序是先把 `_execute_with_admission` / `_track_run_task` /
`_settle_prestart_cancellation` 收进 `http/runner.py` 并让测试改注入 `runner`，
再拆路由。属于独立的一次改动，不该和 W1 挤在一起。

## 时间与依赖

```
W1 执行层解耦        ████████  2.0d   ← 唯一架构级变更，先做
W2 第二检索后端        ████     1.0d   ← 独立
W3 judge 校准          ████     1.0d   ← 独立，需要人工标注时间
W4 意图回放+漂移       ████     1.0d   ← 独立
W5 api.py 拆分          ██      0.5d   ← 必须在 W1 之后
                    ─────────
                              5.5d
```

W2/W3/W4 之间无依赖，可任意调序或并行。W5 必须最后。

---

## 完成后可写进简历的行

- **可扩展执行**：API 与执行分离，worker 抢占式领取 + 租约 fencing，`--scale 3` 水平扩展；
  杀任意 worker 后 _s 内被接管续跑，API 无感（数字待 chaos demo 产出）
- **检索多样性**：双后端合并去重，独立发布方数中位数提升 _%，交叉印证门禁通过率提升 _%
- **可信度量**：语义判定与人工标注一致率 _%（κ=_），judge 用于版本回归而非绝对断言
- **意图可观测**：上下文消解四段式可回放，线上问题可定位到具体层级；本地模型漂移有指标告警

## 明确不做

- 继续增加工作流模板（已 9 种）或前端页面 —— 广度已过剩，边际收益为负
- 向量库 / 长期记忆 / human-in-the-loop —— 与项目一撞车，维持 `UPGRADE_PLAN.md` 的既有判断
- 拆 `guardrails.py` —— 内聚合理，拆了只是搬家
- 多租户与授权 —— **有意的范围边界**，在 README 中显式声明，而不是当成待办
