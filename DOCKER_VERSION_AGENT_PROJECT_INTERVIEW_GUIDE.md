# Deep Research Agent：当前 Docker 运行版本功能原理与面试详解

> 本文的“Docker 版本”只表示分析范围：以当前 `docker compose` 启动后实际运行的 FastAPI、React、PostgreSQL 和 Agent 代码为准。
>
> 本文重点不是 Docker 如何部署，而是该版本具备哪些 Agent 能力、为什么这样设计、代码如何实现、面试时如何表达。桌面程序、离线评测脚本和纯本地 SQLite 启动路径不计入本版本功能。

## 1. 项目一句话定位

- 这是一个可视化、多工作流、可恢复的多 Agent 深度研究系统。
- 用户输入复杂问题后，系统会完成问题拆解、依赖规划、并行搜索、证据抽取、反思补洞、引用约束综合和报告复核。
- 系统同时提供声明式工作流、DAG 编排、动态 Agent 组队、多模型路由、节点级重试和 Checkpoint 恢复，而不是把 Planner、Researcher、Synthesizer 写死在一个函数中。

面试精炼表达：

> 我实现了一套 Workflow-as-Data 的多 Agent 深度研究平台。系统以 Blackboard 作为共享状态契约，以统一 Agent 协议解耦角色和编排，支持线性流程、DAG、条件路由、动态自组队和多团队 Map-Reduce；研究链路包含依赖感知的并发检索、证据反思补洞和真实 URL 引用约束，并通过节点级重试、Token Budget、Checkpoint、数据库租约和 SSE 事件流解决长任务可靠性与可观测性问题。

## 2. 当前版本功能全景

### 2.1 用户能够直接使用的功能

- 创建深度研究任务。
- 选择 deep、quick、reviewed、auto、teams 等内置工作流。
- 为单次研究设置子问题数、反思轮数、并发数、搜索结果数和 Token 预算。
- 实时查看 Planner、Researcher、Reflector、Synthesizer 等 Agent 的执行事件。
- 实时查看报告流式生成、Token、耗时、发现数量和总体进度。
- 查看子问题依赖 DAG 和工作流节点执行状态。
- 查看研究历史、报告、引用、标签和运行轨迹。
- 下载或复制 Markdown 报告。
- 创建可视化自定义工作流。
- 创建自定义 Agent 角色卡片。
- 为不同 Agent 绑定不同模型档案。
- 配置多个 Tavily 搜索 key，并按优先级故障转移。
- 修改全局模型和研究参数。
- 对中断且具备 Checkpoint 的运行执行恢复。

### 2.2 系统内部具备的 Agent 工程能力

- 统一 Agent 执行协议。
- Blackboard 显式共享状态。
- Workflow-as-Data 声明式流程。
- 线性工作流解释执行。
- DAG 拓扑分层并发。
- 条件边与三种 Join 语义。
- 运行时自主生成工作流。
- 多团队 Map-Reduce。
- 数据库驱动的自定义 Agent。
- Agent 级模型路由。
- 结构化 LLM 输出与自动重试。
- 真实搜索来源约束。
- 反思式增量补洞。
- 节点超时、重试、退避、Fallback、Fail-fast。
- Token 统计和软预算控制。
- WorkflowRun/StepRun 状态机。
- Checkpoint 和工作流定义快照。
- 数据库恢复租约。
- SSE 多订阅实时事件和历史回放。

## 3. 系统总体架构

### 3.1 表现层

- React 提供研究创建、运行详情、历史记录、工作流构建器、角色广场和全局设置。
- React Query 管理查询缓存和写操作后的缓存失效。
- EventSource 订阅 SSE，实时接收 Agent 事件。
- React Flow 提供工作流节点画布。
- 代码位置：`frontend/src/pages/*`、`frontend/src/hooks/*`、`frontend/src/components/*`。

### 3.2 API 与任务入口层

- FastAPI 接收研究请求并创建持久化 run。
- API 请求只负责创建任务，实际研究通过 `asyncio.create_task` 在后台执行。
- 每个运行建立独立 EventHub，为多个 SSE 客户端扇出事件。
- 代码位置：`deep_research/api.py`。

### 3.3 Agent 编排层

