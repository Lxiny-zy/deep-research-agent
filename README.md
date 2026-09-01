# Deep Research Agent · 多 Agent 深度研究系统

把「一个问题」自动**拆解 → 并行检索 → 反思补洞 → 综合成带引用的研究报告**的多 Agent 系统。

面向 AI Agent 工程岗位的简历项目，重点展示 **多 Agent 编排、并行 fan-out、来源安全策略、证据验证、流式可观测、自动化评估** 等工程能力（而非又一个对话机器人）。

---

## ✨ 亮点（面试可讲的点）

- **多 Agent 协作**：Planner / Researcher / Reflector / Synthesizer 各司其职，职责清晰。
- **意图识别与请求侧门禁**：用户 query 与检索来源各走一条意图判定通道。输入侧是「多轮指代消解 → 三级意图级联 → 槽位抽取 → 澄清判定」四步：级联内部为「正则规则 → 本地 TF-IDF+逻辑回归（0 token，随包分发的 JSON 权重，纯 Python 推理）→ LLM 兜底」的成本阶梯；12 类可路由任务意图（另有 `unknown` 弃权态）会生成可审计的执行策略，决定工作流、子问题数、反思轮数、并发和证据要求。槽位覆盖时间、领域、语言、实体、输出格式、读者、地域、来源类型、新鲜度等约束并注入 Planner；风险意图（越狱 / 套取系统提示词 / 越权指令）在研究开始前拒识并产出说明性报告。策略与风险都只能收紧用户配置，显式 workflow 始终优先（详见 [docs/INTENT_RECOGNITION.md](docs/INTENT_RECOGNITION.md)）。
- **Workflow-as-Data 编排引擎**：工作流以带版本的图数据（节点 / 边 / 条件 / Join 模式）落库执行。用户界面提供 deep（完整深度研究）、quick（快速检索）和 hsi_review（HSI/AI4S 文献审查）三种公共模板；其它控制原语由默认 planner-driven 运行时统一编排，历史模板保留为兼容入口，也可在前端画布自组工作流。`guarded` 仅是内部兼容别名，不出现在 UI，也不作为自动路由目标。
- **全局提示词与流程规则**：`framework/06_global_rules.md` 作为共享系统上下文注入内置、自定义和 planner-authored 的每个 Agent；默认 `DR_ORCHESTRATION_MODE=planner-driven`，因此提示词约束、计划与 artifact 交接对所有入口一致生效。
- **可靠性设计**：节点级超时 / 重试 / 退避 / fallback、token 预算、Blackboard checkpoint、崩溃后启动自动恢复，多实例场景用可续期租约 fencing 防止旧实例写脏数据。
- **API 与执行分离（可水平扩展）**：`DR_EXECUTION_MODE=worker` 时 API 只把研究入队，由独立 worker 进程抢占式领取执行（`SELECT … FOR UPDATE SKIP LOCKED` 选候选、租约条件更新做最终仲裁），可任意扩副本；全局并发＝副本数 × `MAX_ACTIVE_RUNS`。硬杀任一 worker，API 全程可用，另一个 worker 在租约过期后从 checkpoint 接管续跑。默认 `inline`（API 自执行），桌面版与单容器部署不受影响。
- **角色广场与多模型档案**：Agent 角色卡片与模型档案数据驱动可编辑，检索 key 支持主备池自动切换。
- **并行 fan-out**：子问题用 `asyncio` 并发检索，墙钟时间 ≈ 最慢的一条链，而非求和。
- **反思循环**：Reflector 自评证据是否充分，不足则自动补洞（loop-until-sufficient）。
- **来源策略门禁**：检索内容进入 LLM 前检查 URL scheme、非公网/歧义 IP、嵌入凭据，以及网页标题/正文/URL path/query/fragment 中的中英文 Prompt Injection 信号；隔离/拒绝决策进入结构化事件审计。
- **论断证据门禁**：Finding 必须携带来源原文 `evidence_quote`；程序执行归一化逐字匹配并记录内容哈希，只有 `verified` 且语义判定为 `supported` 的论断能进入 Reflector / Synthesizer 和最终引用。
- **论断一致性标记**：程序为已支持论断生成稳定 `claim_id`，再做跨论断矛盾检测；冲突不会被静默丢弃，而是以 `conflicted`、反向 claim 链接和原因进入审计与报告素材。
- **多来源交叉印证门禁**：关系模型只提出“同一事实 / 相互矛盾”候选，确定性代码校验 claim ID、置信阈值与 registrable domain；同一发布方的不同子域及 IDN / punycode 别名不重复计数，任何关联冲突优先于佐证。可按全局或单次研究开启“严格双源门禁”，仅让至少两个独立发布方支持且关系状态完整、无冲突的论断进入 Reflector / Synthesizer。
- **安全对抗评测**：离线红队集锁定注入拦截、伪引用拦截、矛盾传播、有效双源传播和同源/冲突伪双源拦截；当前固定用例均为 100%，度量的是已知攻击面和管线正确性，不宣称开放世界事实判定率。
- **流式可观测**：SSE 把每个 Agent 的动作实时推到浏览器；内置 Tracer 统计耗时 / token。
- **持久化与回放**：每次研究全过程落库（计划 / 结果 / 报告 / 事件）；提供历史列表、详情、SSE 事件回放。仓储接口双实现（内存 / async SQLAlchemy），本地 SQLite 零配置并在启动时准备 schema，生产切 PostgreSQL，Alembic 管 schema 版本。
- **多检索后端**：`DR_SEARCH_BACKENDS=tavily,brave` 并发查询多个索引并按归一化 URL 去重（剥离跟踪参数、大小写与默认端口），合并发生在来源策略门禁**之前**，独立发布方仍按 registrable domain 判定，不会凭空造出伪双源。单后端失败只记审计事件不阻断，全部失败才向上抛。目的很具体：交叉印证门禁能判定「≥2 个独立发布方」的前提是它们都被检索到了，单一索引下这类漏报无从暴露。run manifest 记录后端组合（如 `TavilySearch+BraveSearch`），因此单/双后端可做对照实验。
- **学术来源与 DOI 级出处**：`DR_SEARCH_BACKENDS=openalex,arxiv` 接入学术索引（均不需要 API Key）。它们额外带回通用网页检索拿不到的字段：DOI、作者、**作者机构**、期刊、发表年份、预印本版本、引用数、**撤稿标记**与开放全文位置。参考来源列表因此从裸 URL 升级为 `作者. 标题. 期刊, 年. <DOI>`，撤稿与预印本状态直接标在引用里而不是只进审计事件。引用文本由 `EvidenceVerifier` 在**验证时刻**渲染并随 Finding 落库（只有那一刻同时握有 Finding 与 Source），因此历史回放与 worker 跨进程执行拿到的引用完全一致；`Report.citations` 仍是纯 URL 列表，前端 [n] 跳转与快照覆盖率指标的契约不变。OpenAlex 的倒排索引摘要会被还原成连续文本，且**还原结果就是逐字证据校验匹配的那一份**——模型看到的、被哈希留证的、被校验的是同一份文本。注意 OpenAlex 有每日免费配额（约 1000 次/日、按出口 IP 计、UTC 午夜重置），耗尽时抛出带重置时间的 `OpenAlexQuotaExceeded` 而非裸 429；多后端下该失败被隔离，其余后端照常产出。详见 [docs/AI4S_HSI_PLAN.md](docs/AI4S_HSI_PLAN.md)。
- **provider 无关**：任意 OpenAI 兼容端点（OpenAI / DeepSeek / Qwen / GLM / Moonshot …）。
- **可测试**：依赖注入（LLM / 检索后端可替换为假实现），单测无需密钥与网络。
- **自动化评估**：内置 LLM-as-judge，从覆盖度/可靠性/深度/可读性四维给报告打分。
- **判定器自身可校准**：语义支持判定（`semantic_status`）由 LLM 给出，因此它本身也需要被度量。`make judge-export` 从已落库的运行里**分层**抽样（随机抽会几乎全是 supported，一致率虚高），人工标注后 `make judge-calibrate` 输出与人工的一致率、分类别 precision/recall 和 **Cohen's κ**（扣除巧合一致）。结论直接决定 judge 分数的用法：κ 高才可支撑对外相对结论，κ 低就只能看趋势——逐字引用验证是确定性的，不在此列。
- **可复现实验清单**：每次运行把工作流 hash、非敏感配置、模型/端点、检索后端与角色目录快照 hash 固化进 checkpoint；原始检索结果按 URL + 内容哈希版本化落库，可离线审计“当时模型看到了什么”。
- **确定性质量指标**：运行详情直接返回逐字验证率、语义支持率、报告准入率、引用快照覆盖率、独立发布方数、冲突/争议/拦截数及成本耗时，不依赖 judge 模型即可做版本回归。

