# 参考产品功能覆盖清单

> **机械核对**：`python scripts/check_workbench_coverage.py` 直接从资料包的原始文件中抽取功能清单，逐项核对本项目中的实体（模板、路由、门函数、模块、计划字段、步骤状态），当前结果为 **66/66 项覆盖或已说明不适用**（其中 6 项不适用：积分计费、订阅计费、实验套件/GPU、运行期装包、品牌样式门、外部进程等待），退出码为 0。抽取范围包括：任务清单里出现过的全部 `template_key`、设计文档架构图列出的全部平台端点、`_shared/scripts` 下的全部门脚本、`.claude/skills` 下的全部技能目录、真实 `vela-steps.json` 中的全部步骤字段与状态值。资料包新增条目而映射表未跟上时，脚本会报「未映射」并失败。下表是这份结果的可读版本。

对照参考资料包 `apevon-full-package/`（其中 `prompts/Apevon研究链路复刻设计.md`、`repo/MANIFEST.md`、`repo/.claude/skills/_shared/references/delivery-contract.md` 和 `tasks/*`），逐项列出功能在本项目中的实现位置与验证方式。标注说明：「✅ 已实现」表示功能可用且有自动化测试覆盖；「⚙ 等价实现」表示目标相同，但实现手段有意与参考不同，原因写在备注里。

## 一、任务类型（8 类）

| 参考功能 | 状态 | 实现 | 测试 |
|---|---|---|---|
| 深度研究 autoResearch | ⚙ | 改为「课题调研」任务 + 检索策略 `deep`/`quick`；深度检索可用于综述、幻灯片、导图 | `test_workflow`, `test_workbench::test_deliverables_endpoints` |
| 文献综述 litReview（主题组织、方法对比、开放问题、参考文献） | ✅ | `lit_review` / `SurveyWriter`，学术检索后端 | `test_lit_review_run_produces_checked_deliverables` |
| 同行评审 peerReview（五段式 + 1–10 分） | ✅ | `peer_review` / `paper_intake` + `PeerReviewer` | `test_peer_review_uses_named_paper_not_open_search` |
| 数据分析 dataAnalysis（计划→统计→图→中文报告） | ✅ | `data_analysis` / `DataAnalyst`（pandas/scipy/matplotlib） | `test_analysis_*`, `test_data_analysis_run_*` |
| 论文精读 paperRead（6 节 + 摘要翻译 + LaTeX 公式） | ✅ | `paper_read` / `paper_intake` + `PaperReader` | `test_paper_intake_isolates_failures`，模板章节门 |
| 幻灯片 slides（PPTX + 备注） | ✅ | `slides` / `SlideWriter` + `delivery/pptx.py` | `test_slides_and_mindmap_runs_deliver_binary_formats` |
| 思维导图 mindmap（≥6 分支 × ≥5 节点，HTML/PNG） | ✅ | `mindmap` / `MindmapWriter` + `delivery/mindmap.py` | 同上、`test_mindmap_*` |
| 学术问答（多轮、带引用、思考过程） | ✅ | `workbench/qa.py`、`qa_store.py`、`/api/qa/*`、前端 `/qa` | `test_answer_question_*`, `test_qa_endpoints_*`, `QaPage.test` |

## 二、任务输入与规划

| 参考功能 | 状态 | 实现 | 测试 |
|---|---|---|---|
| 用户输入规整为「研究任务契约」 | ✅ | `workbench/contract.py`，冻结进 checkpoint | `test_contract_is_deterministic`, `test_create_run_with_template_*` |
| 澄清提问 | ✅ | `/api/intent/assess` 澄清循环（课题调研） | `test_intent_*` |
| 追问（继续对话） | ✅ | 运行「继续追问」+ 多轮指代消解；问答会话 | `test_intent_context_replay`, `test_followup_query_*` |
| 附件（论文 PDF / 数据文件） | ✅ | `POST /api/attachments` 上传即解析（PDF / Word / PPT / Excel / Markdown / 文本 / CSV），`attachment_reader` 让模型逐片段阅读并逐字核验；论文链接由 `paper_intake` 取回；CSV 上传字段 `dataset` | `test_attachment*`、`AttachmentDropzone.test` |
| planner 只产计划 + schema 自校验 | ✅ | `planning.ExecutionPlan`（id 唯一、资源区间、信封一致） | `test_planning*`, `test_external_plan` |
| 步骤状态 done / partial / skipped / failed | ✅ | `planning.StepStatus`，`sync_plan_from_workflow` | `test_external_plan` |
| 步骤间磁盘交接、reset 上下文 | ✅ | `plan_handoffs` + `ArtifactStore`；`PlanExecutor` 每步新上下文 | `test_external_plan_runs_each_prompt_*` |
| 技能显式点名 | ✅ | `skills.SkillResolver.require_explicit_references` | `test_skills*` |
| 重规划（3 次 / +5 步 / 单步 1 次，已执行冻结） | ✅ | `workbench/replan.py`，接入 `PlanExecutor.step` | `test_failed_plan_step_is_rescued_*`, `test_replan_limits_*` |
| 档位（轻量 / 标准 / 深度） | ✅ | `workbench/tiers.py`，`CreateRunRequest.tier` | `test_tier_*` |

