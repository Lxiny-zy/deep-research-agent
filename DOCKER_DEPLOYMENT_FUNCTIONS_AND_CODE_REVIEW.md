# Deep Research Agent：Docker 部署版功能、实现路径与全量代码审查

> 注意：本文是部署配置与生产边界审查附录，不是 Agent 面试主文档。
>
> Agent 功能、原理、调用链和面试回答请优先阅读：`DOCKER_VERSION_AGENT_PROJECT_INTERVIEW_GUIDE.md`。
>
> 审查基线：`main` 分支当前 `HEAD`（`c31d43f`）及当前 Docker 配置。
>
> 本文只描述 `Dockerfile + docker-compose.yml + docker/entrypoint.sh` 实际构建、启动和暴露的版本。桌面打包、开发态 Vite、离线评测脚本等仅在“Docker 版不包含的能力”中说明，不混入 Docker 功能清单。

## 1. 项目定位

- 项目名称：Deep Research Agent。
- 产品形态：由 React 单页应用、FastAPI API、多 Agent 编排引擎和 PostgreSQL 组成的深度研究平台。
- 核心目标：把一个复杂问题拆为多个可检索子问题，按依赖关系并行研究，经过反思补洞后生成带真实来源引用的 Markdown 报告。
- Docker 版默认交付形态：
  - 一个 `api` 容器，同时承载 FastAPI、后台 Agent 任务和前端静态文件。
  - 一个 `db` 容器承载 PostgreSQL 16。
  - 两个 Docker named volume 分别保存业务数据库和运行时配置。
  - 浏览器通过 `http://127.0.0.1:8000` 访问；生产环境建议再接 Nginx/Caddy。

## 2. Docker 部署版本的配置选择

### 2.1 为什么选择多阶段镜像

- 第一阶段使用 `node:20-slim`：
  - 执行 `npm ci`，严格按 `frontend/package-lock.json` 安装前端依赖。
  - 执行 `npm run build`，先做 TypeScript 类型检查，再由 Vite 生成生产静态资源。
  - 产物位于构建阶段的 `/fe/dist`。
- 第二阶段使用 `python:3.11-slim`：
  - 安装 `requirements.txt` 中的运行依赖。
  - 只复制 Python 包、Alembic 迁移文件、前端构建产物和启动脚本。
  - 不把 Node.js、前端源码依赖和开发工具带入最终镜像。
- 选择收益：
  - 前端与后端只有一个最终运行镜像，部署和同源访问简单。
  - 最终镜像不需要 Node.js 运行时。
  - Docker 层按“依赖先复制、源码后复制”组织，源码变化时可复用依赖层缓存。
- 实现文件：`Dockerfile`。

### 2.2 为什么选择 Python 3.11

- `pyproject.toml` 明确要求 Python `>=3.11`。
- 项目使用 `asyncio.timeout`、现代类型标注、Pydantic v2、SQLAlchemy 2.0 异步能力。
- `asyncpg`、SQLAlchemy、OpenAI SDK 等依赖在 Python 3.11 Linux 环境有可直接安装的 wheel，运行镜像无需保留编译器。
- 实现文件：`Dockerfile`、`pyproject.toml`、`requirements.txt`。

### 2.3 为什么 Docker 版选择 PostgreSQL 16

- Compose 将 `DATABASE_URL` 固定为 `postgresql+asyncpg://...@db:5432/deep_research`。
- 后端使用 SQLAlchemy 2.0 async engine 与 `asyncpg`，避免同步数据库访问阻塞 Agent 异步任务。
- 相比本地默认 SQLite，PostgreSQL 更适合：
  - 多连接并发写入研究事件、检查点和运行状态。
  - 数据库租约协调多实例恢复。
  - 后续增加独立 Worker 或水平扩展。
- PostgreSQL 端口默认不发布到宿主机，只在 Compose 内网被 `api` 服务访问。
- 实现文件：`docker-compose.yml`、`deep_research/persistence/db.py`。

### 2.4 为什么前后端同容器、同源部署

- 前端 Vite 产物被复制到 `/app/frontend/dist`。
- FastAPI 在 `/` 返回 `index.html`，在 `/assets` 提供静态资源，并用 catch-all 路由支持 React Router 刷新。
- 浏览器 API、SSE 和静态页面同源，不需要生产 CORS 配置。
- 该选择适合单机演示、个人项目、简历项目和轻量服务。
- 当前限制：API 进程同时承担 HTTP、静态文件和长任务执行，尚未把 Agent 任务拆到独立 Worker。
- 实现文件：`Dockerfile`、`deep_research/api.py`、`frontend/vite.config.ts`。

### 2.5 容器安全与运行选择

- 最终镜像创建 UID `10001` 的 `appuser`，业务进程不以 root 运行。
- `api` 端口默认只绑定 `127.0.0.1:8000`，避免直接暴露到局域网或公网。
- PostgreSQL 端口默认不映射到宿主机。
- `POSTGRES_PASSWORD` 使用 Compose 必填插值，未设置时拒绝启动。
- API 支持可选 `API_KEY` 鉴权；生产环境应设为强随机值。
- FastAPI 中间件加入：
  - `X-Content-Type-Options: nosniff`。
  - `X-Frame-Options: DENY`。
  - `Referrer-Policy: same-origin`。
