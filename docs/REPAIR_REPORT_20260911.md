# 前后端修复与验收记录

日期：2026-09-11。针对基线 `e8cfec3` 的审计问题实施修复，改动保留在工作区。
原始问题、复现材料及修复前结果见
[PROJECT_AUDIT.md](../artifacts/project-audit-20260911/PROJECT_AUDIT.md)。

本轮覆盖权限与归属、HTTP 契约、跨进程配置/执行/流式、报告检查、预算/容量、产物清理、
前端维护性及发布链路。工程验收使用临时数据库、合成凭据和供应商；没有把合成测试结果
当作真实研究准确率或生产容量承诺。

## 问题到修复的对应

| 问题 | 落地改动 | 验证与实际边界 |
| --- | --- | --- |
| F01 删除 204 被误报失败 | 统一传输层识别空响应，删除成功后更新查询 | 真实 HTTP 204、数据库删除、浏览器卡片消失均通过 |
| F02 公共静态资源缺失 | API 服务 public 文件，缺失资源返回 404；wheel 打包根资源 | 源码、wheel、Docker 的 SVG/PNG MIME 与响应通过 |
| F03 删除错误被吞掉 | 展示失败原因，保留未删除选择，禁删进行中记录，末页删除回退 | 组件/API 测试和浏览器禁删检查通过 |
| F04 请求可能无限等待 | 普通请求统一 deadline/取消；导出独立超时；SSE 使用心跳失联判断 | 传输层测试覆盖超时、取消、空响应、错误解析 |
| F05 首屏依赖过重 | 按路由加载报告/工作流/设置；低性能或减弱动画场景延迟 3D | 首屏 JS 从审计约 875 KB 降至约 361 KB；gzip 约 114 KB，已加 CI 门槛；减弱动画时按钮状态与实际停用状态一致 |
| F06 CSS 历史覆盖堆叠 | 15 份样式收敛为基础、组件、布局、页面、动效、打印 6 类；移除 715 条重复声明 | 12 个页面/尺寸的可见元素计算样式前后相同；保留必要 `!important`，没有宣称消除所有历史优先级 |
| F07 长报告重复计算 | token 每 100 ms 合并；生成时文本显示、完成后 Markdown 解析；事件最多保留最近 5000 条、每页 100 条 | 流式/终态/报告测试通过；数据库仍可回放完整事件，不是无限保留浏览器数组；未作真实低端设备性能承诺 |
| F08 工作流草稿/冲突 | 本地草稿、离开保护、409 后比较和显式重载/覆盖 | 工作流编辑与并发冲突测试通过；覆盖是用户明确操作 |
| F09 预检过期 | 结果关联当前选择与配置版本，变化时失效并忽略旧响应 | 预检组件测试通过，服务端创建时仍重新检查 |
| F10 异常页/键盘/移动端 | 中文 404/错误页；dialog 焦点约束；tabs 键盘及关联；折叠灵感/模板 | Chromium 验证 1440/390 下 6 页、键盘、焦点、404，无页面异常和横向溢出 |
| B01 API/worker 配置分歧 | 数据库加密配置版本、CAS 更新、统一解析；checkpoint 保存非秘密配置 | SQLite/PG 并发更新及真实独立 worker 使用在线更新后的模型通过；凭据轮换仍需保持服务一致配置 |
| B02 worker 缺正文 token 流 | 合并增量及累计计数持久化；统一事件版本、seq 和 attempt | 跨进程 SSE、断点事件回放与最终 Markdown 导出通过 |
| B03 同步导出/解析阻塞 | PDF/XLSX/全文解析、文档装配等移入有界线程池 | 异步响应测试、导出测试通过；线程池每进程有界，极重任务仍受整个容器限制 |
| B04 最终正文未经检查 | 对终端报告/声明产物检查引用范围、合格证据映射、段落引用、数值；失败回退为合格素材摘要 | 越界引用、中文数字、缺证据及重编号测试通过；UI 明示检查范围，未宣称完整逐段语义证明 |
| B05 预算/重试边界 | LLM/模型检索请求前预留、usage 结算、保守失败计量、输出限制和重试分类；跨恢复总 deadline | 并行预算与恢复测试通过；上游隐藏用量、检索计费不属于硬账单上限 |
| B06 队列/供应商缺全局限额 | 数据库协调准入和执行租约；按凭据共享并发、请求窗口及冷却；HTTP 身份限流 | SQLite/PG 竞争、过期租约、冷却与跨实例 HTTP 准入测试通过 |
| B07 worker 故障不可见 | 心跳持久化；worker 模式 readyz 要求有效 worker；共享队列/执行指标 | 真进程验证无 worker 为 503、启动后 200；心跳过期与共享指标测试通过 |
| B08 文件生命周期割裂 | run 独立目录；事务内安排清理、后台重试；共享配额与崩溃重算；只读孤儿巡检 | 并发配额、原子失败、脏账本恢复、删除重试、同题隔离通过；备份同时覆盖 DB/文件/原密钥 |
| B09 镜像导出能力不一致 | openpyxl 纳入运行依赖；导出能力发现；wheel 资源完整；字体缓存使用临时可写目录 | 隔离 wheel 实际 XLSX、生产镜像 XLSX/中文 PDF 与公共资源通过 |
| B10 runner 无 OS 隔离 | 默认 required：Linux bubblewrap、prlimit、工作区绑定/系统只读/网络隔离、资源限制；缺失拒绝执行 | 真实 Linux 探针与 bsdtar 解压通过；默认 Docker 策略拒绝 namespace 时也确认拒绝执行；LibreOffice/LaTeX 另需目标环境验收 |
| B11 共享管理员权限 | admin/researcher/reader 身份；run owner；详情、SSE、导出、删除、恢复、标签和批删逐条授权 | 跨身份访问/冲突凭据/撤销/只读/管理边界测试通过；现阶段没有组织租户或报告分享授权 |
| X01 重试导致重复创建 | 幂等 key 随逻辑提交保存，响应不确定时可恢复；后端先识别既有结果再准入，按身份隔离 | 满容量同 key 仍返回原 run，新提交 503；传输、权限及真实 worker HTTP 验收通过 |
| X02 测试边界与质量基线 | 新真实 HTTP、浏览器、独立 worker、镜像/沙箱验收；Judge 接收冻结来源，协议 v2 | 工程链路通过；无来源不评 groundedness，不同协议不混比；人工复核通用基线与 HSI gold 仍待完成 |
| X03 服务和类型重复 | ConfigStore/ReportService、共用 checkpoint 声明和 schema 版本；OpenAPI 生成传输类型 | Mypy、TypeScript、api:check 通过；API/编排仍有大模块，后续可继续按职责拆分 |
| X04 依赖公告/文档过时 | Vitest 4.1.11；WeasyPrint 70.0；中危 npm 门槛；审计纳入 PDF 依赖；能力/恢复文档更新 | npm、Python 核心 + PDF 审计无已知漏洞；新 CI 流程已写入，本地对应验证通过 |