## 三、执行与资源

| 参考功能 | 状态 | 实现 | 测试 |
|---|---|---|---|
| 每步资源声明（CPU / 内存 / GPU / 时限） | ✅ | `planning.ResourceSpec` | `test_planning*` |
| GPU 确认与 GPU 步骤执行 | 不适用 | 部署只通过 API 调用云端 LLM，不调度 GPU；声明 GPU 的计划步骤在入口返回 422 `gpu_unsupported`，执行层同样拒绝 | `test_gpu_plans_are_rejected_*` |
| 每步一次性容器 | ⚙ | `CommandRunner` 子进程 + 可选命名空间隔离（`runner_isolation=required`） | `test_runner*` |
| 步骤级质量检查 enable_check / max_check_attempts | ✅ | `ExecutionStep.enable_check`；`PlanExecutor._checked` 复核产物（非空、Markdown 卫生、地名规范），不合格则带问题清单重做，仍不合格按 partial 交给重规划 | `test_enable_check_reruns_step_until_artifacts_pass` |
| 执行纪律（超时、禁 pkill -f、障碍预算） | ⚙ | 引擎级超时 / 重试 / 退避上限、token 预算、runner 超时回收 | `test_workflow*`, `test_budget_enforcement` |

备注：参考产品为每步起一个 K8s Pod 并调度 GPU。本项目只调用云端 LLM，交付物生成等本地步骤用「子进程 + 可选命名空间隔离」执行。

## 四、过程可见性

| 参考功能 | 状态 | 实现 | 测试 |
|---|---|---|---|
| 事件流 seq 增量分页 + 逐事件 token | ✅ | `/api/runs/{id}/events?after_seq=`、SSE `Last-Event-ID` | `test_api`, `test_sse*` |
| 人话进度叙述 | ✅ | `workbench/narrative.py`，`/api/runs/{id}/narrative` | `test_narrative_*` |
| 按步骤的产物文件树 | ✅ | `workbench/workspace.py`，`/api/runs/{id}/workspace` | `test_workspace_lists_steps_*` |
| 详情页三栏（时间线 / 实时 / 文件树） | ✅ | `RunPage` 三栏网格 + `StepRail` / `FileTree` | `RunPage.layout.test`, `Workspace.test` |

## 五、交付体系

| 参考功能 | 状态 | 实现 | 测试 |
|---|---|---|---|
| 目录契约（工作区 / 成品区分离） | ✅ | `ArtifactStore` 的 `work/` 与 `output/<slug>/` | `test_artifacts*` |
| 交付登记（path / role / title） | ✅ | `publish.DeliveryBundle.registry()`，`/api/runs/{id}/deliverables` | `test_deliverables_endpoints` |
| md / docx / pdf / html 同源四件套 | ⚙ | 同一块树派生；PDF 由 PyMuPDF 排版 | `test_same_source_formats_agree_on_structure` |
| 自包含 HTML（图片 base64、CSS 内联） | ✅ | `delivery/html.py` | 同上 + 无脚本断言 |
| LaTeX 论文 / BibTeX / 可复现包 | ✅ | `report/latex.py`、`report/bundle.py`（既有） | `test_report*` |
| xlsx 数据交付 | ✅ | 统计结果表 + 既有表格导出 | `test_data_analysis_run_*` |
| 数据图只用记录过的统计 | ✅ | 图由 `analyse()` 从台账数据确定性重绘 | `test_analysis_runs_tests_*` |
| 概念图（框架 / 分类法，与数据图互斥） | ✅ | `workbench/figures.py`：配置 `DR_IMAGE_MODEL` 后走图像模型，一图一调用，生成后检查尺寸与格式；否则或调用失败时由结构描述确定性绘制；写作者从已核验素材整理节点，并插入所有格式 | `test_concept_figure_*`, `test_survey_delivers_concept_figure_*` |