- 镜像包含 HTTP healthcheck，但当前只检查进程能否响应 `/healthz`。
- 实现文件：`Dockerfile`、`docker-compose.yml`、`deep_research/api.py`。

### 2.6 持久化卷选择

- `pgdata:/var/lib/postgresql/data`：保存 PostgreSQL 全部业务数据。
- `appdata:/app/data`：保存前端设置中心写入的 `runtime_config.json`。
- 容器重建后两类数据仍保留。
- `docker compose down -v` 会同时删除两类卷，属于清库操作，不应作为普通停止命令使用。
- 实现文件：`docker-compose.yml`、`deep_research/runtime_config.py`。

### 2.7 启动顺序与迁移选择

- `db` 先启动并执行 `pg_isready`。
- `api.depends_on.condition=service_healthy`，数据库健康后才启动 API。
- `docker/entrypoint.sh` 使用 `set -e`：
  1. 执行 `python -m alembic upgrade head`。
  2. 迁移失败立即退出，不在错误 schema 上提供服务。
  3. 迁移成功后用 `exec` 启动 Uvicorn，使容器信号直接传给 Python 进程。
- 实现文件：`docker-compose.yml`、`docker/entrypoint.sh`、`alembic/versions/*`。

## 3. Docker 版启动后的完整调用链

1. 用户执行 `docker compose up --build`。
2. Docker 构建 React 生产资源和 Python 运行镜像。
3. PostgreSQL 初始化 `deep_research` 数据库并通过健康检查。
4. API 容器执行全部 Alembic 迁移，建立研究、事件、编排、角色和配置相关表。
5. Uvicorn 加载 `deep_research.api:app`。
6. FastAPI lifespan：
   - 从环境变量创建基础 `Settings`。
   - 从 `/app/data/runtime_config.json` 读取前端持久化覆盖配置。
   - 创建 SQLAlchemy async engine、研究仓储和 Catalog 仓储。
   - 扫描上次异常中断的 `pending/running` 任务。
   - 对有 checkpoint 的任务尝试获取数据库租约并恢复执行。
7. 浏览器请求 `/`，FastAPI 返回镜像内的 React SPA。
8. 用户提交研究任务后，API 创建数据库记录并在当前 FastAPI 进程中创建后台 `asyncio.Task`。
9. Agent 运行事件同步进入 EventHub，前端通过 SSE 实时展示。
10. 计划、结构化结果、报告、事件、WorkflowRun 和 StepRun 持久化到 PostgreSQL；来源 URL 保存在 Finding 和 Report citations 中。虽然 schema 中存在独立 `source` 表，但当前主运行路径没有调用 `save_sources` 写入该表。

## 4. Docker 版具备的产品功能

### 4.1 访客欢迎页与管理员入口

- 未进入管理状态时展示项目欢迎页、能力说明和实时过程演示。
- API 鉴权开启后，管理员输入 `API_KEY`，前端调用 `/api/config` 验证。
- 验证通过后将 key 保存到浏览器 `localStorage`，普通请求放入 `X-API-Key` 请求头。
- 收到 401 时清除旧 key，并重新弹出登录框。
- 实现路径：
  - 页面状态：`frontend/src/App.tsx`。
  - 登录弹窗：`frontend/src/components/LoginGate.tsx`。
  - 欢迎页：`frontend/src/components/WelcomePage.tsx`。
  - 前端密钥管理：`frontend/src/api/client.ts`。
  - 后端校验：`deep_research/api.py::require_api_key`。

### 4.2 新建深度研究任务

- 用户可输入最长 2000 字的研究问题。
- 可选择内置或自定义工作流。
- 可按单次任务覆盖：
  - 最大子问题数。
  - 最大反思轮数。
  - 最大并发数。
  - 单次搜索结果数。
  - Token 预算。
- API 返回 `run_id` 后，浏览器跳转运行详情页。
- 后端先创建 `pending` 研究记录，再用后台任务异步执行，不阻塞 HTTP 请求。
- 实现路径：
  - 页面：`frontend/src/pages/NewResearchPage.tsx`。
  - 高级参数：`frontend/src/components/SettingsPanel.tsx`。
  - API 客户端：`frontend/src/api/client.ts::createRun`。
  - 请求模型与端点：`deep_research/api.py::CreateRunRequest`、`create_run`。

### 4.3 五种内置研究工作流

- `deep`：Planner → Researcher → Reflector/Researcher 补洞循环 → Synthesizer。
- `quick`：Planner → Researcher → Synthesizer，省略反思以降低延迟和 Token。
- `reviewed`：在 `deep` 后追加 Critic，对报告做问题和建议复核。
- `auto`：Coordinator 根据问题实时生成步骤列表，校验后递归执行；非法时回退 `deep`。
- `teams`：Planner 拆分主题，多个隔离子团队并行研究，最后由 Aggregator 归并。
- 实现路径：`deep_research/workflows.py`、`deep_research/workflow.py`。

### 4.4 Planner：问题拆解与依赖规划

- 将原始问题转换为结构化 `ResearchPlan`。
- 每个 `SubQuestion` 包含问题、拆分理由和 `depends_on` 前驱索引。
- 控制子问题最大数量。
- 规划结果进入 Blackboard，并在持久化运行中保存。
- 实现路径：`deep_research/agents/planner.py`、`deep_research/models.py`。