## 架构

每次运行先经过全局意图门禁，再由默认的 planner-driven 编排器加载共享规则并执行选定流程。
下图展示 deep 公共模板的主链；quick、hsi_review 以及自定义流程复用同一门禁与规则上下文。

```
用户问题
    │
    ▼
┌─────────┐  把大问题拆成若干可独立检索的子问题
│ Planner │
└─────────┘
    │ sub_questions[]
    ▼
┌──────────────────────────────┐  每个子问题一个 Researcher，
│ Researcher × N  (并行 fan-out) │  各自检索网络 → 抽取带出处的发现
└──────────────────────────────┘
    │ findings[]
    ▼
┌────────────┐  证据是否充分？缺什么？需要补哪些新子问题？
│ Reflector  │  （不充分则回到 Researcher，最多 N 轮）
└────────────┘
    │ (sufficient)
    ▼
┌─────────────┐  综合成结构化、带 [n] 引用的研究报告
│ Synthesizer │
└─────────────┘
    │
    ▼
Markdown 报告（含参考来源）
```

## 目录结构

```
deep-research-agent/
├── deep_research/
│   ├── config.py            # 配置（环境变量 + 行为参数）
│   ├── runtime_config.py    # 前端设置中心的持久化覆盖层（runtime_config.json）
│   ├── models.py            # Pydantic 数据模型（各 Agent 的 schema）
│   ├── observability.py     # Event + Tracer（控制台订阅 / SSE 队列）
│   ├── llm.py               # LLM 封装（complete + 流式 + 结构化 parse，provider 无关）
│   ├── guardrails.py        # 来源安全策略 + 证据验证 + 论断一致性门禁 + 来源意图收紧
│   ├── citation.py          # 学术引用渲染（参考来源列表 / 表格脚注 / 导出共用一份）
│   ├── intent/              # 意图识别：标签体系 / 规则 / 本地模型 / 级联 / 多轮消解 / 槽位 / 澄清 / 路由
│   ├── dag.py               # 子问题依赖图：构建 / 环检测 / 拓扑分层
│   ├── registry.py          # Agent 角色注册表
│   ├── scheduler.py         # DAG 分层调度器
│   ├── token_budget.py      # token 预算跟踪与软限制
│   ├── tools/               # 检索后端抽象 + Tavily / Brave / OpenAlex / arXiv 实现（含 key 主备池）
│   ├── agents/              # Planner / Researcher / Reflector / Synthesizer / Critic / Coordinator / IntentRouter …
│   ├── orchestration/       # 工作流图模型：节点 / 边 / 条件解释器 / 图运行时
│   ├── workflow.py          # 工作流执行引擎（checkpoint / 重试 / fallback / 预算 / halt）
│   ├── workflows.py         # 公共工作流模板（deep / quick / hsi_review）与内部兼容编排
│   ├── catalog/             # 角色广场：角色卡 / 模型档案 / 检索 key 的仓储与运行时
│   ├── persistence/         # 仓储层：接口 + InMemory / SQL(async SQLAlchemy) 双实现
│   ├── orchestrator.py      # 编排：DAG 调度 + 反思循环 + run_stream + 落库
│   ├── cli.py               # 命令行入口
│   ├── desktop.py           # 桌面版（PyInstaller）启动入口
│   ├── execution.py         # 执行核心（与 HTTP 解耦：inline 与 worker 共用）
│   ├── worker.py            # 独立执行进程：领取循环 / 毒任务熔断 / 优雅退出
│   ├── api.py               # FastAPI + SSE（含历史 / 回放 / 恢复端点）
│   └── catalog_api.py       # 角色广场 / 模型档案 / 自定义工作流 API
├── alembic/                 # 数据库迁移（env.py + versions/）
├── alembic.ini
├── Dockerfile               # 生产镜像（启动时迁移 + uvicorn）
├── docker-compose.yml       # 一键起 PostgreSQL + API
├── frontend/                # React + TS + Vite 工程化 SPA
│   ├── src/                 # 页面 / 组件 / hooks / API client / 类型
│   ├── vite.config.ts       # dev proxy → :8000；构建产物输出 dist/
│   └── package.json
├── eval/                    # LLM-as-judge 自动化评估
└── tests/                   # pytest（fakes 注入，无需网络）
```