- `WorkflowEngine` 解释 Workflow 数据。
- `OrchestrationRuntime` 管理 WorkflowRun 和 StepRun 生命周期。
- 内置工作流和数据库自定义工作流都进入同一引擎。
- 代码位置：`deep_research/workflow.py`、`deep_research/workflows.py`、`deep_research/orchestration/*`。

### 3.4 Agent 能力层

- Planner：问题拆解。
- Researcher：检索和证据抽取。
- Reflector：证据充分性评估。
- Synthesizer：引用约束报告生成。
- Critic：报告复核。
- Coordinator：动态工作流生成。
- Aggregator：多团队结果归并。
- CardAgent：数据库自定义角色适配器。
- 代码位置：`deep_research/agents/*`。

### 3.5 模型和工具层

- LLM 封装 OpenAI-compatible Chat Completions。
- SearchTool 抽象搜索工具。
- TavilySearch 和 TavilyKeyPoolSearch 提供搜索能力。
- CatalogRuntime 根据 Agent 名称解析专属模型。
- 代码位置：`deep_research/llm.py`、`deep_research/tools/*`、`deep_research/catalog/runtime.py`。

### 3.6 状态和持久化层

- PostgreSQL 保存研究、结果、报告、事件、工作流运行、步骤运行、模型档案、角色卡片、搜索 key 和自定义工作流。
- SQLAlchemy async 负责异步数据库访问。
- Alembic 维护数据库版本。
- 代码位置：`deep_research/persistence/*`、`alembic/versions/*`。

## 4. 一次 deep 研究的完整调用链

1. 用户在新建研究页输入问题并选择 `deep`。
2. 前端调用 `POST /api/runs`。
3. API 在 `research_run` 创建 pending 记录，创建 EventHub 和后台 Task。
4. `DeepResearchAgent` 构造 Tracer、LLM、SearchTool、CatalogRuntime、TokenBudget 和 WorkflowEngine。
5. Planner 将问题解析为 `ResearchPlan` 和多个带依赖的 `SubQuestion`。
6. Researcher 把子问题依赖构造成 DAG。
7. 调度器使用 Kahn 算法生成拓扑层。
8. 同一层中的子问题并发搜索，不同层之间保持依赖顺序。
9. 每次搜索返回真实 URL、标题和内容片段。
10. LLM 从搜索片段中抽取结构化 Finding，并绑定真实 source URL。
11. Reflector 阅读已有 Finding，判断证据是否充分。
12. 如果不足，Reflector 产生少量新增问题，Researcher 只研究新增问题。
13. Synthesizer 对所有 Finding 建立引用编号，流式生成 Markdown 报告。
14. 系统自动追加真实来源列表，避免由模型自行编造 References。
15. 运行期间每个步骤更新 StepRun，关键点保存 Blackboard Checkpoint。
16. Tracer 将事件推送 EventHub，前端通过 SSE 展示。
17. 最终计划、Finding、报告、事件和编排状态写入 PostgreSQL。

主要代码链：

- `api.py::create_run`
- `api.py::_execute`
- `orchestrator.py::DeepResearchAgent.run`
- `workflow.py::WorkflowEngine.run`
- `agents/planner.py`
- `agents/researcher.py`
- `scheduler.py::research_dag`
- `agents/reflector.py`
- `agents/synthesizer.py`

## 5. Agent 统一协议与 Blackboard

### 5.1 为什么需要统一 Agent 协议

- 如果 Planner、Researcher、Synthesizer 各自暴露不同方法，编排器必须理解每个角色的业务参数。
- 新增角色时需要修改编排器，角色与流程形成强耦合。
- 本项目让所有角色统一实现：

```python
async step(bb: Blackboard, ctx: RunContext) -> Blackboard
```

- 编排器只负责按 Workflow 调用 step，不关心角色内部如何使用 LLM 或工具。
- 代码位置：`deep_research/agents/base.py::Agent`。

### 5.2 Blackboard 保存什么

- `query`：原始研究问题。
- `plan`：Planner 产生的研究计划。
- `results`：Researcher 产生的结构化结果。
- `reflections`：Reflector 的充分性判断。
- `report`：最终 Markdown 报告。
- `scratch`：动态工作流、子任务、批评结果、恢复信息等扩展状态。

### 5.3 Blackboard 的价值

