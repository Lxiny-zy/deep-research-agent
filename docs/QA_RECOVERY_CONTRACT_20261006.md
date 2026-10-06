# 问答容量、停止与显式恢复

本地升级分支实现，尚未发布；生产数据库与运行服务保持现状。无数据库迁移，
阶段快照保存在已有 `qa_message.request_payload` 的私有 `_checkpoint` 字段。
公开 API 不返回快照、原始模型配置、密钥或执行租约。

## 接单和领取

`QA_MAX_ACTIVE=4`、`QA_MAX_ACTIVE_PER_OWNER=2`、`QA_MAX_PENDING=64`、
`QA_MAX_PENDING_PER_OWNER=16` 均为正整数。SQL 实现在共享数据库事务锁内检查和登记，
上限不会乘以 API 副本数；按会话归属统计用户额度，管理员代操作不绕过会话用户额度。
满队列对新请求返回 `429 qa_queue_full` 和 `Retry-After: 5`；同一请求的幂等读取仍有效。

恢复扫描先过滤会话中更早的排队/有效运行请求和已达到运行上限的用户，再取最早 200 个会话头。
已过期运行请求不会被重新执行；领取下一轮时将旧轮标记中断并清除写权限。
排队等待不消耗模型执行期限。

## 停止与恢复 API

`POST /api/qa/conversations/{cid}/requests/{rid}/cancel` 返回这轮消息。
有效状态从 pending/running 转为 cancelled，保留历史、已落盘事件和阶段结果；
已完成回答保持原状态。取消与完成共用会话锁，旧执行者不能覆盖胜出的终态。
其他 API 实例通过持久状态观察停止；浏览器断线仍仅断开观察，不等于停止。

用户显式继续时使用原消息的 query/sources/project_id/revision_message_id，向原提问接口
提交新的 request_id 和 `resume_message_id`。相同新 request_id 的重复提交只创建一轮。
原问题继续保留为中断/停止状态，不修改旧对话。

每条消息包含 `recovery.available`；可恢复时带 `stage`：

| 阶段 | 复用范围 | 仍需执行 |
| --- | --- | --- |
| evidence | 已抽取、核验的发现及原文材料 | 作答与正文核验 |
| draft | 完整草稿、引用编号、证据和冻结上下文 | 正文核验及允许的局部修订 |
| reviewed | 已绑定的核验结果、最佳稿和已消耗修订次数 | 未完成的有限修订/最终持久化 |

没有完整阶段的取消、纯模型知识问答和超过 16 MiB 的阶段材料不承诺恢复。
未完整返回的模型响应不会成为阶段；错误/取消不会自动重发未知的付费请求。
用户主动继续是一次新的、可产生费用的请求，仍受单次模型调用上限约束。
已开始局部修订前先保存修订次数，恢复不会重置自动修订额度。

继续前和实际执行前均校验原问题、来源选项、任务材料、资料库来源版本、历史对话、身份、
当前配置、角色广场模型/提示词、全局规则与核验规则指纹。还比较实际解析后的执行上下文，
防止接单与运行之间的模型配置变化。接单时发生变化或快照校验失败返回 `409 qa_recovery_unavailable`，
已接单后才发现执行上下文变化时，保存该轮错误并返回失败，模型请求不会继续。
不默默改用新材料。网页引用复用已保存的原文快照，不声称网页现在仍未变化。

## 当前验证证据

- `tests/test_qa_queue_fairness.py`：两个会话合计 398 条旧积压不遮挡独立会话；过期前驱不重发。
- `tests/test_qa_admission.py`：多个管理器同时 reserve/claim、全局/用户上限、满队列幂等读取。
- `tests/test_qa_stop_recovery.py`：取消/完成竞争、跨实例停止、落盘阶段复用与篡改拒绝。
- `tests/test_qa_recovery_api.py`：真实 ASGI 权限、重复继续、旧请求重连不重做、配置/来源/历史失效。
- `tests/test_qa_reliability_pg.py`：独立 PostgreSQL 连接池复用同一组队列/停止不变量。
  本地隔离 PostgreSQL 16 容器运行 7 项通过，结果 `validation/qa-reliability-pg.xml`。

`tests/test_qa_deadline.py` 覆盖不响应取消的模型任务、写流记录卡死、完成写入期间停止等情况。
工作、流记录落盘和完成事务共用一个执行期限；停止时立即停止续租，并在额外 5 秒清理预算内
保存终态。仍未退出的协程继续登记在应用任务集合中，由 API 总关闭期限与进程监管处理。
若数据库也无法及时响应，则明确返回状态尚未确认，等待租约过期后检查，不伪造完成。

`tests/test_qa_clock_fencing.py` 在 SQLite/PostgreSQL 的真实事务锁、连接池等待跨过租约期限后，
验证旧执行者不能续租或写完成。生产时钟在取得锁和读取行后采样；注入测试时钟仍保留确定性。
独立运行 8 项通过，日志 `validation/qa-clock-fencing-20261006.txt`。

完整前端双页停止、继续及刷新证据见 [前端验收](RELIABILITY_FRONTEND_EVIDENCE_20261006.md)。
整体验收状态以 [升级计划](RELIABILITY_UPGRADE_PLAN_20261006.md) 和最终全量结果为准。