## 快速开始

```bash
python -m pip install --require-hashes -r requirements.lock
cp .env.example .env   # 填入 LLM_API_KEY 与 TAVILY_API_KEY

# 命令行
python -m dotenv run -- python -m deep_research.cli "2026 年主流 AI Agent 框架有哪些？各自取舍是什么？"
```

`requirements.txt` / `requirements-dev.txt` 只维护直接依赖与允许升级范围；日常安装、CI
和镜像构建使用带完整传递依赖及发行包哈希的 `requirements*.lock`。修改依赖范围后运行
`make lock`（需要 `uv`）并提交两个锁文件，避免不同时间部署得到不同依赖组合。

## Web 实时 Demo（React 前端）

前端是独立的 **React + TS + Vite** 工程（`frontend/`），与 FastAPI 后端分离开发、同源部署。

**开发模式**（前端热重载，推荐）：

```bash
# 终端 1：后端（:8000）
python -m uvicorn --env-file .env --reload deep_research.api:app

# 终端 2：前端（Vite dev server，自动 proxy /api 与 /healthz 到 :8000）
cd frontend && npm ci && npm run dev   # 打开 http://127.0.0.1:5173
```

**同源构建模式**（本地构建后由后端托管）：

```bash
cd frontend && npm ci && npm run build  # 产出 frontend/dist
python -m uvicorn --env-file .env deep_research.api:app  # 访问 http://127.0.0.1:8000
```

