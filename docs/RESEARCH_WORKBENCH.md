# Science Research 科研工作台

Science Research 是面向科研人员的研究工作台：一级入口是科研任务（七类任务 + 学术问答），交付物统一走「同源生成 → 机械验收 → 登记」流程。代码位于 `deep_research/workbench/`，前端首页即工作台。

部署形态：模型全部通过 API 调用云端 LLM，部署服务器不做 GPU 或训练调度；PDF、Word、PPT、思维导图、统计图表等交付物在部署服务器本地生成。

## 任务与检索策略

「怎么找证据」与「交付什么」是两件正交的事。原来的深度研究链路（多子问题检索 → 逐字核验 → 反思补洞）现在是**检索策略**之一，任何需要检索的任务都可以选它：

| 策略 `strategy` | 含义 |
|---|---|
| `none` 不检索 | 只用用户提供的论文或数据 |
| `quick` 快速检索 | 规划后检索一轮即写作 |
| `deep` 深度检索 | 多子问题并行检索、逐字核验、反思补洞 |

| 任务 | 可选策略（默认加粗） | 工作流 | 终端角色 | 交付物 |
|---|---|---|---|---|
| 课题调研 `autoResearch` | **deep** / quick | `research` / `research_quick` | research_writer | md / docx / pdf / html |
| 文献综述 `litReview` | **deep** / quick | `lit_review` / `lit_review_quick` | survey_writer | md / docx / pdf / html |
| 同行评审 `peerReview` | **none** | `peer_review` | peer_reviewer | md / docx / pdf / html |
| 论文精读 `paperRead` | **none** | `paper_read` | paper_reader | md / docx / pdf / html |
| 数据分析 `dataAnalysis` | **none** | `data_analysis` | data_analyst | md / docx / pdf / html / xlsx / png |
| 幻灯片 `slides` | **quick** / deep | `slides` / `slides_deep` | slide_writer | pptx / md |
| 思维导图 `mindmap` | **quick** / deep | `mindmap` / `mindmap_deep` | mindmap_writer | 交互 html / png / md |

创建运行时传 `template` 和可选的 `strategy`；任务不支持的策略返回 422 `unsupported_strategy`。策略写进任务契约并冻结进 checkpoint。档位（见下文）调节检索深度，不设置 token 预算。课题调研仍可在「高级：自定义研究流程」里改用自定义工作流。

模板定义在 `workbench/templates.py`，是纯数据：包括输入类型、策略表、必答章节（及其同义标题）、交付格式、最少引用数和篇幅下限。前端、任务契约、写作者提示词和验收门都读取这同一张表，所以新增一类任务只需要改这一处。

导航：工作台 / 学术问答 / 任务记录 / 资料库 / 设置。工作流构建与角色广场属于高级定制，入口在「设置 → 高级」。

## 任务契约

创建研究时，如果带上 `template` 字段，服务端会调用 `build_contract` 把用户原话规整成任务契约，并冻结进初始 checkpoint（`scratch.task_contract`）。契约包含以下内容：

- 解析出的论文指针：arXiv / DOI / URL，自动去重并规范化；
- 数据分析的问题与数据：可以直接把 CSV 粘在问题下方，也可以用 `dataset` 字段单独上传；
- 必答章节、交付格式、约束和证据纪律。

契约是确定性的，同样的输入永远得到同样的契约。恢复运行、worker 执行和交付生成读到的都是创建时那一份。`POST /api/templates/contract` 可以在提交前预览系统会怎样理解这次任务。

## 证据纪律（所有任务一致）

- 写作者只接收通过逐字核验和语义核验的发现，素材编号固定为 `[n]`；
- 正文由 `finalize_report` 复核引用编号和数值，不合格就回退为「已核验素材摘要」；
- 同行评审的评分属于审稿人的判断，不是来源事实，所以在复核之前单独抽出，以「审稿结论」段落追加回正文；
- 数据分析的全部数字由确定性代码计算（pandas / scipy：描述统计、Welch t / 单因素方差分析，以及 Mann–Whitney / Kruskal–Wallis 稳健性复核、Pearson 相关）。模型只负责解释这些数字，正文里一旦出现统计台账以外的数字，就回退为统计摘要。编号列（例如场景 1..k）会被识别出来，不当作测量值处理。

## 同源交付

交付物以定稿 Markdown 为唯一内容真源，所有格式都由 `delivery/markdown.py` 产出的同一棵块树派生：