- Agent 不需要互相直接调用。
- 流程可以自由调整角色顺序。
- 状态可以序列化为 Checkpoint。
- 测试可以构造任意中间状态。
- 自定义 Agent 可以复用相同状态协议。

### 5.4 Blackboard 的边界

- `scratch` 不能无限扩张成无约束字典。
- 并行节点写相同 key 时，目前按声明顺序覆盖。
- 当前实现保证确定性，不代表自动解决业务字段冲突。
- 更成熟的方案是为字段声明 append、set-union、first-wins 或自定义 reducer。

## 6. Planner：如何拆解复杂问题

### 6.1 输入与输出

- 输入：原始 query。
- 输出：`ResearchPlan`。
- `ResearchPlan` 包含问题解释和 `SubQuestion[]`。
- 每个 SubQuestion 可以声明 `depends_on`。

### 6.2 原理

- 通过结构化 LLM 输出让模型返回符合 Pydantic Schema 的 JSON。
- 限制子问题最大数量，避免任务无限膨胀。
- 依赖字段让“需要先获得背景事实的问题”排在后续层。

### 6.3 面试回答重点

- 项目不是简单把问题随机拆成 N 份。
- 子问题携带依赖关系，后续 Researcher 会执行依赖感知的调度。
- Planner 的价值是建立研究空间和执行顺序，而不仅是生成提纲。

## 7. Researcher：依赖感知的并发检索

### 7.1 DAG 构建

- `build_dag` 清洗越界依赖、自环和重复依赖。
- `detect_cycle` 检测循环。
- 发现循环时降级为无依赖并行，保证系统不会死锁。
- `topo_layers` 使用 Kahn 算法生成层级。

### 7.2 并发策略

- 同层子问题没有相互依赖，可以并发执行。
- 后层等待前层完成，并把前驱 Finding 作为上下文。
- Researcher 内部通过 Semaphore 限制最大并发。
- 单个子问题失败不会终止同层其他任务。

### 7.3 为什么不用所有子问题直接 gather

- 直接 gather 无法处理依赖。
- 后续问题可能需要前置事实才能形成更精确的检索词和判断。
- 分层并发在依赖正确性和性能之间取得平衡。

### 7.4 搜索结果如何进入 LLM

- Tavily 返回 URL、标题和内容片段。
- 系统将网页内容视为不可信数据，而不是系统指令。
- LLM 只负责从给定片段抽取结构化 Finding。
- Finding 必须绑定实际搜索结果中存在的 source URL。

### 7.5 搜索 key 池

- 数据库可配置多个 key，并按 priority 排序。
- 遇到配额、限流或鉴权类错误时切换下一个 key。
- 切换后保持粘滞，避免持续请求失效 key。
- 如果数据库没有启用 key，才使用环境变量 `TAVILY_API_KEY`。
- 数据库 key 池全部失败后不会回退环境 key。

## 8. 证据约束与引用防幻觉

### 8.1 项目如何降低引用幻觉

- 来源 URL 来自搜索工具，不由 LLM 自由生成。
- Researcher 输出的 Finding 必须引用真实候选 URL。
- Synthesizer 接收的是系统编号后的证据材料。
- Prompt 要求保留 `[n]` 角标，不生成自己的参考来源列表。
- 最终 References 由系统根据真实 URL 自动追加。

### 8.2 能否声称完全消除幻觉

- 不能。
- 系统降低的是伪造 URL 和脱离材料生成事实的风险。
- 当前没有完整的句子级 entailment 检测，也没有保存网页原文快照供长期审计。
- `source` 数据表虽然存在，但当前主运行路径没有调用 `save_sources`；历史来源主要通过 `finding.source_url` 和 `report.citations` 保留。

### 8.3 与普通 RAG 的区别

- 普通 RAG 通常是一次检索后生成。
- 本项目包含规划、依赖调度、多轮补洞、工作流控制、状态恢复和全链路事件。
- 它更接近面向复杂任务的 Agent workflow，而不是单次 Retrieval-Augmented Generation。

## 9. Reflector：有界反思补洞

### 9.1 Reflector 做什么

- 阅读现有结果摘要。
- 判断是否足以回答原问题。
- 输出证据缺口和新增子问题。
- 每轮新增问题数量受限制。