页面提供：新建研究（可选工作流模板、可调研究参数）、实时观看（Agent 时间线 / DAG 分层调度 / 流式报告 / **实时统计**：耗时秒级跳动、token 随阶段累加）、**报告导出**（复制 / 下载 `.md`）、历史列表与回放、**历史管理**（删除单条·批量 / 状态·关键词·标签筛选 / 打标签分类）、**工作流构建器**（自由画布拖排角色、连线加条件、存库后可直接运行）、**角色广场**（角色卡 / 模型档案 / 检索 key 在线编辑）、**全局设置**（前端改模型 / 端点 / 密钥 / 检索参数并持久化）。未认证访客会先看到欢迎页并可弹出密钥登录。后端 `GET /` 优先加载 `frontend/dist/index.html`，未构建时回退到占位页。

## Docker 一键启动（含 PostgreSQL）

```bash
cp .env.example .env
# 至少填写 POSTGRES_PASSWORD、API_KEY、CATALOG_ENCRYPTION_KEY；
# 使用内置模型/检索时再填写 LLM_API_KEY、TAVILY_API_KEY。
docker compose up --build
# 启动后访问 http://127.0.0.1:8000
# 容器内置 Postgres，API 启动时自动 alembic upgrade head 建/升级表
make down       # 停止服务，保留数据库与运行时配置
# make down-clean  # 危险：永久删除 pgdata/appdata 数据卷
```

`docker-compose.yml` 起两个服务：`db`（postgres:16-alpine，带健康检查）与 `api`（构建本仓库镜像，待数据库就绪后启动）。`DATABASE_URL` 已在 compose 中指向内部 `db` 服务，LLM / 检索密钥从根目录 `.env` 注入。数据库使用 `pgdata` 卷，前端设置中心的运行时配置使用 `appdata` 卷；常规 `make down` 与容器重建都会保留二者，只有显式执行 `make down-clean` 才会删除。

### 水平扩展：API 与执行分离

默认 `inline`：API 进程自己执行研究任务，单容器即可跑通。改成 worker 拓扑后，API 只负责入队，
执行交给独立进程，可任意扩副本：

```bash
# .env 里设 DR_EXECUTION_MODE=worker，然后启用 worker profile
DR_EXECUTION_MODE=worker docker compose --profile worker up --build --scale worker=3
```

两种拓扑共用同一套租约 fencing 与 checkpoint 续跑语义，切换只改一个环境变量，
任务本身的执行行为零变化——回滚同样只需改回 `inline`。

- **谁执行**：worker 轮询 `(status, claimable_at)` 索引挑候选，用条件租约更新做最终仲裁。
  候选选择只是优化，租约才是跨进程的唯一裁决者——与崩溃恢复完全同源。
- **取消与 SSE**：均已跨进程。取消经数据库状态生效（执行侧轮询到 `cancelling` 自行收尾）；
  SSE 在本地没有事件中心时自动降级为仓储回放，浏览器无感。
- **毒任务熔断**：同一 run 被领取超过 `DR_MAX_CLAIM_ATTEMPTS`（默认 3）次仍失败即置终态，
  原因 `poison_run` 入审计，避免必然崩溃的任务在 worker 之间无限传递。
- **优雅退出**：worker 收到 SIGTERM 后停止领取但**不打断**在跑的研究；被强杀也无妨，
  租约到期后由其他副本从 checkpoint 接管。

实测（`make chaos-demo-worker`，deep 工作流跑到第 3 层时硬杀 worker）：
**API 全程 /healthz 200，新 worker 启动后 4.7s 接管**，planner/researcher 两层断点续跑跳过，
**节省 66.7% token**（对照全量 9000，恢复后仅新增 3000）。
对照组 `make chaos-demo`（inline，杀 API 后重启）接管 2.3s、同样节省 66.7%。