### 4.5 Researcher：并发网络检索与证据抽取

- 根据子问题调用 Tavily 检索。
- 对无依赖子问题并发执行；有依赖时按拓扑层执行。
- 后层子问题可读取前驱发现作为上下文。
- 使用 Semaphore 控制实际并发度。
- 将搜索结果包装为 `Source`，再让 LLM 抽取结构化 `Finding`。
- 对单个子问题失败进行隔离，避免一处检索失败终止整次研究。
- 实现路径：
  - Agent：`deep_research/agents/researcher.py`。
  - DAG 调度：`deep_research/scheduler.py`、`deep_research/dag.py`。
  - 搜索抽象：`deep_research/tools/base.py`。
  - Tavily 实现：`deep_research/tools/tavily_search.py`。

### 4.6 搜索 Key 池与主备故障转移

- 管理员可在角色广场维护多个 Tavily key。
- key 按优先级排序。
- 当前 key 遇到 401、403、429、quota、credit 等配额或鉴权错误时切换下一个 key。
- 切换后保持粘滞，避免后续请求继续撞已耗尽 key。
- 只有数据库中不存在启用的 key 时，才使用环境变量中的单个 `TAVILY_API_KEY`；只要已启用数据库 key 池，池内 key 全部失败后会直接向上抛错，不会再回退环境 key。
- 实现路径：
  - Key 池：`deep_research/tools/tavily_pool.py`。
  - 运行时选择：`deep_research/api.py::_build_search_tool`。
  - 管理 API：`deep_research/catalog_api.py`。
  - 数据表：`deep_research/persistence/orm.py::SearchKeyRow`。

### 4.7 Reflector：证据充分性检查与增量补洞

- 分析当前研究结果是否足以回答原问题。
- 输出 `is_sufficient`、理由和新的补洞问题。
- 最多执行配置的反思轮数。
- 补洞问题只做增量检索，不重复整个研究流程。
- 反思轮次和新增问题被记录到 Blackboard scratch，后续落库。
- 实现路径：`deep_research/agents/reflector.py`、`deep_research/workflow.py::_reflect_loop`。

### 4.8 Synthesizer：带真实来源的流式报告生成

- 汇总所有研究发现并给来源编号。
- 提示模型只使用已有材料，不自行编造来源列表。
- 报告正文通过 LLM 流式输出，前端实时显示 token delta。
- 系统最终自动追加真实来源列表。
- 报告保存为 Markdown，支持浏览器查看与下载。
- 实现路径：
  - Agent：`deep_research/agents/synthesizer.py`。
  - LLM 流：`deep_research/llm.py::stream`。
  - 前端报告：`frontend/src/components/ReportView.tsx`、`ReportActions.tsx`。

### 4.9 Critic：报告复核

- 对已生成报告输出总体评价、具体问题和改进建议。
- 复核结果写入 Blackboard scratch，并通过事件展示。
- `reviewed` 工作流默认启用该步骤。
- 当前 Critic 不自动改写报告，只提供结构化审查结果。
- 实现路径：`deep_research/agents/critic.py`、`deep_research/workflows.py::REVIEWED`。

### 4.10 Coordinator：运行时自主编排

- 根据研究问题从内置角色白名单生成工作流步骤。
- 支持普通 Agent 步骤和反思循环步骤。
- 对生成结果执行：
  - 角色白名单校验。
  - 步骤数限制。
  - 反思轮数限制。
  - 终端 Synthesizer 校验。
  - 禁止嵌套 compose/team-fanout 等规则。
- 首次非法时把错误反馈给 LLM 自修复一次。
- 仍非法或生成失败时回退 `deep` 工作流。
- 零研究产出时可在受限次数内重规划。
- 实现路径：`deep_research/agents/coordinator.py`、`deep_research/workflow.py::_compose`、`validate_workflow`。

### 4.11 多团队 Map-Reduce

- Planner 先产生子主题。
- 每个子团队获得独立 Blackboard，避免并发写共享状态。
- 子团队可通过 `SubTask.steps` 执行自定义内部步骤；未提供时默认只执行一次 Researcher。Planner 只在父流程执行一次，最终报告也只由父流程 Aggregator 统一生成一次。
- 多团队受全局 Semaphore 限制并发。
- 单团队失败被隔离，不拖垮其他团队。
- 父流程串行合并结果，再由 Aggregator 生成统一报告。
- 实现路径：`deep_research/workflow.py::_team_fanout`、`deep_research/agents/aggregator.py`。

### 4.12 Workflow-as-Data 编排内核

- 所有 Agent 遵循统一 `async step(bb, ctx) -> Blackboard` 协议。
- `Blackboard` 显式保存 query、plan、results、reflections、report 和扩展 scratch。
- `RunContext` 注入 LLM、搜索工具、Tracer、Settings 和按角色解析模型的方法。
- `Workflow`、`Step`、`WorkflowNode`、`WorkflowEdge` 将流程控制数据化。
- 引擎支持：
  - 线性步骤。
  - DAG 图。
  - Agent 节点。
  - 反思循环。
  - 自组合。
  - 多团队 fan-out。
  - 条件边。
  - 节点超时、重试、退避、fallback 和 fail-fast。
- 实现路径：`deep_research/agents/base.py`、`deep_research/workflow.py`、`deep_research/orchestration/*`。

