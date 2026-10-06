# 渲染执行与关闭期限验证（本地）

本改动仅位于 `upgrade/reliability-20261006` 工作树，未发布。Windows 组合回归
97 项通过；本地 Linux 临时容器的真实进程与关闭回归 23 项通过。原始记录见
[Windows 日志](validation/render-reliability-20261006.txt) 和
[Linux 日志](validation/render-reliability-linux-20261006.txt)。

## 执行边界

`RenderService` 每次领取持久化任务后启动独立 Python 子进程。子进程直接调用已有
`render_tasks.execute_render`，保留输入 fingerprint、逐格式检查点、SHA-256、质量门、
不可变版本、共享物理槽和每个 run 的文件锁。没有新增一套交付物存储或重试协议。

Windows 子进程使用隐藏窗口，并在接收任务之前加入启用 `KILL_ON_JOB_CLOSE` 的
Job Object；Linux 使用独立进程组，取消时杀死整个进程组，父进程死亡时主渲染进程由
`PDEATHSIG` 终止。原生库阻塞和持续占 CPU 的死循环都不能靠租约续租无限占槽。

子进程仅接收 JSON 数据，不接收 Python 闭包、pickle 或数据库连接。每次既有发布检查
通过私有管道询问父进程，由父进程核对实际租约和运行状态。随机 nonce、递增序号、
单行 64 KiB 上限和每任务最多 10000 次授权限制使协议失败时关闭执行边界。父进程
管道断开不会授权继续发布；诊断输出与协议分离，非协议输出也会终止该次执行。

`render_execution_timeout_seconds` 默认 600 秒，限制整个子进程执行尝试；
`render_progress_timeout_seconds` 默认 180 秒，限制没有新增持久化格式检查点的时间；
`render_progress_poll_seconds` 默认 1 秒。进度变化按原有 `progress_token` 判断，
CPU 活跃或租约心跳不算交付进展。合法但超过 180 秒的单格式生成同样会触发停止，
可按具体运行环境调整进展期限；总执行期限始终独立生效。超时后的失败仍进入既有的
连续三次无进展上限，已完成的格式可以从检查点恢复。

## 关闭期限

Worker 从首次停止请求起共享一个单调时钟期限：
`worker_shutdown_grace_seconds + 5 秒清理预算`。领取循环、运行任务、注销心跳、
渲染 dispatcher、渲染服务关闭和 engine dispose 均受这一总期限约束。
专用 worker CLI 超出期限调用既有的强制退出分支，未完成任务保留租约恢复语义。

API 在开始关闭时也建立一个共享期限，先发起所有任务的停止，再等待清理。
某个不响应取消的 dispatcher 不会延迟发起 QA/渲染停止。期限结束后记录
`shutdown_incomplete` 和需要 supervisor 终止的日志，不通过最终无界 gather 卡住
lifespan。API 库本身不调用 `os._exit`；其他不合作的 Python 清理任务仍依靠部署
supervisor 的最终进程终止期限。原生渲染子进程在服务关闭时主动终止。

## 可复现检查

Windows 本地使用短临时目录，避免测试名称、run UUID 与 artifact hash 组合超过
Windows 路径长度。命令在升级工作树执行：

```powershell
python -m pytest -q --basetemp=D:/tmp/render-reliability tests/test_render_process.py tests/test_shutdown_deadline.py tests/test_render_restart.py tests/test_render_progress.py tests/test_render_capacity.py tests/test_render_service.py tests/test_worker.py tests/test_worker_admission_cleanup.py tests/test_worker_embedded.py tests/test_worker_mode_api.py tests/test_rendering_isolation.py tests/test_legacy_rendering_isolation.py
python -m ruff check deep_research/render_process.py deep_research/render_service.py deep_research/shutdown.py deep_research/worker.py deep_research/api.py tests/render_helpers.py tests/render_subprocess_fixture.py tests/worker_shutdown_subprocess.py tests/test_render_process.py tests/test_shutdown_deadline.py
python -m mypy deep_research/render_process.py deep_research/render_service.py deep_research/shutdown.py deep_research/worker.py
```

新进程测试实际启动子进程，覆盖原生阻塞、CPU 死循环、异常退出、子孙进程终止、
总执行期限、格式进展期限、三次无进展终态、取消和正常完成竞争、关闭自身被取消、
stdout 杂音/洪流/错误 nonce、物理槽释放及未发布文件不可列出。超时后继续的实际
bundle 验证 HTML/DOCX 各只渲染一次，PDF 在新尝试中继续，首次残缺 bundle 无当前版本。

关闭测试在真实 worker CLI 子进程分别注入 dispatcher、render close、engine dispose、
运行任务和心跳注销不响应取消，验证进程在外部 10 秒硬上限内自行进入退出码 0 的
既有强制退出路径。另有 API 多阶段共用预算及重复取消测试。

原有需要在父进程 monkeypatch 渲染器的单元测试显式使用 `cooperative_render` 测试
夹具；上述进程测试和普通真实导出不使用此夹具。Linux 验证使用本地已有的
`deep-research-agent:release-9723575a0662` 镜像，只读挂载当前升级源码及纯 Python
测试依赖，设置 `APP_ENV=test`、`--network none`、`--init`，运行后删除临时容器。
未修改镜像或现有服务。实际覆盖 Linux PDF 继续渲染、进程组终止及 CLI 关闭。

问答停止流程另补齐了同类等待边界：模型执行、流式事件最终写入和完成事务共享单轮
截止时间，停止事件能中断等待；停止后立即停止续租，并在 5 秒清理预算内保存中断
状态。不响应取消的子任务仍登记在 `app.state.qa_tasks`，API 关闭会再次扫描它们。
数据库完全不可用时不假装已经保存终态，而是返回待确认状态并让租约自然失效；
不会重新发起该模型请求。`tests/test_qa_deadline.py` 使用实际拒绝取消的异步任务和
Memory/SQLite 两套持久化实现，验证单轮停止、事件写入卡住和完成事务卡住。

渲染失败日志按 job ID、attempt 和父/子执行位置关联，保存最多 4 层异常类型及每层
24 个文件 basename、行号、函数名。不会记录原始异常文本、局部变量、源码行或
完整路径，也不会把这些诊断加入公开的 job error。`tests/test_render_process.py`
分别对真实子进程与父进程注入含秘密文本的异常，验证日志保留定位帧而不泄漏文本。