两个服务均使用 Docker `local` 日志驱动，每个日志文件上限 10 MB、最多保留 5 个，避免访问日志或数据库日志无限占满宿主机磁盘；需要长期审计时应接入集中日志系统。

### 文件处理与本机命令

Planner/Agent 不能把模型生成的 shell 文本交给操作系统。需要解压、转 PDF、编译
LaTeX 等动作时，计划只声明稳定的 operation ID（以及结构化的输入/输出 artifact
路径），由镜像内可信代码注册的 `CommandRunner` 生成参数数组并执行。执行器会校验工作区
边界、拒绝 shell wrapper 与控制字符，限制超时、并发和 stdout/stderr 大小，并把退出码、
参数摘要与产物哈希写入 manifest。

基础镜像只包含 runner 的注册契约，不预装 `bsdtar`、`libreoffice` 或 `latexmk` 等重量级
工具。启用对应 operation 前应构建受控的 runner/worker 镜像（安装所需包、为
`appuser` 配置可写的 HOME/cache），然后在 `.env` 中收紧
`DR_RUNNER_ALLOWED_OPERATIONS`；未安装工具时请求会得到可审计的失败状态，不会退化为
执行任意命令。生产环境不要把 Docker socket 或宿主机根目录挂进容器，文件交接统一通过
共享的 `artifacts` volume：

```bash
# 例：只开放已经在自定义 worker 镜像中安装并验收过的转换操作
DR_RUNNER_ALLOWED_OPERATIONS=pdf.convert
DR_EXECUTION_MODE=worker
DR_ORCHESTRATION_MODE=planner-driven
docker compose --profile worker up --build --scale worker=3
```

本地开发与 Linux Docker 使用同一套 operation 注册接口；如果需要新增文件处理能力，
应在代码中新增一个固定的 `OperationDefinition` 并随镜像发布，而不是把命令字符串写进
planner prompt 或运行时环境变量。

安全默认值：容器以非 root 用户运行；`db` 不向宿主机发布端口；`api` 仅绑定 `127.0.0.1`，对外访问请经反向代理（TLS/限流）。Compose 为 API 设置 `APP_ENV=production`，启动时会强制校验 PostgreSQL、`API_KEY` 与 `CATALOG_ENCRYPTION_KEY`，缺一即失败。所有 `/api` 端点接受 `Authorization: Bearer <key>` 或 `X-API-Key` 请求头，不接受 URL 查询参数。前端 SSE 同样使用请求头，登录凭据只保存在当前标签页的 `sessionStorage`，关闭标签页即清除。

Nginx 反向代理可从 `docker/nginx.conf.example` 起步。SSE 实时进度要求关闭 `proxy_buffering`，并把读写超时提高到覆盖最长研究任务。宿主机 Nginx 经 Docker bridge 访问 API 时，在 `APP_BIND` 保持 `127.0.0.1` 的前提下同时设置 `APP_TRUST_PROXY=true` 与 `FORWARDED_ALLOW_IPS=*`，分别让应用限流和 Uvicorn 信任代理覆盖的客户端 IP/协议；直接暴露 API 时两项都必须保持收紧，尤其不要把 `FORWARDED_ALLOW_IPS` 设为 `*`。若不用反向代理、明确要直接暴露端口，可在 `.env` 设置 `APP_BIND=0.0.0.0`，但仍应在安全组中限制来源并配置 HTTPS。

### 服务器端口与配置清单

推荐生产拓扑：公网 → Nginx/Caddy（HTTPS）→ `127.0.0.1:8000` → API 容器，API 容器通过 Docker 内部网络访问 PostgreSQL。

| 端口 | 是否开放公网 | 用途 |
|------|--------------|------|
| `80/tcp` | 是 | HTTP 与 HTTPS 跳转；若只使用 443 可按实际策略关闭 |
| `443/tcp` | 是 | Web 页面、API 与 SSE 实时进度 |
| `8000/tcp` | 否 | FastAPI 宿主机回环端口，仅供反向代理访问 |
| `5432/tcp` | 否 | PostgreSQL，仅在 Compose 内部网络访问 |

服务器需要允许**出站 `443/tcp`**，用于访问 OpenAI 兼容模型端点、Tavily 检索服务和 GitHub。

部署时主要维护以下文件：

- `.env`：服务器私有配置，不提交 Git。生产必须设置 `POSTGRES_PASSWORD`、`API_KEY`、`CATALOG_ENCRYPTION_KEY`；使用内置后端时设置 `LLM_API_KEY`、`TAVILY_API_KEY`。
- `docker-compose.yml`：应用、PostgreSQL、数据卷和端口绑定。
- `docker/nginx.conf.example`：Nginx 反向代理与 SSE 长连接示例；复制到服务器 Nginx 配置目录后修改域名。
- `.env.example`：环境变量模板，不包含真实密钥。