### 9.2 为什么是增量补洞

- 已完成的检索结果继续保留。
- 新一轮只研究新增问题。
- 避免每轮从头执行 Planner 和全部 Researcher。
- 降低 Token、搜索调用和整体延迟。

### 9.3 如何保证反思不会无限循环

- `max_rounds` 限制轮数。
- TokenBudget 限制累计消耗。
- Reflection 充分、没有新问题或预算耗尽都会终止。

### 9.4 面试中如何避免夸大

- 反思不是“模型自己想一遍就必然更正确”。
- 项目把反思限制为结构化的证据充分性判断，并要求输出具体补洞问题。
- 是否真正提升质量仍需要固定评测集验证。

## 10. Synthesizer：流式引用报告

### 10.1 合成流程

- 汇总所有 Finding。
- 对来源 URL 去重并编号。
- 将事实和引用编号组织成受约束素材。
- 调用 LLM 流式生成 Markdown。
- 将 token delta 通过 Tracer 和 SSE 推送前端。
- 结束后追加真实来源列表。

### 10.2 为什么流式输出

- 长报告生成时间较长。
- 用户可以立即看到正文增长，而不是等待完整响应。
- Token 和耗时也可实时更新。

### 10.3 预算耗尽时为什么仍允许 Synthesizer 执行

- 如果直接停止，用户可能只得到中间 Finding，没有最终可读结果。
- 当前策略是跳过非终端步骤，但保留 Synthesizer/Aggregator。
- 这是软预算策略，不能描述为严格费用硬上限。

## 11. Critic：报告复核

- 输入最终报告。
- 输出总体评价、issues 和 suggestions。
- 结果保存在 Blackboard scratch，并进入事件流。
- Critic 当前不会自动重写报告。
- `reviewed` 工作流将 Critic 放在 Synthesizer 之后。

面试表达：

- Critic 是“独立复核角色”，不是另一轮无约束自我反思。
- 当前实现把复核意见和最终正文分离，便于观察 Critic 是否真正发现问题。

## 12. 五种内置工作流

### 12.1 deep

- Planner → Researcher → Reflect Loop → Synthesizer。
- 适合复杂、需要多角度和证据补洞的问题。

### 12.2 quick

- Planner → Researcher → Synthesizer。
- 省略反思，适合简单问题和低成本场景。

### 12.3 reviewed

- deep → Critic。
- 适合需要报告质量检查的任务。

### 12.4 auto

- Coordinator 根据问题现场生成步骤列表。
- 生成结果经过规则校验。
- 非法时自修复一次，仍失败则回退 deep。

### 12.5 teams

- 父 Planner 产生子主题。
- 多个隔离子团队并行执行 Researcher。
- 父 Aggregator 统一生成报告。
- 默认子团队只有 Researcher，不会各自重复 Planner 和 Synthesizer。

## 13. Workflow-as-Data

### 13.1 核心思想

- 流程不写死在 Python if/else 中，而是由 Workflow、Step、Node 和 Edge 描述。
- 同一引擎可以执行内置流程、用户画布流程和 LLM 动态生成流程。

### 13.2 Step 支持的控制原语

- `agent`：执行一个 Agent。
- `reflect_loop`：反思和研究循环。
- `compose`：动态生成并递归执行工作流。
- `team_fanout`：多团队并行和归并。

### 13.3 数据化带来的收益

- 流程可以持久化。
- 流程可以版本迭代。
- 流程可以由前端画布编辑。
- 流程可以由 LLM 生成。
- 运行时可以保存定义快照并恢复。

注意：自定义 Workflow Catalog 本身有 version；当前 WorkflowRun 保存的是定义 snapshot，没有单独保存和回显 Catalog version。

## 14. 图工作流、条件路由和 Join

### 14.1 图执行

- 节点表示 Step。
- 边表示依赖和可选条件。
- 引擎先校验图，再拓扑分层执行。
- 同层节点分别复制父 Blackboard，在隔离状态中执行。
- 一层完成后按节点声明顺序合并增量。

### 14.2 条件表达式为什么不用 eval

- `eval` 可执行任意 Python，用户定义条件会成为远程代码执行入口。
- 项目只支持受限状态路径、JSON 字面量和有限比较运算。
- 代码位置：`deep_research/orchestration/conditions.py`。