- **HTML**：单文件自包含，CSS 内联、图片 base64 内联，不含脚本（思维导图的折叠交互是唯一例外，且不访问网络）。原始 HTML 在解析阶段就被当作文本处理。
- **PDF**：由 PyMuPDF Story 排版为 A4，并做字体子集化（3 页中文约 60 KB），不依赖 LibreOffice 或 TeX。生成后自检两项：页数，以及正文最后一段是否出现在 PDF 末尾（用来拦截静默截断）。
- **DOCX**：统一样式基底，包括字体、标题配色、表格底纹和页码。
- **PPTX**：16:9 固定设计系统，每页附演讲备注，并估算每页行数以提示版面溢出。
- **思维导图**：确定性的径向布局，SVG（交互 HTML）与 PNG 共用同一套布局。

## 验收门（`workbench/gates.py`）

| 门 | 检查 | 失败级别 |
|---|---|---|
| citation | 引用编号越界 / 已核验引用数低于模板下限 | 越界为 fail，数量不足为 warn |
| markdown | 裸 HTML 标签 / 页内锚点 / 未闭合代码块 | 裸标签为 fail |
| structure | 模板必答章节（按标题或同义标题匹配）；导图分支和节点下限；幻灯片页数 | warn |
| length | 正文篇幅下限 | warn |
| review | 评审是否给出 1–10 分 | fail |
| consistency | DOCX 内嵌图数 = HTML 内联图数 = Markdown 图数；PDF 自检；HTML 无脚本 | fail |
| slides | 备注覆盖率、版面溢出估算 | warn |
| scholarly | 学术文体、摘要禁引用、引用堆砌、重复来源、引用下限、时效覆盖、局限说明（`workbench/scholarly.py`） | warn（「质量不合格即判失败」开启时升为 fail） |
| revision | 写作返工次数与用尽后仍未解决的问题 | warn |
| territory | 各格式可见文本的地名规范 | fail |
| render | 某个交付格式渲染崩溃：该格式缺席，其余格式照常交付 | fail |

门的结论写进交付登记 `GET /api/runs/{id}/deliverables`，每个文件都带大小、sha256 和验收状态。引用越界时只发布 Markdown 供排查，不会以其它格式对外交付。

## 交付质量与返工

质量阈值集中在 `workbench/quality.py` 的 `QualityPolicy`，可在前端「设置 → 交付质量」修改（`PUT /api/config` 的 `quality` 字段，部分更新、越界 422）。字段、取值范围、默认值和悬浮说明由 `GET /api/config/quality-schema` 提供，界面与验收逻辑同源。默认值：综述引用下限 20、课题调研 3、单处引用上限 7、最多返工 2 次。策略在创建任务时冻结进任务契约，已开始的任务按创建时的口径验收。

质量保证分三层：

1. **检索阶段**（`workbench/coverage.py`）：按契约计算证据缺口（不同来源数低于引用下限、合格发现过少、点名年份无文献）。缺口写进反思提示词；模型宣布「已充分」但缺口仍在且给出了新子问题时，继续补洞。
2. **写作阶段**（`workbench/revision.py`）：每版草稿经确定性检查（引用与数字逐段复核、模板章节、学术文体与来源检查），不合格就把问题清单和上一版全文交回写作者重写，最多 `max_revisions` 次，最终取问题最少的一版。检索不足（可用来源少于下限）不算写作问题，不消耗返工。
3. **交付阶段**：定稿仍经 `finalize_report` 安全网（引用或数字对不上即回退为素材摘要），再过全部验收门。未完全达标的交付标为「部分完成」，交付页列出硬性问题与改进建议。

摘要类章节（摘要 / 一句话总结）按规范不带引用角标，引用复核对它们免除「必须引用」，但其中数字仍须出现在已核验素材里。

## 学术问答

`/api/qa/conversations`（表 `qa_conversation` / `qa_message`，迁移 0030）。普通问答默认使用模型自身知识，不调用搜索；用户在本轮明确勾选私有资料库或联网检索后，才叠加对应来源。论文精读会固定使用本论文片段，资料库与联网检索仍分别可选。启用来源时依次经过检索、逐字核验、作答、引用复核；无外部来源时直接作答且不生成引用。追问里出现「它 / 第二篇」这类指代时，会用上一轮的问句补全检索式。每条消息都保存来源过程，前端可以展开查看。问答接口使用带 15 秒 keep-alive 的 SSE，完成后返回已持久化消息；检索不到合格证据时，会如实回答「现有检索结果不足以回答」。