额外修复：模型目录默认项增加数据库唯一索引，并串行更新。真实 PostgreSQL 空目录并发
创建默认模型的测试确认最终只有一个默认项。迁移中的 `is_default` 按实际 Integer 类型
使用 `0/1`，兼容 SQLite/PostgreSQL。

## 验收结果

可机读汇总见 [verification-summary.json](../artifacts/project-audit-20260911/verification-summary.json)，
包含测试数、覆盖率、bundle 大小、镜像 ID 和 wheel 哈希。

| 检查 | 最终结果 | 记录 |
| --- | --- | --- |
| 后端完整离线测试 | 1352 passed；16 个 PG 用例另跑；覆盖率 86.24%，高于 80% 门槛 | [日志](../artifacts/project-audit-20260911/fix-backend-final.txt) |
| PostgreSQL | 16 passed；迁移、模型漂移、最新版本回滚/重放通过 | [日志](../artifacts/project-audit-20260911/fix-postgres-final.txt) |
| Ruff / 格式 / Mypy | 268 个 Python 文件格式通过；118 个核心源文件类型通过 | [日志](../artifacts/project-audit-20260911/fix-static-final.txt) |
| 前端 | 31 个文件、246 项测试通过；ESLint、TS、Vite、OpenAPI 一致性及 bundle 门槛通过 | [测试](../artifacts/project-audit-20260911/fix-frontend-final.txt)、[构建](../artifacts/project-audit-20260911/fix-frontend-build-final.txt) |
| 浏览器 | 两个脚本通过；6 页 × 2 尺寸、实际删除、键盘、dialog、404、静态 MIME | [工作区](../artifacts/ui-workspace/final/results.json)、[检索流程](../artifacts/project-audit-20260911/fix-browser-search-final.txt) |
| CSS 迁移 | 迁移前后 12 组计算样式一致 | [迁移前](../artifacts/ui-workspace/before-css/results.json)、[迁移后](../artifacts/ui-workspace/after-css/results.json) |
| 跨进程 HTTP | 配置更新、容量幂等、token SSE、取消终态、存储故障恢复 attempt 2、导出通过 | [日志](../artifacts/project-audit-20260911/fix-worker-http-final.txt) |
| Wheel | 构建、隔离安装、CLI、迁移资源、公共图像及实际 XLSX 通过 | [日志](../artifacts/project-audit-20260911/fix-wheel-final.txt) |
| Docker | 非 root、只读镜像、临时可写卷；真实就绪/公共文件、应用 XLSX/PDF 导出、模型漂移检查通过 | [日志](../artifacts/project-audit-20260911/fix-container-smoke-final.txt) |
| Linux runner | 私有文件/环境不可见、系统只读、网络隔离、rlimit、实际内存限制、输出截断、超时子进程清理、bsdtar 通过 | [日志](../artifacts/project-audit-20260911/fix-runner-sandbox-linux.txt) |
| 无沙箱条件 | 基础镜像缺工具、Docker 默认禁止 namespace 时均拒绝执行 | [缺工具](../artifacts/project-audit-20260911/fix-container-smoke-final.txt)、[内核限制](../artifacts/project-audit-20260911/fix-runner-sandbox-refusal.txt) |
| 依赖 | 锁文件一致；npm 0 漏洞；Python 核心/PDF 无已知漏洞 | [npm](../artifacts/project-audit-20260911/fix-npm-audit-final.txt)、[Python](../artifacts/project-audit-20260911/fix-pip-production-audit-final.txt) |