### 14.3 三种 Join

- `any`：任一上游条件成立即可执行。
- `all`：所有上游路径都激活即可执行，不要求全部成功。
- `success_all`：所有上游都激活且执行成功。

### 14.4 并行状态如何合并

- 每个节点拿 Blackboard 深拷贝。
- results/reflections 只追加相对父状态的新元素。
- plan/report 使用明确覆盖。
- scratch 按声明顺序 update。
- 这样避免共享对象并发写，但同 key 仍可能发生确定性覆盖。

## 15. Coordinator：受约束的自主编排

### 15.1 为什么不是让 LLM 任意生成代码

- Coordinator 只生成结构化 Step 列表。
- 角色来自白名单。
- 步骤数量有硬上限。
- 反思轮数受限制。
- 流程必须包含终端输出角色。
- 禁止危险或无限嵌套控制原语。

### 15.2 自修复和回退

- 首次生成非法时，把校验错误反馈给模型。
- 第二次仍非法时回退内置 deep。
- 运行零产出时可以有限次数重规划。
- 递归深度和 Token Budget 共同保证终止。

### 15.3 面试亮点

- 自主性不是“让模型控制一切”。
- 可靠 Agent 系统应该把模型决策限制在结构化、可校验、可回退的动作空间中。

## 16. 多团队 Map-Reduce

### 16.1 Map 阶段

- 父流程把多个 focus 分配给子团队。
- 每个子团队拥有独立 Blackboard。
- 默认每个子团队执行一次 Researcher。
- 通过 Semaphore 限制团队并发数。

### 16.2 Reduce 阶段

- 父流程按固定顺序合并各团队 ResearchResult。
- Aggregator 复用 Synthesizer 的证据编号和报告生成能力。
- 单团队失败返回 None，不拖垮其他团队。

### 16.3 适用场景

- 一个大问题可以分为多个互相独立的专题。
- 不适合强顺序依赖、需要频繁跨团队共享中间状态的任务。

## 17. 数据驱动 Agent 与角色广场

### 17.1 自定义 Agent 如何实现

- 数据库 AgentCard 保存 name、behavior、system_prompt、model_profile 和 enabled。
- behavior 决定复用哪个内置执行实现。
- system_prompt 可以覆盖内置默认 Prompt。
- model_profile 决定该 Agent 使用哪个模型。
- CardAgent 将数据库配置适配为统一 Agent 协议。

### 17.2 支持的 behavior

- plan。
- research。
- reflect。
- synthesize。
- critique。

### 17.3 优点与边界

- 新角色不需要改 WorkflowEngine。
- 可以通过数据配置同一种行为的不同专家 Prompt 和模型。
- 当前不是任意工具插件系统；自定义角色只能复用已有 behavior 和已有工具权限。

## 18. 多模型路由

### 18.1 模型档案

- 每个档案保存 base URL、API key、model 和生成参数。
- AgentCard 可以绑定模型档案。
- 未绑定时使用全局默认模型。

### 18.2 OpenAI-compatible 兼容

- 使用标准 Chat Completions 接口。
- 可接 OpenAI、DeepSeek、Qwen、GLM、Moonshot 等兼容服务。
- 结构化输出不依赖特定厂商 JSON Mode。

### 18.3 参数模式

- temperature 模式发送 temperature。
- reasoning 模式发送 reasoning_effort，并省略 temperature。
- 避免部分推理模型拒绝不支持的参数组合。

### 18.4 面试中的模型分工思路

- Planner/Reflector 可以使用推理能力更强的模型。
- Researcher 的抽取可以使用更快、更便宜的模型。
- Synthesizer 可以使用长上下文和写作能力更好的模型。
- 项目已具备角色级路由基础，但具体成本收益需要实测。

## 19. 结构化输出可靠性

- 将 Pydantic JSON Schema 注入 Prompt。
- 从代码块或额外文本中提取 JSON 对象。
- 使用 Pydantic 做字段和类型校验。
- 解析失败时把错误反馈给模型后重试。
- 网络失败和解析失败共享有限重试预算。
- 代码位置：`deep_research/llm.py::parse`。

为什么不用 Provider JSON Mode：

- 保持对不同 OpenAI-compatible 服务的兼容性。
- 代价是可靠性低于原生 constrained decoding，需要解析、校验和重试兜底。