备注：参考产品的 PDF 由品牌 DOCX 经 LibreOffice 转出。本项目改为由同一棵块树直接排版，并在出图后做页数与末段自检。这样可以避免外部转换器的并发锁死和静默截断，也免去 LibreOffice / TeX 这类系统依赖；目标（同源、不截断）保持一致。参考产品的品牌样式基底按约定不予复制。

## 六、质量门

| 参考门 | 状态 | 本项目对应 |
|---|---|---|
| citation_gate（引用真伪） | ✅ | 逐字证据核验 + 语义核验（研究阶段）；`gates.citation_gate`（交付阶段） |
| markdown_gate（裸 HTML / 锚点） | ✅ | `gates.markdown_gate`；解析阶段禁 HTML 直通 |
| length_gate | ✅ | `gates.length_gate` |
| 结构 / 必答章节 | ✅ | `gates.structure_gate`（含同义标题） |
| glyph_check（中文缺字、截断） | ✅ | `delivery/pdf.verify_pdf`：缺字符号与末段哨兵 |
| 跨格式图数比对 | ✅ | `gates.consistency_gate` |
| 幻灯片 fitcheck | ✅ | `delivery/pptx.fit_report` + `gates.slides_gate` |
| statistical_table_qa | ✅ | 数据分析的数字台账复核 `analysis.check_numbers` |
| deliver_gate 汇总 | ✅ | `publish.build_bundle` 汇总各门结论，登记到每个文件 |
| territory_gate（地名规范） | ✅ | `workbench/territory.py`：发布前对定稿做确定性规范化，再用 `gates.territory_gate` 逐格式（md / html / docx / pptx / pdf 可见文本）把关；计划步骤的质量检查也会调用它 |
| style_gate（品牌基底出处） | ⚙ | 不适用：不复制品牌基底，DOCX 统一由 `_base_document` 生成 |
| manuscript_qa 学术文体（成对套话、口语化、半角标点、生产过程叙述、超长句） | ✅ | `workbench/scholarly.check_register` + `gates.scholarly_gate`（`test_register_*`） |
| 摘要禁引用 / 引用堆砌 / 重复来源 / 孤儿与凑数 | ✅ | `scholarly.check_abstract / check_clusters / check_sources`；只统计正文实际引用的来源 |
| 独立综述引用下限（参考默认 100） | ⚙ | 默认 20，可在「设置 → 交付质量」调整；不足时继续补洞，仍不足如实报「部分完成」 |
| 时效覆盖（点名年份必须有该年份文献） | ✅ | `scholarly.check_recency` + `coverage.coverage_gaps` 驱动补洞 |
| 检测即返工（remediation loop） | ✅ | `workbench/revision.write_with_revisions`：问题清单交回写作者重写，取最佳版本（`test_writer_revises_draft_that_fails_quality_checks`） |

## 七、账户与计量

| 参考功能 | 状态 | 实现 | 测试 |
|---|---|---|---|
| 每事件 / 每运行 token 计量 | ✅ | Tracer 累计、`RunSummary.total_tokens` | `test_budget_enforcement` |
| 额度（配额） | ✅ | `DAILY_RUN_QUOTA` / `DAILY_TOKEN_QUOTA`，`/api/usage`，前端显示用量 | `test_daily_quota_*` |
| 积分定价 | ⚙ | 不做积分计费；用量只按运行次数与 token 额度统计（`/api/usage`） | — |
| 订阅计费 | ⚙ | 自部署工作台没有收费主体，不做订阅结算；额度由运行次数与 token 额度承担 | — |
| 登录鉴权 | ✅ | API Key / 多身份角色（admin / researcher / reader） | `test_access_control*` |