服务器拉取与启动：

```bash
git clone https://github.com/Lxiny-zy/deep-research-agent.git
cd deep-research-agent
cp .env.example .env
# 编辑 .env，填写口令与 API Key
chmod 600 .env  # Linux 服务器：限制密钥文件只对当前用户可读写
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/readyz
```

### 备份与恢复

升级或执行 `make down-clean` 前，至少备份 PostgreSQL 和 `.env` 中的
`CATALOG_ENCRYPTION_KEY`。数据库可在服务运行时导出为自包含归档：

```bash
mkdir -p backups  # Windows PowerShell 可用：New-Item -ItemType Directory -Force backups
docker compose exec -T db sh -c 'umask 077; pg_dump -U dr -d deep_research -Fc -f /tmp/deep_research.dump'
docker compose cp db:/tmp/deep_research.dump backups/deep_research.dump
docker compose exec -T db rm -f /tmp/deep_research.dump
```

恢复前先停止 API 写入并确认目标库可以被覆盖；以下命令会清理归档中已有的数据库对象，
不要指向仍需保留的数据：

```bash
docker compose cp backups/deep_research.dump db:/tmp/deep_research.dump
docker compose exec -T --user root db chown postgres:postgres /tmp/deep_research.dump
docker compose exec -T db pg_restore --list /tmp/deep_research.dump
docker compose stop api
docker compose exec -T db pg_restore -U dr -d deep_research --clean --if-exists /tmp/deep_research.dump
docker compose exec -T db rm -f /tmp/deep_research.dump
docker compose start api
curl http://127.0.0.1:8000/readyz
```

`appdata` 仅保存 `/app/data/runtime_config.json` 的非密钥运行时覆盖，可用
`docker compose cp api:/app/data/runtime_config.json backups/runtime_config.json` 备份；恢复时先停止
`api`，再写回、校验 JSON 并恢复 UID 10001 的文件所有权后启动：

```bash
docker compose stop api
docker compose cp backups/runtime_config.json api:/app/data/runtime_config.json
docker compose run --rm --no-deps --user root --entrypoint sh api -c 'python -m json.tool /app/data/runtime_config.json >/dev/null && chown 10001:10001 /app/data/runtime_config.json && chmod 600 /app/data/runtime_config.json'
docker compose start api
```

Catalog 凭据在 PostgreSQL 中加密，恢复数据库时必须同时恢复原 `CATALOG_ENCRYPTION_KEY`，否则已保存的
模型与检索凭据无法解密。恢复后应实际读取一条历史 run 并验证 Catalog 凭据，而不只检查 `/readyz`。

## 持久化 · 历史与回放

经 API 提交的每次研究全过程会落库：计划、子问题、结果与发现、来源、报告、事件流（瞬态 token 事件不落库）。仓储抽象成 `ResearchRepository` 协议，两份实现行为对齐、可互换：

- **InMemoryRepository**：纯进程内存，离线单测零依赖，亦为 `ResearchRepository` 的参考实现。
- **SqlRepository**：async SQLAlchemy 2.0，本地 SQLite、生产 PostgreSQL 通用；API 默认使用。