## 20. 节点级可靠性

### 20.1 支持的策略

- 超时：`asyncio.timeout`。
- 重试：`max_attempts`。
- 指数退避：`retry_backoff * 2**attempt`。
- Fallback Agent：主角色耗尽重试后切换替代角色。
- Continue：记录失败并继续后续节点。
- Fail-fast：工作流直接失败。

### 20.2 为什么错误事件要区分层级

- 单个 Agent 的 error 可能已经被隔离，整个研究仍能继续。
- 只有 ORCHESTRATOR 的 done/error 被前端视为最终状态。
- 否则 Researcher 的局部失败会导致 SSE 页面提前停止。

## 21. Token 统计与预算

- 普通请求优先使用 provider usage。
- provider 不返回 usage 时使用字符估算。
- 流式生成期间持续估算并实时展示。
- 最终收到 usage 后用精确值校准估算。
- TokenBudget 根据 Tracer 累计值判断是否耗尽。
- 预算耗尽后跳过非终端节点，保留最终报告生成。

边界：

- 这是检查点式软预算。
- 单次正在执行的 LLM 请求可能超过剩余额度。
- 终端步骤仍被允许执行。
- 面试时不能称为严格账单硬限制。

## 22. WorkflowRun、StepRun 与状态机

### 22.1 WorkflowRun

- 保存工作流名称、输入、输出、定义快照、Checkpoint、开始/完成时间和整体状态。

### 22.2 StepRun

- 保存 node_id、label、kind、agent、attempt、error、开始/完成时间和步骤状态。

### 22.3 状态价值

- 前端能够展示每个节点的真实状态。
- Checkpoint 能判断哪些步骤已经完成。
- 失败、跳过、重试和取消不会只存在日志字符串中。

## 23. Checkpoint 与恢复

### 23.1 保存内容

- Blackboard 完整序列化状态。
- Workflow definition 快照。
- WorkflowRun 和 StepRun。

### 23.2 自动恢复

- 服务启动扫描 pending/running 任务。
- 没有 Checkpoint 的孤儿任务标记 error。
- 有 Checkpoint 的任务先获取数据库租约，再恢复执行。
- 已完成步骤被跳过，未完成步骤继续执行。

### 23.3 为什么保存定义快照

- 用户可能在任务中断后修改自定义工作流。
- 恢复必须继续原运行使用的流程，而不是加载最新版本。
- 当前保存 snapshot，但没有单独记录 Catalog version。

### 23.4 当前一致性语义

- 不能称为 exactly-once。
- 外部 LLM 和搜索调用没有完整幂等协议。
- 租约没有 fencing token，旧实例在极端情况下可能继续写入。
- 更准确的说法是具备 Checkpoint 的 at-least-once 恢复基础。

## 24. 可观测性与 SSE

### 24.1 Event 模型

- stage：事件属于哪个 Agent/编排器。
- type：start、info、finding、round、token、report、done、error。
- elapsed：运行耗时。
- tokens：累计 Token。
- data：结构化附加信息。

### 24.2 Tracer

- 收集非 Token 事件。
- 统计和校准 Token。
- 向 CLI、持久化和实时 sink 分发事件。

### 24.3 EventHub

- 每个 run 一个 Hub。
- 每个 SSE 客户端一个独立有界队列。
- 支持多个浏览器同时观看。
- 非 Token 事件可供迟到订阅者回放。
- 慢消费者不会阻塞 Agent 主流程。

### 24.4 断线处理

- 前端连接中断后关闭 EventSource。
- RunPage 改为轮询数据库详情。
- 最终状态和报告可以恢复，但中间事件时间线可能不完整。

## 25. 历史与数据持久化

- 保存研究问题、状态、Token 和耗时。
- 保存计划子问题和依赖。
- 保存 Finding 和 source URL。
- 保存最终报告和 citation URL。
- 保存可回放的非 Token 事件。
- 保存 WorkflowRun 和 StepRun。
- 支持关键词、状态和标签筛选。

注意：独立 `source` 表当前没有被主研究路径写入，不能声称已保存完整网页内容快照。

## 26. 前端如何体现 Agent 系统能力

