# N0 人工验收定位：后端工程记录

分支 `improvement/scenario-quality-20261006`。本次只做本地工程实现与受控验证，未修改主计划、既有评测基线、
正文写作或正式质量门，未发起付费模型调用，未部署。

## 实际接线

`acceptance_api.py` 已挂入 FastAPI，沿用运行归属和写入权限。前缀为
`/api/runs/{run_id}/acceptance`，提供 template、context、locations、evidence、records、package。
填写与完整用法见 [人工验收记录模板](N0_ACCEPTANCE_RECORD_TEMPLATE.md)。

`acceptance_context.py` 复用已有冻结要求、coverage_review、prose_units、ReportDocument 与 EvidenceRecord。
陈旧覆盖记录不映射新稿，同 URL 不同内容快照不混用，图形只关联实际使用的表列。
正文位置、来源 hash、页段 locator 和现有核验状态均有实际数据来源；不另造质量评分。

`acceptance_store.py` 在现有运行目录的 `.framework/acceptance/records` 以跨进程锁和原子写入保存不可变记录，
沿用存储配额，另有每任务 500 条上限。相同请求幂等，不同参数冲突。恢复创建子记录，原记录不被覆盖。
`initial_status` 是首次登记的观察值；`first_attempt_status` 单独表示人工报告的首次实际结果，默认 unknown。

问题包只保存必要标识、指针、人工观察和主动选择的片段，不默认收集完整正文、私人来源全文、全对话、模型思考或配置。
选择的正文和原文都核对为目标版本中的真实子串，选择文本中的凭据形态及当前认证值被遮盖。

## 验证与边界

`test_acceptance_api.py` 15 项覆盖实际模板/位置/保存/下载、并发幂等、旧版重放、首次/恢复隔离、范围与片段校验、
额外整对话字段拒绝、SQLite 仓库实例重启、记录损坏、跨用户隔离、凭据遮盖、覆盖记录失效、结构化位置和来源版本。
与既有 requested_content、report_document、access_control 合计 102 项通过。
日志：[文本](validation/n0-backend-20261006.txt)、[JUnit](validation/n0-backend-20261006.xml)。新增四模块通过 mypy，修改范围通过 Ruff。

业务页面入口由前端后续接入；API 已实际可调用。新输入的科研质量与视觉体验仍待用户人工验收。
不保存整篇历史文档；旧记录保留当时所选片段和定位，不跳转新稿解释旧问题。
`package_code_sha256` 是安装的 Python 包源码指纹，不代表前端构建；未设置 `DR_APP_REVISION` 时 revision 为 null。
任意私人对话和外部文件路径不能被塞入问题包，后续问答包需另按会话归属和选择授权接入。