### 4.13 图工作流、条件路由与 Join

- 图结构保存节点、边和画布 viewport。
- 使用 Kahn 算法生成拓扑层，同层节点通过 `asyncio.gather` 并行。
- 每个并行节点使用 Blackboard 深拷贝，层结束后按声明顺序确定性合并。
- 条件表达式只允许状态路径、JSON 字面量和有限比较操作，不使用 Python `eval`。
- Join 模式：
  - `any`：任一有效上游激活即可。
  - `all`：全部上游路径被激活即可，不要求全部成功。
  - `success_all`：全部上游激活且成功。
- 图保存前会校验环、无效引用、终端角色和 fallback 角色。
- 实现路径：`deep_research/orchestration/graph.py`、`conditions.py`、`deep_research/workflow.py::_run_graph`。

### 4.14 可视化工作流构建器

- 用户可以创建、编辑和删除自定义工作流。
- 支持自由拖放节点、输入/输出端口连接、分支、汇聚、条件边、缩放、平移和 MiniMap。
- 保存节点坐标、边、viewport、版本号和启用状态。
- 可从构建器直接跳到新建研究页并选中该工作流。
- 前后端均进行工作流合法性校验。
- 实现路径：
  - 页面：`frontend/src/pages/WorkflowBuilderPage.tsx`。
  - 编辑器：`frontend/src/components/WorkflowEditor.tsx`。
  - 画布：`frontend/src/components/WorkflowFlowCanvas.tsx`。
  - API：`deep_research/catalog_api.py`。
  - 图模型：`deep_research/orchestration/graph.py`。
  - 数据表：`WorkflowDefRow`。

### 4.15 角色广场与数据驱动 Agent

- 管理模型档案、Agent 角色卡片和搜索 key。
- Agent 卡片包含：
  - 唯一角色名。
  - 展示名、说明、图标。
  - behavior 类型。
  - 自定义 system prompt。
  - 绑定模型档案。
  - enabled 状态。
- 自定义角色无需新增 Python 类，而是复用内置 behavior：plan、research、reflect、synthesize、critique。
- 运行时优先解析数据库角色卡片，再回退代码注册表。
- 实现路径：`frontend/src/pages/AgentSquarePage.tsx`、`deep_research/agents/card_agent.py`、`deep_research/catalog/runtime.py`。

### 4.16 多模型档案与 OpenAI-compatible 接入

- 每个模型档案可配置独立 base URL、API key、model 和采样参数。
- 支持 OpenAI、DeepSeek、Qwen、GLM、Moonshot 等兼容 Chat Completions 的端点。
- 支持后端临时测试尚未保存的配置。
- 支持调用兼容端点的模型列表接口进行远程模型发现。
- 模型参数支持互斥模式：
  - temperature 模式发送 temperature。
  - reasoning 模式不发送 temperature，改发 reasoning_effort。
- 未绑定档案的 Agent 使用全局默认设置。
- 实现路径：`deep_research/catalog_api.py`、`deep_research/catalog/runtime.py`、`deep_research/llm.py`。

### 4.17 全局运行设置中心

- 可查看脱敏后的全局 LLM/Tavily 配置。
- 可更新模型、base URL、密钥、研究参数和请求超时。
- 密钥留空表示保留原值，API 响应只返回是否设置和尾部提示。
- 配置保存到 `/app/data/runtime_config.json`，由 `appdata` 卷持久化。
- 更新只影响之后创建的运行，已启动任务继续使用自己的 Settings 快照。
- 实现路径：`frontend/src/pages/SettingsPage.tsx`、`deep_research/api.py::update_config`、`deep_research/runtime_config.py`。

### 4.18 节点级可靠性策略

- 每个 Step 支持：
  - `timeout_seconds` 节点超时。
  - `max_attempts` 最大尝试次数。
  - `retry_backoff` 指数退避基数。
  - `fallback_agent` 主 Agent 失败后的替代角色。
  - `failure_policy=continue/fail_fast`。
- 所有状态进入统一 StepRun 状态机：pending、ready、running、retrying、succeeded、failed、skipped、cancelled。
- 单步隔离失败发角色级 error 事件，不错误地终止整个 SSE 流。
- 实现路径：`deep_research/workflow.py::_execute_with_policy`、`deep_research/orchestration/runtime.py`、`types.py`。

### 4.19 Token 统计与预算保护

- 普通补全优先读取 provider usage，缺失时进行保守估算。
- 流式补全先按字符估算，结束时若 provider 返回精确 usage，则替换估算值。
- 前端明确标识当前 Token 是否仍包含估算。
- `TokenBudget` 以 Tracer 累计值作为唯一真相源。
- 预算耗尽后跳过非终端研究步骤，但保留 Synthesizer/Aggregator 等终端步骤，尽量输出部分报告。
- 因此当前是“软预算”，不是严格账单硬上限。
- 实现路径：`deep_research/llm.py`、`deep_research/token_budget.py`、`deep_research/workflow.py`。

### 4.20 Checkpoint、手动恢复与启动自动恢复

- 每层/每步后保存：
  - Workflow definition 快照。
  - Blackboard checkpoint。
  - WorkflowRun 状态。
  - StepRun 列表。