API 由环境变量 `DATABASE_URL` 选择 SqlRepository 后端（缺省 `sqlite+aiosqlite:///./deep_research.db`）；CLI 直跑直出、不落库。配套 API 端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/runs` | 提交研究（支持 `Idempotency-Key`），返回 `run_id` |
| `GET`  | `/api/runs` | 历史列表（分页 `limit`/`offset`，可按 `status`/`q`/`tag` 筛选） |
| `GET`  | `/api/runs/{id}` | 单次详情（计划 + 结果 + 报告 + 标签） |
| `DELETE` | `/api/runs/{id}` | 删除单条（进行中返回 409）；级联清子表 |
| `POST` | `/api/runs/batch_delete` | 批量删除（body `{ids:[...]}`，跳过进行中） |
| `PUT`  | `/api/runs/{id}/tags` | 设置标签（替换语义，body `{tags:[...]}`） |
| `GET`  | `/api/tags` | 全部标签 + 引用计数 |
| `GET`  | `/api/runs/{id}/events` | 事件回放（支持 `after_seq` 增量） |
| `GET`  | `/api/runs/{id}/stream` | SSE：支持 `Last-Event-ID` 断点续传与跨实例增量轮询 |
| `POST` | `/api/runs/{id}/cancel` | 幂等请求取消运行，进入 `cancelling` / `cancelled` |
| `POST` | `/api/runs/{id}/resume` | 从最近 checkpoint 手动恢复中断的 run |
| `GET`  | `/api/workflows` | 可用工作流列表（内置模板 + 自定义） |
| `GET`  | `/api/roles` | 可用 Agent 角色列表 |
| `GET`  | `/api/config` | 当前全局配置（密钥脱敏） |
| `PUT`  | `/api/config` | 更新并持久化全局配置（对后续 run 生效） |
| `GET`  | `/api/research?q=` | 兼容旧 SSE 客户端：创建持久化 run 后转发事件流，响应含 `X-Run-ID` |

角色广场 / 自定义工作流另有一组 Catalog API（`catalog_api.py`）：`/api/behaviors`、`/api/models*`、`/api/agents*`、`/api/search-keys*`、`/api/workflows/custom*`，覆盖角色卡、模型档案、检索 key 池与画布工作流的增删改查。

## 全局配置 · 前端设置中心

前端「设置」页（`GET`/`PUT /api/config`）可在线修改 LLM 模型 / Base URL、当前进程使用的 API Key / Tavily Key 与研究行为默认值，并对后续创建的研究生效。

- **加载顺序**：环境变量（基础默认）→ `runtime_config.json`（前端写入的覆盖项）→ per-run `params`（本次运行覆盖）。
- **严格双源门禁**：设置页可全局开启，也可在新建研究的高级设置中按次覆盖；环境变量部署可使用 `REQUIRE_CORROBORATION=true`。默认关闭以兼容既有单来源报告，开启后关系验证失败、单一来源或争议论断均无法进入报告；若没有任何合格素材，Synthesizer 会跳过生成模型并返回确定性的无证据结果。
- **密钥安全**：`GET` 只脱敏回显（`…末四位` + 是否已设置），表单留空＝保持不变；运行时输入的密钥只驻留进程内存，不写 `runtime_config.json`，重启后回到环境变量。Catalog 中的模型/检索凭据使用 `CATALOG_ENCRYPTION_KEY` 加密落库，启动时自动迁移旧明文。
- **持久化位置**：非密钥配置默认写当前工作目录 `runtime_config.json`（已 gitignore），可经 `RUNTIME_CONFIG_PATH` 改路径。Docker Compose 已自动设置为 `/app/data/runtime_config.json` 并挂载 `appdata` 数据卷，容器重建后不会丢失。
- `database_url` 与服务端 `api_key` 不可经前端改（自举 / 鉴权安全），仍只来自环境变量。

## 数据库迁移（Alembic）

schema 版本由 Alembic 管理，`alembic/env.py` 复用 `Settings.database_url`（与应用同一配置来源）：

```bash
make migrate            # = alembic upgrade head，应用到最新版本
make revision m="说明"  # = alembic revision --autogenerate，按模型变更生成迁移
```

> API 启动时会为本地 SQLite 准备 schema：新库/已有 Alembic 库走迁移，历史 `create_all` 旧库会补齐新增验证列并 stamp 到当前 head；单测仍可直接用 `create_all` 快速建临时库。生产 PostgreSQL 统一用 `python -m deep_research.migrate`（或直接启动容器，让 entrypoint 执行）；该入口用 session-level advisory lock 串行多实例迁移。不要在多实例发布脚本里绕过它直接运行裸 `alembic upgrade head`。

## 自动化评估

```bash
python -m eval.run_eval
python -m eval.run_eval --workflows deep,quick,hsi_review --output
```

默认把每个 `workflow × case` 作为独立研究运行写入 `DATABASE_URL`，并在指定
`--output` 时同时生成 Markdown 和同名 JSON：Markdown 展示 judge 评分、确定性证据
指标、成本/耗时与每格 `run_id`；JSON 保存完整矩阵、Run Manifest、质量指标和明细
SHA-256。临时实验可用 `--no-persist-runs` 禁止落库。

每次持久化运行的 `GET /api/runs/{id}` 还会返回：

- `manifest`：可复现运行清单，不包含 API Key 等密钥；endpoint 会移除凭据与 query。
- `sources`：检索时实际交给来源安全门禁和 Researcher 的原文快照。
- `metrics`：从 findings / citations / events 确定性计算的质量、来源与成本指标。

这些字段用于比较工作流与模型版本；LLM-as-judge 负责主观质量维度，确定性指标负责证据链和工程回归，两者不互相替代。

对内置用例集逐条研究并由 LLM-as-judge 打分；每个分数都能经 `run_id` 回溯到运行
详情、检索快照和事件流，而不是只保留最终平均值。

### Benchmark 回归门禁

```bash
python -m eval.run_eval \
  --workflows deep,quick,hsi_review \
  --output eval/results/candidate.md \
  --baseline eval/baselines/main.json
