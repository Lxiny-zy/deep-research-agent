# 运行、权限与恢复

本指南对应数据库迁移 `0027`。升级前先取得一致备份；不需要连接实验室服务器。

## 部署能力

| 能力 | 源码 + requirements.lock | 隔离安装 wheel | 基础 Docker 镜像 |
| --- | --- | --- | --- |
| inline / 独立 worker | 支持，两者共用数据库配置、租约和事件 | 支持 | 支持，worker 需启用 profile |
| React 页面与公共图像 | 先构建 frontend/dist | 构建 wheel 前须先构建前端 | 多阶段构建自动打包 |
| Markdown / CSV / XLSX | 支持；openpyxl 已是默认运行依赖 | 支持 | 支持 |
| 报告 PDF | 安装 PDF 锁文件及系统 Pango/字体 | 安装 pdf extra 及系统库 | 已安装 WeasyPrint、Pango 与中文字体 |
| 外部归档/文档命令 | Linux 沙箱和相应工具可用后启用 | 同左 | 默认未安装，拒绝执行 |
| PDF 全文解析 | 可选 PyMuPDF，缺失时按解析器能力降级 | fulltext extra | 以实际镜像安装情况为准 |

`GET /api/capabilities` 返回当前导出能力，前端据此展示可用操作。报告 PDF 渲染和
`pdf.convert`（LibreOffice 文件转换）是两条不同执行路径。

## 权限与配置

`API_KEY` 对应兼容管理员身份。`DR_API_KEYS` 接受 JSON 数组，例子见 `.env.example`：
每项包含稳定 `id`、角色 `role` 和至少 16 字符随机 `key`，仅使用合成占位符制作配置模板。
`admin`、`local` 是保留 ID。修改身份列表或轮换登录密钥后，统一重启全部 API 副本。

| 角色 | 研究记录 | 共享配置与目录 |
| --- | --- | --- |
| admin | 查看、创建及管理全部记录 | 管理 |
| researcher | 创建并管理自己拥有的记录 | 读取可见的共享配置，不可修改 |
| reader | 只读自己拥有的记录 | 只读，不可修改 |

未归属的旧记录仅管理员可见。只读账号不会自动获得别人的报告；保持 ID，将现有研究员
降为 reader 后，可以继续读取自己的历史。当前没有组织租户、账号注册、报告授权分享或
完整登录审计系统。浏览器身份切换会清理查询、草稿和待定提交，后端仍逐条验证归属。

SQL 部署的在线设置以数据库不可变版本为准。密钥由 `CATALOG_ENCRYPTION_KEY` 加密，
所有 API/worker 使用同一数据库及同一解密 key。不要直接用新的 key 替换旧 key 来“轮换”
加密：须先用旧 key 解密，再经过显式的重加密迁移。环境变量提供基础默认；已保存的在线
配置优先级更高。旧 `runtime_config.json` 仅保留导入和兼容用途。

## 容量、流式与恢复规则

`MAX_ACTIVE_RUNS`、`MAX_QUEUED_RUNS` 在同一数据库中协调，增加 API 或 worker 副本不
会放大队列容量。满载时新提交返回 503；相同身份、同一逻辑提交的幂等重试仍返回原 run。
客户端保留待定提交 key，用于响应丢失后的恢复。

`DR_PROVIDER_MAX_CONCURRENCY`、`DR_PROVIDER_REQUESTS_PER_MINUTE` 对同一供应商凭据
共享调用租约、滚动窗口请求计数和冷却。LLM 与主要付费检索适配器接入此边界；第三方工具
扩展须显式接入协调器。按凭据分组不能推断供应商的账号级、组织级或跨端点的总账单限额。

LLM 和模型检索在请求前预留输入估计与输出预算，成功后按 usage 结算；无 usage 或无法
确定是否已计费的失败保守计量。`LLM_MAX_INPUT_CHARS` 限制输入，`LLM_MAX_OUTPUT_TOKENS`
限制单次输出。供应商隐藏 token、检索费用、外部重试与计价差异不属于硬账单保证。

合并后的 token 文本、累计用量和稳定 seq 入库；API 可回放 worker 产生的事件。
`/healthz` 仅表示 API 活着，`/readyz` 还检查数据库及 worker 模式下的有效心跳。
`python -m deep_research.worker --check` 检查本 worker 的心跳，不能靠它替代 API 探针。
管理员的 `/metrics` 包含数据库汇总状态；部署监控应观察队列等待、活跃租约及 worker 数。

`cancelled` 和 `done` 是终态；`/resume` 返回 409。故障状态修复后可恢复，执行 attempt
增加但不重置原始总截止时间。崩溃任务由租约过期后的执行者接管；超过领取上限会熔断。
若配置中的全局模型端点已经变化，应创建新研究，旧 run 不会带着旧设置转发到新端点。

## 文件与命令

API run 使用 `DR_ARTIFACT_ROOT/runs/<run_id>`；CLI 使用独立 ID，恢复沿用已有 ID。
`DR_ARTIFACT_MAX_BYTES` 限制单个受管文件，`DR_ARTIFACT_TOTAL_BYTES` 限制根目录总量。
配额使用文件锁和可恢复计量文件；正常写入差量更新，异常中断或过期会重新扫描。多机共享
文件系统必须提供可靠的文件锁与一致挂载；否则应使用支持条件写入的对象存储适配器。
数据库、日志、临时目录及未登记的外部工具临时输出另需磁盘/容器限制。

删除记录时在同一数据库事务中安排清理，API 后台重试删除失败的产物。不会清理仍被引用
的旧共享目录。可用只读巡检列出疑似孤儿：