- 服务启动时扫描 `pending/running` 任务。
- 有有效 checkpoint 的任务获取租约后恢复；无 checkpoint 的孤儿任务转为 error。
- 提供 `/api/runs/{id}/resume` 手动恢复端点。
- 已成功步骤不重复执行，后续节点继续运行。
- 多实例使用 PostgreSQL 条件更新获取恢复租约，并定时续租。
- 当前语义仍是 at-least-once，不是 exactly-once。
- 实现路径：`deep_research/api.py::lifespan/resume_run/_execute`、`deep_research/workflow.py::_checkpoint`、`SqlRepository.acquire_lease`。

### 4.21 SSE 实时观测与断线兜底

- Tracer 将 Agent 内部过程统一建模为 Event。
- EventHub 为每个在线浏览器建立独立有界队列，支持多端同时观看同一运行。
- 非 Token 事件保存在内存 buffer，迟到订阅者先回放再接实时流。
- Token 事件只实时发送，不落库，避免事件表和内存无限膨胀。
- 运行结束后发送哨兵关闭所有 SSE 订阅。
- 前端断线后不盲目自动重连，改为每 4 秒轮询运行详情，最终从数据库取得报告和状态。
- Nginx 示例关闭代理缓冲并把超时提高到 3600 秒。
- 实现路径：`deep_research/observability.py`、`deep_research/api.py::stream_run`、`frontend/src/hooks/useResearchStream.ts`、`docker/nginx.conf.example`。

### 4.22 研究历史、搜索、标签与删除

- 历史列表支持分页、状态筛选、关键词搜索和标签筛选。
- 研究详情包含问题、计划、结果、报告、Token、耗时、标签和编排执行轨迹。
- 支持单条删除和批量删除。
- 运行中的任务禁止删除；批量删除会跳过运行中任务。
- 标签支持去空白、去重、长度和数量限制。
- 实现路径：`frontend/src/pages/HistoryPage.tsx`、`deep_research/api.py`、`deep_research/persistence/sql_repository.py`。

### 4.23 报告展示和导出

- React Markdown + GFM 渲染报告。
- 展示流式正文、最终报告和引用。
- 支持复制或下载 Markdown 报告。
- 实现路径：`frontend/src/components/ReportView.tsx`、`ReportActions.tsx`、`frontend/src/lib/download.ts`。

## 5. Docker 版数据库模型

- `research_run`：研究主记录、状态、耗时、Token、创建/完成时间。
- `sub_question`：计划和反思产生的子问题、依赖、轮次和来源类型。
- `research_result`：每个子问题的研究结果。
- `finding`：带 source URL 和置信度的事实发现。
- `source`：为搜索来源标题、URL 和内容片段预留的表；当前主研究持久化路径未调用 `save_sources`，实际来源追踪主要依靠 `finding.source_url` 和 `report.citations`。
- `report`：Markdown 报告和引用 URL。
- `event`：可回放的非 Token 运行事件，按 `(run_id, seq)` 唯一。
- `run_tag`：研究标签。
- `workflow_run`：工作流输入、输出、定义快照、checkpoint 和恢复租约。
- `step_run`：节点级状态、尝试次数、错误和时间。
- `model_profile`：模型连接与参数档案。
- `agent_card`：数据驱动 Agent 角色卡片。
- `search_key`：Tavily key 池。
- `workflow_def`：自定义图工作流、版本与画布状态。
- 实现路径：`deep_research/persistence/orm.py`、`alembic/versions/0001-0010`。

## 6. Docker 环境变量：实际生效情况

### 6.1 Compose 当前显式传入的变量

- `DATABASE_URL`：固定指向 Compose 内的 PostgreSQL。
- `LLM_API_KEY`：全局 LLM 密钥，可空但真实研究会失败。
- `LLM_BASE_URL`：默认 `https://api.openai.com/v1`。
- `LLM_MODEL`：默认 `gpt-4o-mini`。
- `TAVILY_API_KEY`：全局 Tavily key，可由数据库 key 池替代。
- `API_KEY`：API 管理员凭证；当前允许为空。
- `RUNTIME_CONFIG_PATH`：固定 `/app/data/runtime_config.json`。
- `POSTGRES_PASSWORD`：数据库密码，Compose 启动必填。
- `APP_BIND`：只参与端口绑定插值，不进入容器；默认 `127.0.0.1`。

### 6.2 Settings 支持但 Compose 当前未透传的变量

- `LLM_USER_AGENT`。
- `MAX_SUB_QUESTIONS`。
- `MAX_ROUNDS`。
- `MAX_CONCURRENCY`。
- `RESULTS_PER_SEARCH`。
- `MAX_TOKENS`。
- `MAX_REPLANS`。
- `REQUEST_TIMEOUT`。
- 影响：把这些值写进根目录 `.env` 并不会自动进入 `api` 容器，除非在 `docker-compose.yml` 中显式补充或使用 `env_file`。
- 其中部分研究参数和 timeout 可以进入系统后通过设置中心持久化；`LLM_USER_AGENT`、`MAX_TOKENS`、`MAX_REPLANS` 当前不能通过设置中心修改。

### 6.3 推荐的生产 Docker 配置原则

