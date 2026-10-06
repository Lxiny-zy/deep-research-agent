# 文档版本与渲染回执

本次变更仅在本地升级工作树实现，未部署。

- `GET /api/runs/{run_id}/document` 的 `content_version` 是规范化 ReportDocument SHA-256，
  计算时排除该字段自身；`source_version` 对应交付登记的 `input_version`，用于检查同源。
  响应同时提供 `X-Content-Version`。`include_hsi_tables` 是明确的投影选择。
- 所有 `document.*` 下载接受 `?version=<content_version>`。当前投影不同则返回
  `409 / document_version_changed`，不会把新版文件交给固定旧版的页面。
  文档入口不充当历史仓库；已完成导出的回执可以下载其冻结的历史文件。
- `GET /deliverables?version=<delivery_version>` 返回指定不可变版本；`items[].available=false`
  表示文件缺失或校验失败，并增加对应的可修复格式。单文件下载校验版本登记、快照、路径和该文件；
  `GET /deliverables.zip?version=...` 校验全部文件，包内 manifest 记录文件哈希。
  修复生成新版本，原文件和旧版本登记保持原样。

`POST /api/runs/{run_id}/render-operations` 返回 202 回执，参数如下：

```json
{"kind":"export","format":"md","version":"<document hash>","request_id":"stable-client-id"}
```

`kind` 可为 `bundle`、`retry`、`export`；后两者必须提供 `version` 与 `format`。
可选导出字段为 `include_hsi_tables`、`table_id`、`profile`、`template`。
读者只能提交自己有权读取任务的 `export`，生成与修复仍需研究权限。

回执提供 `operation_id`（亦为 `id`）、`request_id`、`status`、`content_version`、
`input_version`、`status_url`、`result_url` 和精简错误。状态为 `pending`、`running`、
`done`、`error`、`cancelled`。客户端在发请求前保存 request_id；首响应丢失时，使用
`GET /render-operations?request_id=...` 找回操作。按 ID 查询应使用回执中的完整 status_url，
它包含对应的请求别名；多个标签页可以共享同一底层渲染任务。

同 request_id 不同参数返回 409；同内容不同 request_id 共用规范队列任务。
回执别名采用受路径约束、受存储配额约束的原子控制文件，实际执行状态沿用现有持久 RenderJob。
队列仍受已有 admission 上限约束。进程重启后的 PostgreSQL/SQLite任务仍可查询；内存仓库不具备跨进程持久性。

导出完成后 `result_url` 返回冻结文件并校验文件 SHA-256，响应带 `X-Content-Version` 与
`X-Content-SHA256`；生成/修复的 result_url 返回指定版本的交付登记。
Markdown 文件自身包含文档版本、核验范围和格式缺失范围，ZIP manifest 与校验清单同样记录版本与哈希。

验证入口：`tests/test_delivery_contract_upgrade.py`（文件损坏/缺失、登记篡改、整包完整性、
跨标签版本变化、历史回执导出、不同请求复用、SQLite 重启、权限隔离），配合现有
`test_delivery_persistence.py`、`test_delivery_retry_api.py`、`test_delivery_input_snapshot.py`、
`test_access_control.py` 和 `test_api.py` 导出测试。