## 运行详情：三栏工作区

- **左栏**：步骤轨道（状态、耗时、重试、错误、重规划补救）和人话进度（`GET /api/runs/{id}/narrative`，由事件流纯函数生成）。
- **中栏**：正文、任务卡（论文导入结果 / 评审分数 / 数据规模）和交付物面板。
- **右栏**：产物文件树（`GET /api/runs/{id}/workspace`，成品排在前面），以及机器事件时间线。文件预览只允许读取清单里登记过的路径，并先按清单哈希复核；HTML 产物以纯文本展示。

## 档位、额度与计算资源

- **档位**（`GET /api/tiers`）：轻量 / 标准 / 深度三档，对应子问题数、补洞轮数和每次检索结果数。创建研究时用 `tier` 字段指定；不指定时取模板的 `tier_default`。用户显式给出的 `params` 优先于档位。档位最终生效的参数随运行设置一起冻结进 checkpoint；token 仅统计消耗，不设累计或每日 token 额度。
- **额度**（`GET /api/usage`）：`DAILY_RUN_QUOTA` 和 `DAILY_TOKEN_QUOTA` 限制每个身份每个 UTC 自然日的研究次数和 token 用量，用量直接由运行记录汇总。额度只在创建研究时检查，超出返回 429 `quota_exhausted`，已开始的运行不会被中途打断。前端在档位选择器旁显示今日用量。
- **GPU**：本项目不调度 GPU。外部计划中有步骤声明 `resource.gpu` 时，创建运行直接返回 422 `gpu_unsupported`；执行层也会拒绝这类步骤，不会降级到 CPU 静默运行。

## 任务附件

- **上传即解析**（`POST /api/attachments`）：支持 PDF、Word、PPT、Excel、Markdown、纯文本和 CSV，单文件不超过 16 MB。解析复用资料库同一条链路，PDF 按章节、Office 按标题 / 幻灯片 / 工作表、文本按段落切片，每个片段带定位（如「第 3 页」「第 2 张幻灯片」）。解析只在内存中进行，原始文件不落盘。
- **随任务提交**：创建研究时把解析结果放进 `attachments` 字段。每个任务最多 8 个文件，单文件最多 40 个片段，合计最多 120 个片段。片段冻结进 checkpoint 的 `scratch.attachments`。
- **模型阅读**：`attachment_reader` 是所有任务工作流的第一步，没有附件时不产生任何调用。它每 4 个片段调用一次抽取，结果和检索来源走同一套来源门禁、逐字核验和语义核验。交付物的参考来源显示文件名与定位。

## 地名规范、步骤质量检查与概念图

- **地名规范**：发布前先对定稿做确定性规范化（`territory.normalize`），把涉台称谓统一为「中国台湾 / Taiwan, China」，并修正并列表述。幻灯片页面与导图节点同样处理。之后地名门逐格式检查可见文本，未通过记为 fail。
- **步骤级质量检查**：计划步骤设置 `enable_check: true` 后，执行器会复核本步产物，检查是否全部写出且非空、Markdown 卫生是否合格、地名是否规范。不合格就附上问题清单重做，最多重做 `max_check_attempts` 次；仍不合格时本步以 partial 结束，交给重规划器处理。
- **概念图**：综述、课题调研、精读和幻灯片的写作者会从已核验素材中整理出一张框架图或分类法图的结构描述。部署配置了 `DR_IMAGE_MODEL`（以及可选的 `DR_IMAGE_BASE_URL` / `DR_IMAGE_API_KEY`）时，走图像模型，一图一调用，生成后检查图片尺寸与格式；未配置或调用失败时，由结构描述确定性绘制。概念图会插入正文，并出现在所有交付格式中。

## 重规划

计划步骤以 partial 或 failed 结束时，`PlanExecutor` 会请重规划器判断走 `rescue` 还是 `accept`，补救在同一个步骤、同一份交付契约下执行。限额如下：每次运行最多重规划 3 次，累计最多新增 5 个补救步骤，同一步最多补救 1 次。基础设施错误（租约、鉴权、存储、预算、取消）不做补救。每次重规划都记入 `scratch.replan_state.log`，并显示在左栏。