- 必须设置强随机 `POSTGRES_PASSWORD` 和 `API_KEY`。
- 密码若包含 `@`、`:`、`/`、`#`、`%` 等 URL 特殊字符，需要 URL 编码或改用拆分式数据库配置，避免拼接后的 `DATABASE_URL` 失效。
- 明确补充所有需要的 `MAX_*`、`REQUEST_TIMEOUT` 和 `LLM_USER_AGENT` 环境透传。
- 公网只暴露反向代理的 443，不直接暴露 Uvicorn 8000 和 PostgreSQL 5432。
- Nginx/Caddy 配置 TLS、SSE 禁用缓冲、请求限速、连接数限制和访问日志脱敏。
- 生产密钥优先使用 Docker secrets/外部 secret manager，而不是长期明文保存在 `.env`、数据库或 JSON 卷中。
- 对数据库和 `appdata` 做定期备份；普通停机使用 `docker compose down`，不要带 `-v`。

## 7. Docker 镜像中不包含或不直接提供的能力

- `eval/` 被 `.dockerignore` 排除：Docker 镜像没有 LLM-as-judge 离线评测脚本。
- `tests/` 和开发依赖被排除：运行镜像不能直接执行完整 pytest/ruff/mypy 流程。
- 桌面打包入口虽位于 Python 包中，但 Docker 启动入口固定为 FastAPI，不提供 PyInstaller 桌面应用。
- Vite 开发服务器不在运行镜像中；Docker 只提供构建后的静态 SPA。
- 没有独立任务队列或 Worker 容器；研究任务运行在 API 进程内。
- 没有 Redis、Celery、Temporal、Kafka 等分布式调度基础设施。
- 没有内置 TLS 终止；`docker/nginx.conf.example` 只是反向代理示例。
- 没有 Prometheus 指标端点、OpenTelemetry exporter 或集中日志服务。

## 8. 全方位代码审查结论

### 8.1 总体评价

- 架构优点：
  - Agent、状态、流程和基础设施依赖分层清晰。
  - 工作流数据化程度高，线性、DAG、自组合和多团队共用同一执行内核。
  - 可靠性不只停留在异常捕获，已经包含状态机、重试、fallback、checkpoint、租约和 SSE 断线兜底。
  - 测试覆盖后端核心模块较全面。
  - Docker 镜像采用多阶段、非 root、数据库内网、named volume 和迁移先行等合理选择。
- 当前成熟度：适合单机 Docker 演示、个人部署和 Agent 工程能力展示；尚不应描述为完整的多租户、强一致、可水平扩展生产平台。

### 8.2 P1：Docker 默认 API_KEY 为空时，前端无法进入系统

- 证据：
  - `docker-compose.yml` 将 `API_KEY` 默认设为空。
  - `deep_research/api.py::require_api_key` 在 key 为空时允许所有 API 请求。
  - `frontend/src/App.tsx` 在 localStorage 没有 key 时直接进入 `guest`，不会探测后端是否开放。
  - `LoginGate` 禁止提交空 key。
- 结果：按注释直接执行 `docker compose up --build`，即使后端 API 已开放，浏览器仍停留欢迎页，无法进入新建研究、历史、工作流和设置页面。
- 建议：
  - 增加公开的 `/api/auth/status` 或在启动时无凭证请求 `/api/config`。
  - 200 表示无需登录并进入系统，401 才显示密钥登录。
  - 同时补充“API_KEY 为空”的前端集成测试。

### 8.3 P1：Compose 未透传多项文档声明支持的环境变量

- 证据：`Settings` 支持 `LLM_USER_AGENT`、`MAX_*`、`REQUEST_TIMEOUT`，`.env.example` 也给出说明，但 Compose 的 `api.environment` 只传入少数字段。
- 结果：用户在 `.env` 中配置这些参数后，Docker 容器仍使用代码默认值，形成“看似配置成功、实际未生效”的静默错误。
- 建议：
  - 在 Compose 中逐项显式透传。
  - 或使用受控 `env_file: .env`，并继续由 `environment` 覆盖 `DATABASE_URL` 等必须固定的字段。
  - 增加 Compose 配置快照测试，断言关键变量存在。

### 8.4 P1：租约续期失败不会停止旧执行，缺少 fencing token

- 证据：`_execute` 的 heartbeat 每 60 秒调用 `acquire_lease`，但忽略返回值；Repository 只保存 owner 和过期时间，没有单调 fencing token。
- 风险：数据库抖动、长时间事件循环阻塞或实例暂停可能导致租约过期并被另一实例接管；旧实例恢复后仍可能继续调用外部 LLM、写 checkpoint 和最终结果，产生重复执行或旧写覆盖新写。
- 建议：
  - 续租返回 false 或抛错时取消当前运行。
  - 租约增加单调递增 fencing token。
  - 所有 checkpoint/finalize 更新带 owner + token 条件。
  - 对外部工具调用增加幂等键或调用日志。

### 8.5 P1：SSE 认证把长期 API_KEY 放在 URL 查询参数

- 证据：原生 EventSource 不能自定义 Header，前端 `streamUrl` 构造 `?api_key=...`。
- 风险：密钥可能进入浏览器历史、反向代理 access log、APM、异常日志和运维截图。
- 建议：
  - 登录后使用 Secure、HttpOnly、SameSite Cookie。
  - 或用 Header 鉴权换取短时、单 run、一次性 SSE ticket。
  - 反向代理日志必须过滤 query string。

### 8.6 P2：运行时与 Catalog 密钥以明文持久化