- 新建研究页：选择工作流和研究参数。
- RunPage：展示问题、状态、Agent 时间线、工作流步骤、DAG、报告和实时统计。
- WorkflowBuilder：将 Workflow-as-Data 可视化。
- AgentSquare：管理模型档案、角色卡片和搜索 key。
- Settings：管理全局模型和研究行为。
- History：回放历史运行和报告。

面试表达：

- 前端不是普通聊天框，而是 Agent Runtime 的可视化控制面和观测面。
- 它把工作流定义、节点状态、证据过程和最终报告分开展示。

## 27. 当前版本最值得讲的三个创新点

### 27.1 Workflow-as-Data 的多 Agent 编排

- Agent、状态和控制流解耦。
- 同一引擎覆盖固定、可视化和动态工作流。
- 支持 DAG、条件、Join、可靠性策略和恢复。

### 27.2 证据约束的研究闭环

- 依赖感知的问题拆解和并行检索。
- Finding 绑定真实来源。
- Reflector 只做有界增量补洞。
- Synthesizer 使用系统编号引用。

### 27.3 面向长任务的可靠性与观测

- Step 状态机、重试、Fallback 和 Fail-fast。
- Token 预算。
- Checkpoint 和数据库租约。
- SSE 实时事件和历史回放。

## 28. 面试高频问题与回答要点

### 28.1 为什么使用多 Agent，而不是一个大 Prompt

- 复杂研究包含规划、工具调用、证据判断和写作等不同目标。
- 单 Prompt 难以独立控制每阶段模型、失败策略、输入输出 Schema 和成本。
- 多 Agent 的价值是职责隔离和可编排，不是角色数量越多越好。

### 28.2 为什么不用 LangGraph

- 项目目标之一是自己实现 Agent Runtime 的核心机制，深入理解状态、调度、失败和恢复语义。
- 自研引擎能精确展示 Workflow-as-Data、Blackboard、Join 和 Checkpoint 的设计。
- 生产项目是否使用 LangGraph/Temporal 应根据团队维护成本决定，而不是否定成熟框架。

### 28.3 DAG 并发如何保证没有竞态

- 同层节点运行在 Blackboard 深拷贝上。
- 完成后按固定顺序合并增量。
- 避免共享对象并发写。
- 仍需承认相同 scratch key 的语义冲突尚未由 reducer 自动解决。

### 28.4 如何防止 Prompt Injection

- 搜索内容按不可信证据处理，不赋予工具或系统权限。
- Agent 的 system prompt 与网页内容分离。
- 自定义角色只能复用已有 behavior，Prompt 不能自动获得新工具。
- 当前还可进一步增加网页清洗、内容分区和注入分类器。

### 28.5 如何控制成本

- quick/deep 等不同工作流。
- 子问题、反思轮数、搜索数和并发上限。
- Agent 级模型路由。
- TokenBudget。
- 增量补洞而不是全流程重跑。

### 28.6 服务重启后如何恢复

- 数据库保留 Blackboard 和工作流定义快照。
- 启动扫描异常中断任务。
- 获取租约后恢复，跳过已完成步骤。
- 外部调用仍是 at-least-once，需要幂等和 fencing token 才能进一步加强。

### 28.7 如何评价系统效果

- 质量：覆盖度、引用支撑、深度、结构和事实正确性。
- 成本：Token、搜索次数和模型费用。
- 性能：P50/P95 总耗时、并发收益。
- 可靠性：重试成功率、恢复成功率、重复调用率。
- 当前仓库有离线 eval 代码，但它不在 Docker 运行镜像中，因此不能算 Docker UI 的在线功能。

## 29. 面试时必须主动说明的边界

- 当前后台任务仍运行在 FastAPI 进程，不是独立分布式 Worker。
- Checkpoint 恢复不是 exactly-once。
- TokenBudget 是软预算。
- Blackboard 合并缺少字段级 reducer。
- 密钥当前以明文形式存在运行时配置文件或数据库中。
- SSE 慢消费者可能丢中间事件，最终详情由数据库兜底。
- source 表当前未接入主持久化链路。
- 自定义 Agent 是数据驱动 behavior 复用，不是任意代码或任意工具插件。
- Docker 运行版不包含离线 LLM-as-judge 评测脚本。

主动说明这些边界不会削弱项目，反而能体现对 Agent 系统生产问题的理解。

