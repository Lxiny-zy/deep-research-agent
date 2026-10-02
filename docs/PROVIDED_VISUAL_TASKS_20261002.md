# 幻灯片、导图的指定材料模式与场景验收

日期：2026-10-02。父版本 `1e3dc5c`，本批仅在本地开发与验证，未推送、未部署。

## 用户可见行为

幻灯片、思维导图新增“仅指定材料”策略。用户可上传多份文件、填写指定链接，直接整理这些内容；原有快速/深度检索仍用于开放主题任务。

- 前后端冻结 `strategy=none` 与对应的 `slides_provided` / `mindmap_provided` 工作流；不经过规划检索步骤。
- 上传文件由附件读取阶段处理一次；论文导入阶段复用已取得的附件证据，并读取另外指定的链接。
- 材料缺失时，在创建任务前提示补充；长任务说明不能冒充论文原文。提交这种策略时，不携带此前选择的资料库项目；切回开放检索后可以恢复该选择。
- 每份指定文件/链接都须有可用证据，并在交付正文中体现其引用。部分导入失败、材料截断或静默遗漏会阻止 PPTX/导图等正式格式输出；保留诊断信息，不用其他材料替代。
- 格式版本升为 32，以区分新增的材料覆盖检查。

## 同行评审的任务标准

MST++ 首次真实评审已经生成两版草稿并进入核验。原固定代码会要求“顶会审稿人口吻”，导致用户没有指定投稿场景时仍按顶会录用门槛给出拒稿建议；草稿还出现把未知写成缺陷、将复用已有模块直接归为不足、重复大量数据表等现象。

本地已调整后续评审提示词：使用用户指定的目的和评价维度；未指定投稿场景时说明本次评分依据，不自行假定顶会门槛；区分已证实缺陷、需澄清的问题和验证建议，未核实内容不能作为确定扣分事实；意见须说明依据、影响及可执行修改。减少无助于评价的表格复述。

这只是已实施的调整，尚未用新提示词完成真实复验。原固定代码任务继续运行，其终态会原样保留，不能用新提示词的预期覆盖原结果。

## 当前真实任务与复用记录

| 场景 | 当前进展 | 保存位置 |
| --- | --- | --- |
| 同行评审 | MST++ 全文首次真实任务；两版草稿，核验/修订中 | `artifacts/sc31/peerReview/`；日志 `artifacts/sc31-engine.log` |
| 幻灯片 | MST++ 全文，12 分钟研究生组会、8–10 页正文；新指定材料模式已启动 | `artifacts/sc33/`；固定代码 `artifacts/sc33-code/`；日志 `artifacts/sc33.log` |
| 自动研究 | arXiv 与 OpenAlex 实际检索各返回 3 条来源；完整模型任务准备好，尚未启动 | `artifacts/search-preflight-sc34.json`、`artifacts/sc34/` 与 `artifacts/sc34-code/` |
| 多输入导图 | 新模式的完整离线链路已验证，真实多输入任务尚待启动 | 回归 `test_closed_visual_workflow_reads_each_file_once_and_requires_every_input` |

检索预检不等同于自动研究成功。新增 `scripts/accept_delivery_live.py --live-search` 使用实际 arXiv/OpenAlex 后端及配置的全文展开，逐次保存检索问题、返回来源、耗时和失败类型；模型响应与最终冻结状态仍分别保存。默认固定来源模式不变，两类结果在运行元数据中明确区分。

自动研究已准备的命令（确认未启动后执行；不能因轮询没有输出而重启）：

```powershell
& C:/Users/Administrator/AppData/Local/Programs/Python/Python311/python.exe -X utf8 -u artifacts/sc34-code/scripts/accept_delivery_live.py --authorized-ssh Aliyun --templates autoResearch --strategy quick --live-search --query-file artifacts/sc34/query.txt --output artifacts/sc34
```

本地真实模型沿用用户授权的阿里云模型配置，只读获取，凭据不落盘。真实执行使用已有 Python 3.11 环境；`.venv` 与 `.venv-desktop` 不含完整验证依赖，不能用它们启动这些验收。

## 验证范围

完整离线流程确认：两个输入各抽取一次、没有开放检索、生成 PPTX 或导图文件；追加一份未取得证据的必需材料后，正式输出被阻止。API 检查输入拒绝、工作流与材料冻结；前端检查策略提交、资料库解绑与切回恢复。

前端 **389 项通过**；构建、ESLint、OpenAPI 类型一致性和体积门槛通过。后端全量 **2,035 项通过、16 项 PostgreSQL 专用用例未运行，覆盖率 89.35%**，日志 `artifacts/regression-sc33.log`；评审提示词调整后另补跑两项评审链路回归通过，不作为真实质量验收。Ruff 静态/格式和 Mypy 通过。浏览器验收由用户手动完成，本批没有自动浏览器测试。