本机 Python 3.11.8、Node 24.11.1，Docker 前端构建使用 Node 20。PostgreSQL 16 使用
本轮独立的本地容器。Linux runner 正向测试使用单独的、无外网、非 root、限 CPU/内存/PID
容器；为允许创建命名空间，仅在该测试容器中解除默认 seccomp，保留无额外 capability 和
no-new-privileges。生产 Compose 没有解除这些限制；目标 worker 须具备所需内核能力。
本轮 API/PostgreSQL 测试容器及专用 Docker 网络已清理，验收日志、wheel 和本地镜像保留。

补充依赖审计发现原 WeasyPrint 68.1 受
[PYSEC-2026-3412](https://osv.dev/vulnerability/PYSEC-2026-3412) 和
[PYSEC-2026-3940](https://osv.dev/vulnerability/PYSEC-2026-3940) 影响，已更新到 70.0 并
重新生成带哈希锁文件、构建镜像和验收导出。CI 审计从仅核心依赖扩展到包含 PDF 依赖，
未使用漏洞忽略列表。

## 使用与后续边界

部署、角色配置、容量、命令隔离和一致备份步骤见 [OPERATIONS.md](OPERATIONS.md)。
升级新增迁移到 `0027`，生产使用 `python -m deep_research.migrate`；所有 API/worker
须使用同一数据库、产物根目录和原解密 key。

仍需在具体业务环境完成真实供应商调用/配额验证、组织容量压测、人工质量基线复核、
LibreOffice/LaTeX 可选工具验收及正式备份恢复演练。确定性引用/数值检查无法证明整篇报告
语义正确；现有角色体系也不是完整组织租户平台。这些边界不计作已通过的工程测试。