- 全局 LLM/Tavily key 写入 `/app/data/runtime_config.json`。
- 模型档案和搜索 key 直接写入 PostgreSQL Text 字段。
- API 回显虽然脱敏，但静态卷、数据库备份和主机管理员仍可读取明文。
- 建议：使用外部 Secret Manager、信封加密或至少由环境注入的主密钥做应用层加密，并建立轮换和审计机制。

### 8.7 P2：健康检查只验证 HTTP 进程，不验证数据库和迁移可用性

- `/healthz` 无条件返回 `{"status":"ok"}`。
- 数据库连接池失效、权限错误或迁移未完成时，容器仍可能显示 healthy。
- 建议拆分：
  - liveness：只检查事件循环和 HTTP。
  - readiness：执行轻量 `SELECT 1` 并验证关键 schema revision。

### 8.8 P2：进程内限流在反向代理和多实例场景不可靠

- 限流 key 使用 `request.client.host`。
- Nginx 反代后该值通常是代理地址，所有用户可能共享一个额度。
- `_hits` 只存在单进程内，多实例额度不一致；key 数量也没有全局清理机制。
- 建议：可信代理配置后解析真实客户端地址，并将生产限流放到 Nginx/API gateway/Redis。

### 8.9 P2：失败或跳过的恢复步骤被当作已处理，不会重新执行

- 线性恢复跳过 `SUCCEEDED/FAILED/SKIPPED`。
- 图恢复把 FAILED 节点视为 active，只在 `success_all` 中区分成功。
- 对“进程中断后继续”有利，但手动恢复 fail-fast 失败任务时，失败节点不会重新尝试，可能直接执行后续步骤或把不完整流程标为成功。
- 建议：明确区分 crash recovery 与 operator retry；手动重试允许从失败节点、指定节点或新 run 分叉。

### 8.10 P2：后台任务与 API 进程耦合，扩展和发布风险较高

- `asyncio.create_task` 在 Uvicorn 进程内执行长研究。
- 容器滚动发布、进程 OOM 或 API 高负载会直接影响研究任务。
- 虽有 checkpoint 恢复，但外部 LLM/搜索调用仍可能重复。
- 建议：中长期拆为 API + Worker，使用可靠队列或 Temporal 等工作流平台。

### 8.11 P2：数据库密码直接拼入 DATABASE_URL

- `POSTGRES_PASSWORD` 直接插入 URI userinfo。
- 密码含特殊字符时会改变 URL 语义并导致连接失败。
- 建议要求 URL-safe 密码、文档明确编码，或使用单独变量在应用内通过 URL builder 组装。

### 8.12 P2：多副本同时自动执行 Alembic 迁移存在竞争风险

- 每个 API 容器 entrypoint 都执行 `alembic upgrade head`。
- 单副本 Compose 没有问题；未来扩容时多个副本可能同时迁移。
- 建议生产发布把迁移做成独立 one-shot job/init step，成功后再滚动启动 API/Worker。

### 8.13 P2：`/api/research` 快路径忽略应用运行时配置

- 端点内部新建 `DeepResearchAgent(Settings())`，没有使用 `request.app.state.settings`。
- 它也不使用数据库模型档案、Agent 卡片和搜索 key 池。
- 结果：设置中心修改后，持久化任务与旧快路径的行为可能不一致。
- 建议删除旧路径、明确标记 legacy，或统一复用应用级 Settings/Catalog/SearchTool 构建逻辑。

### 8.14 P2：独立 source 表存在，但主运行路径没有写入

- `SqlRepository.save_sources` 和 `source` 表已经实现，但 `DeepResearchAgent` 完成持久化时只调用 `save_plan`、`save_result`、`save_report`、`save_events` 等方法。
- 当前引用仍可通过 `finding.source_url` 和 `report.citations` 追踪，但搜索标题、内容片段等 source 表字段不会形成完整历史记录。
- 建议在 Researcher/Blackboard 中保留去重后的 Source，并在同一运行持久化流程中调用 `save_sources`；同时处理 `(run_id, url)` 唯一约束和恢复时的幂等写入。

### 8.15 P3：React Hook 存在依赖数组警告

- `WorkflowFlowCanvas.tsx` 的 `useCallback` 被 ESLint 报告缺失 `props` 依赖。
- 当前引用了细分 props，实际风险较低，但警告会降低 CI 信噪比，也可能在后续重构时产生旧闭包。
- 建议在组件顶部解构所需 props，并让 callback 依赖明确字段。

### 8.16 P3：运行事件的强一致与补拉协议仍不完整

- 慢 SSE 消费者队列满时可能丢非 Token 事件。
- 数据库事件在运行结束时覆盖式保存，运行中并非持续 durable append。
- 前端断线后主要靠详情轮询，不能按 seq 精确恢复中间时间线。
- 建议事件携带全局 seq，关键事件增量落库，客户端使用 `Last-Event-ID` 或 `after_seq` 补拉。

### 8.17 P3：并行 Blackboard 合并只有确定性，没有冲突检测

- 列表字段按相对增量追加，plan/report 和 scratch 按声明顺序覆盖。
- 两个并行节点写同一 scratch key 时后者静默覆盖前者。
- 建议为字段声明 reducer，或使用 `scratch[node_id]` 命名空间并在冲突时告警。

### 8.18 P3：容器进一步加固空间