```

门禁默认要求引用快照覆盖率不低于 95%、无语义支持论断率不高于 5%、冲突率不高于
10%，并限制相对基线的 judge 均分下降不超过 0.25、token 增幅不超过 25%。失败时
进程以退出码 `2` 结束，适合直接接入 CI。阈值可用 `--min-citation-coverage`、
`--max-unsupported-rate`、`--max-conflict-rate`、`--max-score-drop` 和
`--max-token-increase` 调整。

## 测试

```bash
pytest            # 使用假 LLM / 假检索，无需任何密钥或联网
verify.bat         # Windows：锁依赖、静态检查、迁移、覆盖率、前端、Wheel 与 Compose 核心门禁
```

## 意图识别

```bash
python -m scripts.train_intent_model --dry-run  # 先评测候选 L2，不写随包模型
make intent-train    # 评测达标后生成 L2（TF-IDF + 逻辑回归，纯离线、确定性）
make intent-eval     # 离线评测：准确率 / 混淆矩阵 / 拒识率 / 误伤率 / 级联分流
```

`make intent-eval` 只覆盖 L1 规则与 L2 本地模型两级（不调真实 LLM，因此可复现且零成本），
输出中显式标注这一口径。安全指标成对报告——**拒识率**（漏放攻击）与**误伤率**
（正常研究请求被误拒）同时给出，评测集里有一半是「研究提示词注入防御」这类
与攻击同词不同意图的困难负例，防止「全部拒绝」把拒识率刷到 100%。
任一指标出现问题时进程以退出码 `1` 结束，可直接接入 CI。

设计取舍与三条安全不变量见 [docs/INTENT_RECOGNITION.md](docs/INTENT_RECOGNITION.md)。

## 设计决策（面试可展开）

- **为什么意图识别做成三级级联而不是直接调大模型？** 级联是**成本阶梯**不是准确率堆叠：意图判定是每个请求的前置步骤，做成一次 LLM 调用等于给所有流量加固定开销与秒级延迟；规则与本地模型吃掉大部分常规流量，只把低置信样本让给 LLM。攻击流量在第一级就被拦下，攻击者无法靠刷请求放大 LLM 账单。
- **为什么意图判定只能收紧不能放宽？** 它读的是攻击者可控的输入。做成单向后，即便判定器被完全操控，最坏结果也只是把正常来源误判为可疑（可用性损失），而不可能让攻击性来源进入模型上下文（安全性损失）——安全属性是结构性质，不依赖组件自身正确。
- **为什么结构化输出不用 provider 私有 API？** 为了 provider 无关与可移植：靠「Schema 注入 prompt + 稳健 JSON 抽取 + 失败重试」实现，任意兼容端点都能跑。
- **为什么把 Tracer 做成事件总线？** 同一份事件流，CLI 用来打印、API 用来 SSE，前端用来实时可视化——一处产生、多处消费。
- **为什么依赖注入？** Orchestrator 接受外部传入的 LLM / 检索后端，测试时注入假实现即可全离线跑完整流程。
- **并行与限流**：`asyncio.gather` 做 fan-out，`Semaphore` 限制并发，单个 Researcher 失败被隔离，不拖垮全局。

## Roadmap

- [x] 报告流式逐字输出（token streaming）
- [x] 子问题之间的依赖编排（DAG，而非纯并行）
- [x] 研究历史持久化 + 事件回放（SQLite / PostgreSQL，Alembic 迁移，Docker 一键起）
- [x] 实时统计（耗时秒级跳动 + token 随阶段累加，不止结束时一次）
- [x] 报告导出（Markdown 下载 / 复制）
- [x] 历史管理（删除单条·批量 / 状态·关键词·标签筛选 / 打标签分类）
- [x] 前端全局设置中心（模型 / 端点 / 密钥 / 检索参数，持久化生效）
- [x] 学术检索源与 DOI 级出处（OpenAlex / arXiv，含机构与撤稿标记）
- [ ] AI4S（高光谱计算成像）：全文分节证据、数值+协议校验、同一 work 聚类判独立性、
      对照表由代码渲染、系统综述工作流（分期见 [docs/AI4S_HSI_PLAN.md](docs/AI4S_HSI_PLAN.md)）
- [ ] 检索后端增加 Bing / SerpAPI / 自建向量库
- [ ] 评估接入 LangSmith / Phoenix 做 tracing 看板
- [ ] 多用户与鉴权、报告 PDF 导出 / 分享链接