```bash
python -m deep_research.maintenance --artifact-root ./artifacts --grace-days 7
```

此命令不删除业务数据，不创建或迁移数据库。SQLite 只读访问仍可能生成 WAL 协调元数据。
未引用目录也可能是 CLI 产物或备份，须结合归属人工判断，不能直接把列表当删除清单。

命令默认要求 Linux bubblewrap + prlimit，系统只读、当前工作区可写、网络隔离、凭据不
继承。`DR_RUNNER_MEMORY_BYTES` 是每次操作的地址空间上限，外层 Compose 再限制整个
容器的 CPU/内存/PID。超时终止该次操作的进程组。`trusted` 仅用于明确受信的本地开发，
生产拒绝启用；缺少沙箱时不降级。

可选 archive worker 镜像：

```bash
docker build -t deep-research-agent:local .
docker build -f docker/runner.Dockerfile \
  --build-arg BASE_IMAGE=deep-research-agent:local -t deep-research-runner:local .
# 在实际 Linux worker 环境执行验证，再开放 archive.unpack
python -m scripts.verify_runner_sandbox
```

容器的 seccomp/AppArmor 或宿主机策略可能禁止用户命名空间；安装 bubblewrap 不代表沙箱
已经可用。验收失败时保留拒绝执行状态，使用支持此隔离模型的独立 worker 环境，不修改
宿主机全局策略，也不把 Docker socket、宿主机根目录或真实凭据挂入命令工作区。
LibreOffice/LaTeX 为可选能力，须在目标 worker 中另行安装、限制并验证。

## 一致备份与恢复

备份集合必须包含 PostgreSQL、产物卷、兼容配置卷、部署配置和原解密 key。仅有数据库
无法恢复外部产物；只有新加密 key 也无法解开旧密文。备份目录限制访问，不提交版本库。

以下为 **Linux Bash** 命令；不要在旧版 Windows PowerShell 中用文本重定向保存二进制
归档。Windows 可改用 `docker cp` 或二进制安全的脚本。

1. 停止流量，暂停所有同库 API、worker 副本、CLI 及其他写入者。确认这些进程已经退出；
   跨主机执行者也必须暂停。保留 PostgreSQL 运行。
2. 创建新的备份目录并取得同一冻结时刻的数据库、文件和部署配置：

```bash
docker compose --profile worker stop -t 120 api worker
umask 077
backup_dir="backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
docker compose exec -T db pg_dump -U dr -d deep_research -Fc > "$backup_dir/database.dump"
# 覆盖 entrypoint，不启动 API、不执行迁移；使用 appuser 读取自身卷。
docker compose run --rm --no-deps -T --entrypoint tar api \
  -C /app/artifacts -czf - . > "$backup_dir/artifacts.tar.gz"
docker compose run --rm --no-deps -T --entrypoint tar api \
  -C /app/data -czf - . > "$backup_dir/appdata.tar.gz"
install -m 600 .env "$backup_dir/deployment.env"
docker compose exec -T db pg_restore --list < "$backup_dir/database.dump" > "$backup_dir/database.contents"
tar -tzf "$backup_dir/artifacts.tar.gz" > "$backup_dir/artifacts.contents"
sha256sum "$backup_dir"/*.dump "$backup_dir"/*.tar.gz > "$backup_dir/SHA256SUMS"
```

确认每条命令成功、归档可读，并单独记录镜像版本、迁移版本、部署拓扑和解密 key 的安全
存放位置；随后恢复原有服务。inline 只启动 API，worker 模式同时启动 API 与所有 worker。
线上仍写入时的单独 `pg_dump` 是数据库快照，不能当作数据库/文件的一致恢复集合。

恢复推荐使用新 Compose 项目、新数据库及新卷：

1. 先校验归档哈希。在独立目录准备对应版本代码/镜像和原部署环境，配置不同宿主机端口，
   确认连接的是恢复目标数据库。保留原 `CATALOG_ENCRYPTION_KEY`，启动空 PostgreSQL。
2. 对空目标运行 `pg_restore --no-owner --exit-on-error`，恢复 artifacts/appdata 到对应卷；
   新卷须归 UID/GID 10001，数据卷保持可写。不要对待保留数据库使用 `--clean`。
3. 启动应用前用 `python -m deep_research.migrate` 升级目标库，检查迁移和配置解密。
   保持恢复环境不对外提供付费任务执行，先检查历史记录、SSE 回放、报告导出、来源快照
   和至少一个磁盘产物的 manifest 哈希。
4. 通过后再启用 worker，检查 `/readyz`、队列和心跳。确定要恢复的任务没有超过原截止
   时间，并确认不会与旧部署同时执行；最后才切换流量。

覆盖原目标属于单独的运维决定：必须先确认目标、停止所有写入者并保存当前数据，不把
演练步骤直接应用到仍需保留的环境。

## 发布验收

CI 覆盖静态检查、80% 覆盖率门槛、PostgreSQL 迁移往返、前端契约生成/体积门槛、浏览器
实际 204/键盘/布局、独立 API/worker HTTP 流程、wheel 隔离安装、Docker 导出及 Linux
沙箱探针。`scripts/verify_worker_http.py` 的配额耗尽日志是故障注入场景，随后验证恢复。

这些验收使用合成供应商，只证明工程行为。最终报告校验覆盖引用映射、缺失引用段落和
关键数值；界面显示检查范围与回退状态，不宣称所有段落经过语义证明。实际研究质量需要
冻结来源与人工复核的评估基线，见 `eval/baselines/README.md`。