- 当前已做到非 root，但尚未设置 `read_only`、`tmpfs`、`cap_drop`、`no-new-privileges`、CPU/内存/PID 限制。
- 基础镜像使用浮动 tag，没有 digest pinning，也没有镜像漏洞扫描/SBOM 配置。
- 建议按部署平台补齐；注意 Alembic、Python 临时文件和 `/app/data` 的写权限需求。

## 9. 自动化验证结果

- Python Ruff lint：通过。
- Python Ruff format check：101 个文件通过。
- Mypy：47 个源码文件通过。
- Pytest：148 passed，1 个 PostgreSQL 标记测试未执行。
- 前端 ESLint：0 error，1 warning。
- 前端 Vitest：2 个测试文件、12 个测试通过。
- 前端 TypeScript + Vite production build：通过。
- `docker compose config`：通过，并确认最终服务、网络、卷和环境变量展开结果。
- `docker compose build`：未完成，原因是本机 Docker Desktop Linux engine 未运行，命名管道不存在；该失败不是 Dockerfile 编译错误证据。
- 尚未验证：
  - PostgreSQL 标记集成测试。
  - 实际镜像构建。
  - 容器启动后的迁移、healthcheck 和浏览器 E2E。
  - 真实 LLM/Tavily 联调。
  - 并发、长 SSE、恢复租约和故障注入压测。

## 10. 建议的修复优先级

### 第一优先级：保证 Docker 默认可用和配置可信

1. 修复 `API_KEY` 为空时的前端登录死锁。
2. 补齐 Compose 环境变量透传。
3. 文档明确 `docker compose down -v` 会清库。
4. 增加 Docker 配置和无鉴权模式的自动化测试。

### 第二优先级：保证恢复和密钥安全

1. 租约续期失败立即中止旧 worker。
2. 增加 fencing token 和条件写。
3. SSE 改短期 ticket 或安全 Cookie。
4. 密钥改为 Secret Manager/加密存储。

### 第三优先级：生产化拆分

1. API 与 Worker 解耦。
2. 独立迁移 job。
3. readiness、指标、集中日志和 tracing。
4. 分布式限流、任务幂等和事件补拉。
5. 资源限制、只读文件系统和供应链安全。

## 11. 面试简历提炼参考

### 11.1 一句话项目介绍

- 基于 FastAPI、React、PostgreSQL 和 OpenAI-compatible API 构建可 Docker 化部署的多 Agent 深度研究平台，通过声明式工作流、DAG 并发检索、证据反思补洞、流式报告、节点级可靠性和 checkpoint 恢复，实现从复杂问题到可追溯研究报告的完整闭环。

### 11.2 三条可直接改写为简历职责

- 设计 Workflow-as-Data 多 Agent 编排内核，以统一 Agent/Blackboard/RunContext 协议支持线性流程、DAG 分支汇聚、条件路由、自主编排和多团队 Map-Reduce，并通过隔离子状态与确定性合并控制并发竞态。
- 构建证据约束的深度研究链路，实现子问题依赖建模、拓扑分层并发 Tavily 检索、多 key 故障转移、结构化事实抽取、反思增量补洞和真实 URL 引用报告生成。
- 完成长任务可靠性与可观测体系，包括节点超时/重试/指数退避/fallback/fail-fast、软 Token 预算、Workflow/Step 状态机、Blackboard checkpoint、数据库恢复租约、SSE 多订阅实时事件和历史回放。

### 11.3 Docker/后端方向可强调的工程点

- Node 20 + Python 3.11 多阶段构建，前端静态资源由 FastAPI 同源托管。
- PostgreSQL 16 + SQLAlchemy async + Alembic 自动迁移。
- 非 root 容器、数据库内网、回环端口绑定、healthcheck、named volume 持久化。
- 运行时配置和模型/Agent/Search Key Catalog 数据化管理。
- 能主动说明当前边界：进程内任务、at-least-once 恢复、软预算、明文 secret 和缺少 fencing token，而不是把项目包装成不存在缺陷的“完整分布式平台”。

## 12. 关键文件索引

- Docker 构建：`Dockerfile`。
- Compose 拓扑：`docker-compose.yml`。
- 容器入口：`docker/entrypoint.sh`。
- Nginx SSE 示例：`docker/nginx.conf.example`。
- FastAPI/API/SSE/恢复：`deep_research/api.py`。
- 全局配置：`deep_research/config.py`、`runtime_config.py`。
- 编排引擎：`deep_research/workflow.py`、`workflows.py`。
- Agent 协议：`deep_research/agents/base.py`。
- Agent 实现：`deep_research/agents/*`。
- DAG 与图执行：`deep_research/dag.py`、`scheduler.py`、`orchestration/*`。
- LLM 封装：`deep_research/llm.py`。
- 搜索工具：`deep_research/tools/*`。
- 可观测性：`deep_research/observability.py`。
- 持久化：`deep_research/persistence/*`。
- Catalog：`deep_research/catalog/*`、`catalog_api.py`。
- 数据库迁移：`alembic/versions/*`。
- 前端路由：`frontend/src/main.tsx`。
- 前端 API：`frontend/src/api/client.ts`。
- 主要页面：`frontend/src/pages/*`。
- 工作流画布：`frontend/src/components/WorkflowFlowCanvas.tsx`。
- 测试：`tests/*`、`frontend/src/**/*.test.ts`。