## 30. 简历项目描述模板

### 项目名称

Deep Research Agent｜可恢复的多 Agent 深度研究与可视化编排平台

### 项目简介

面向复杂研究任务构建的多 Agent 系统，支持问题规划、依赖感知的并发检索、证据反思补洞、引用约束报告、多工作流编排、自定义 Agent、多模型路由、节点级可靠性、Checkpoint 恢复和 SSE 实时观测。

### 三条核心职责

1. 设计 Workflow-as-Data 编排内核，以 Blackboard 和统一 Agent 协议解耦角色、状态和控制流，支持线性工作流、DAG、条件 Join、动态自组队与多团队 Map-Reduce。
2. 构建证据约束研究闭环，通过子问题依赖建模、拓扑分层并发 Tavily 检索、结构化 Finding、真实 URL 绑定和增量反思补洞降低研究遗漏与引用幻觉。
3. 建设长任务可靠性和可观测体系，实现节点超时/重试/Fallback/Fail-fast、Token 软预算、WorkflowRun/StepRun 状态机、Blackboard Checkpoint、数据库恢复租约和 SSE 实时执行轨迹。

## 31. 两分钟面试介绍示例

> 这个项目不是普通的搜索加生成 Demo，而是一套可配置的多 Agent 研究 Runtime。用户输入问题后，Planner 会产生带依赖关系的子问题，Researcher 使用拓扑分层实现层内并发、层间依赖，并从 Tavily 搜索结果中抽取绑定真实 URL 的 Finding。Reflector 会判断证据缺口并做有限轮次的增量补洞，Synthesizer 最后根据系统编号的证据流式生成带引用报告。
>
> 在架构上，我把角色、状态和流程拆成三层：Agent 统一实现 step 协议，Blackboard 保存显式共享状态，Workflow 则以数据描述线性步骤、DAG、条件、Join、自主编排和多团队 Map-Reduce。这样内置流程、用户画布创建的流程和 Coordinator 动态生成的流程都可以进入同一个引擎。
>
> 工程上我重点解决长任务问题，包括节点超时、重试、Fallback、Token Budget、Step 状态机、Checkpoint、数据库恢复租约和 SSE 实时观测。当前系统适合单机 Docker 部署和 Agent 平台能力展示，但我也会明确它还不是完整分布式任务系统，恢复语义仍是 at-least-once，下一步会增加独立 Worker、fencing token、字段级 reducer 和更完整的评测体系。

## 32. 关键代码索引

- API 与运行入口：`deep_research/api.py`。
- 总编排器：`deep_research/orchestrator.py`。
- WorkflowEngine：`deep_research/workflow.py`。
- 内置工作流：`deep_research/workflows.py`。
- Agent 协议与 Blackboard：`deep_research/agents/base.py`。
- Planner：`deep_research/agents/planner.py`。
- Researcher：`deep_research/agents/researcher.py`。
- Reflector：`deep_research/agents/reflector.py`。
- Synthesizer：`deep_research/agents/synthesizer.py`。
- Critic：`deep_research/agents/critic.py`。
- Coordinator：`deep_research/agents/coordinator.py`。
- Aggregator：`deep_research/agents/aggregator.py`。
- 数据驱动 Agent：`deep_research/agents/card_agent.py`。
- 子问题 DAG：`deep_research/dag.py`、`scheduler.py`。
- 图工作流：`deep_research/orchestration/graph.py`。
- 条件路由：`deep_research/orchestration/conditions.py`。
- 状态机：`deep_research/orchestration/runtime.py`、`types.py`。
- LLM 封装：`deep_research/llm.py`。
- Token Budget：`deep_research/token_budget.py`。
- 可观测性：`deep_research/observability.py`。
- 搜索工具：`deep_research/tools/*`。
- Catalog 和模型路由：`deep_research/catalog/*`、`catalog_api.py`。
- PostgreSQL 持久化：`deep_research/persistence/*`。
- 前端运行页：`frontend/src/pages/RunPage.tsx`。
- 工作流构建器：`frontend/src/pages/WorkflowBuilderPage.tsx`、`components/WorkflowFlowCanvas.tsx`。
- 角色广场：`frontend/src/pages/AgentSquarePage.tsx`。
