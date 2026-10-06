# 可靠性故障场景矩阵（本地）

历史证据：以下部署状态指记录产生时的阶段，不代表当前线上状态；最新版本见 [发布记录](../RELEASE.md)。

本表记录本地取得证据的交付、展示、队列和进程故障场景。本阶段未部署；
全量集成结果另见本目录 `backend-upgrade*.xml`，第一次失败与最终重跑须分开阅读。

| 要求 | 故障/边界 | 实际行为 | 可重复证据 | 边界 |
| --- | --- | --- | --- | --- |
| D1 | 旧页面导出时另一标签页保存新版正文 | 所有 legacy `document`/`document.*` 入口固定旧版本时返回 409；已提交旧版导出仍从持久回执取得原字节 | `test_export_receipt_pins_old_version_after_new_tab_changes_report` | 历史 `/document?version=` 不回溯装配；历史导出通过 receipt.result_url 下载 |
| D1 | 文档装配与导出元数据读取之间发生修改 | 对比 ReportDocument.source_version 和所使用 RunDetail 的交付输入指纹，拒绝混合两次快照 | `test_export_rejects_mixed_run_snapshot_during_assembly` | 不把不同 include_hsi_tables 投影视为同一内容版本 |
| D1 | 序列化/格式转换及自身版本字段参与 hash | 内容 hash 排除 content_version 自身；JSON、Markdown、PDF 使用的 HTML 和 LaTeX 源携带相同文档身份；HTTP 结果另有文件字节 hash | `test_document_identity_excludes_stamp_and_offline_bundle_records_identity`；`test_report_pdf.py`、`test_report_latex.py` | PDF/TeX 可选运行时缺失仍返回原有 501；模板输出变化与内容身份分别由文件 hash 和内容版本表示 |
| D2 | 已登记 HTML 文件缺失/被改坏 | 同版本 Markdown 保持可读，坏文件 409，登记标不可用；修复产生新版本，旧坏文件和旧登记保留；同请求重放不重做 | `test_one_damaged_file_keeps_intact_files_and_repairs_to_new_version`（missing/corrupt）；`test_build_and_repair_receipts_publish_immutable_versions` | 只允许修复指定格式，其他保留文件仍须校验；科研质量门照常执行 |
| D2 | 整包里单文件损坏 | 正常 ZIP 各项符合 manifest SHA-256；任一登记文件损坏后整包 409，不能通过部分下载接口生成不完整 ZIP | `test_http_corruption_isolated_but_complete_zip_stays_strict` | 原不可变版本不原地修复 |
| D2 | 快照、路径、登记 hash、版本 ID 篡改 | 定向单文件读取同样拒绝 | `test_selected_download_still_rejects_registry_tampering`（4 类） | 新登记有整体校验和；旧登记仍使用已有快照/路径/文件校验 |
| D3 | 响应丢失、刷新、SQLite 仓库实例重启 | request_id 别名与队列记录可找回同一操作，重放继续原任务；已完成导出固定原内容 | `test_receipt_and_alias_survive_sql_repository_restart` | 内存仓库不声称跨进程持久；PostgreSQL 使用既有 SqlRenderQueue，另由队列 pg 套件覆盖 |
| D3 | 重启后导出文件或回执别名损坏 | 导出拒绝；别名校验失败返回 409，不创建替代操作 | `test_receipt_and_alias_survive_sql_repository_restart` | 文件完整性失败需显式处理，不能自动覆盖原版本 |
| D3 | 同内容不同标签请求、队列满 | 不同 request_id 的相同内容复用一个 job；不同格式的新工作在队列已满时 503 | `test_duplicate_content_reuses_queue_slot_while_distinct_work_is_bounded` | 回执控制文件还受现有存储配额约束 |
| D3 | 读者或其他租户访问回执 | 读者仅可导出自己的任务，bundle/retry 403；其他属主查询状态/别名 404 | `test_render_receipt_enforces_owner_and_reader_kind_permissions`；`test_access_control.py` | 完成任务载荷也校验 payload_hash 与 run_id；不返回内部路径和原始 payload |
| S1 | 真实单元 p-8d500939cf20f72e2733-0 的保守拒绝 | 原文与所选短摘录覆盖范围分开记录；未自动把完整原文当作现有引文的支持；保留机械拒绝中的原选 IDs 与未锚定 IDs | [历史科学核验记录](https://github.com/Lxiny-zy/deep-research-agent/blob/88fbc72b5ff5de2b13a148747e2c6a793373b3eb/docs/SCIENTIFIC_SUPPORT_CALIBRATION_20261006.md)；`test_real_candidate_rejection_preserves_specific_selected_excerpts` | 旧版本未保存确切原选 IDs，重放选择明确标为候选；生产历史 fallback 未修改 |
| S1 | 局部修订只收到泛化错误，无法修正复合句引用 | 修订器收到具体引用/数值/专名诊断，按现有短摘录在原段内拆句或收窄；修改后重新过原门 | `test_repair_receives_binding_diagnostics_then_rechecks_accurate_revision` | 不放宽门限，不自动扩展/编造摘录，不删除必要条件规避检查 |
| S1 | 数值/专名/条件/因果/多来源正负案例 | 12 案例机械判定符合标签；条件替换、相关转因果、假设转事实明确要求独立语义判定 | `python -m eval.scientific_support_cases`；`test_scientific_support_calibration.py` | 离线语义标签用于检查流程，不当作真实模型正确率；本阶段未重新发起付费模型测量 |
| U1/U2 | 窄屏宽表、多系列图、数学和引用交互 | 五类 SVG 图形、数学排版与可访问源表；键盘/触摸可横向查看末组数据；引用进入既有证据面板并恢复焦点 | [历史前端证据](https://github.com/Lxiny-zy/deep-research-agent/blob/88fbc72b5ff5de2b13a148747e2c6a793373b3eb/docs/RELIABILITY_FRONTEND_EVIDENCE_20261006.md)；`frontend/scripts/verify-scientific-ui.js`；三尺寸截图 | 使用明确标注的合成科学数据验证真实组件，未声称所有论文表格自动解析正确 |
| D3/U3 | 导出响应丢失后刷新、旧内容版本变化、离线副本 | 按同一 request_id 查询回执并验证文件 hash；版本冲突不偷换内容；离线 Markdown 自带版本和降级说明 | `frontend/scripts/verify-delivery-reconnect.js`；`artifacts/reliability-ui/fixed-version.md`、`offline-copy.md` | 浏览器网络故障由受控 HTTP fixture 注入；后端真实文件/队列测试独立覆盖 |
| P1 | sleep/CPU 卡死、异常退出、取消/完成竞争 | 子进程被终止，物理槽归还，完整检查点保留，未完成文件不能发布 | `test_render_process.py`；Windows 97 项及 Linux 23 项日志 | Linux 使用本地断网、只读源码临时容器；不修改生产服务 |
| P2 | dispatcher/worker/数据库清理不响应取消；取消过程中又登记任务 | 全部关闭阶段共用绝对期限；反复收拢新登记任务；worker CLI 明确退出；API 保留监管终止边界 | `test_shutdown_deadline.py`、`test_api.py::test_lifespan_stops_recovery_before_snapshotting_workers`；`lifecycle-regression-20261006.txt` | 任意不响应取消的 Python 扩展不能靠 asyncio 强制结束，API 最终进程终止由外部 supervisor 负责 |
| Q1 | 大量旧积压、多实例同时接单/领取、用户占满容量 | 只恢复可领取会话头部；全局/用户额度共用数据库锁；满队列仍允许原请求幂等读取 | `test_qa_queue_fairness.py`、`test_qa_admission.py`、`test_qa_reliability_pg.py` | PostgreSQL 在本地专用临时库验证，不连接生产 |
| Q1 | 等待锁或连接池期间租约到期 | 获取行后按当前时间判断；旧执行者不能复活或与新会话超额并发 | `test_qa_clock_fencing.py`；`qa-clock-fencing-20261006.txt` | 显式注入测试时钟例外仅用于确定性测试 |
| Q2 | 排队/运行中停止、完成竞争、跨实例停止、刷新重连 | 保留历史，状态先持久化；旧执行者写入被拒绝；重复观察不重发模型 | `test_qa_stop_recovery.py`、`test_qa_recovery_api.py`；双页停止/恢复浏览器证据 | 不承诺追回已由上游计费的请求 |
| Q2 | 证据/草稿/核验阶段崩溃，配置/来源/历史变化 | 显式新请求复用有效阶段；完整审计、修订次数和引用编号保留；不匹配时拒绝恢复 | `test_qa_stop_recovery.py`、`test_qa_recovery_api.py` | 没有完整阶段或快照超过 16 MiB 时不承诺恢复；纯知识问答没有证据阶段 |
| Q2 | 模型忽略取消、写事件/完成事务卡住 | 执行和保存共用期限，停止续租，额外清理预算有界；残留任务交 API 关闭监管 | `test_qa_deadline.py`；`qa-deadline-20261006.txt` | 数据库不可响应时明确状态未确认，不把超时伪装为已完成 |
| D2/P2 | 文件已提交、完成状态数据库写失败；随后损坏一个文件 | 完整登记读取可恢复完成状态，损坏登记仍保持 needs_review；好文件仍可下载，不触发重复渲染 | `test_completion_retry_http.py` 两参数场景 | 必须全部文件完整才能恢复完成状态 |
| P1/V1 | 首轮全量出现一条未保存原始位置的 RuntimeError | 父/子进程现在保存异常链类型和代码位置，公开错误不泄漏诊断；后续 10 次真实子进程复验的 20 个任务均一次完成 | `render-diagnostics-20261006.txt`、`library-render-stress-20261006.md` | 原始记录已丢失细节，根因未确认；独立压力复验不能替代完整顺序重跑，不能据此宣称找到了原始原因 |

验证记录：D 契约新增 14 项及报告 PDF/LaTeX 回归合计 35 项通过；科研新增案例与现有
support_alignment/prose_edit 合计 75 项通过。既有 delivery_persistence/report_document/report_latex
63 项、delivery_retry_api/delivery_input_snapshot/access_control 54 项、文档 API 导出 23 项也已通过。
测试在短路径 `D:/tmp/dr-*` 运行以避免 Windows 临时文件路径长度限制。
