# 最新上线记录

最新发布：**2026-11-08，`8425e3130fe4b842f79bb5c238b40ffba9a4816f`**（幻灯片版式问题改为标注后交付；PDF 表格按整体宽度计算带连字符的名称，见 [主计划](IMPROVEMENT_PLAN.md) S75–S78，含 `e91c8cb`）。

- 发布方式：同前，仅替换 api 容器；切换前无进行中的任务。镜像 `sha256:836561c0c2454b3a66f4176116f6226fb13ce56d2d39adca6d1ad5ecfd96e948`。数据库仍为 **0045**，无迁移。
- 验证：容器 healthy，`/readyz` 200。发布前本地全量回归 4174 通过、2 跳过，ruff、mypy 通过。

2026-11-07 至 11-08 期间的逐次发布（每次均为同样流程：备份数据库、打回滚标签、仅替换 api 容器、发布前全量回归通过）：

| 提交 | 日期 | 内容 |
|---|---|---|
| `6eae650` | 11-08 | 正文与附录中名称上标（`MixS^2`）、`\cite{…}` 规整；PDF 末段比对忽略省略号与引号 |
| `14476d2` | 11-08 | 渲染子进程被杀时任务停在待复核并保留正文（不再记为出错、用量记 0）；名称连字符不再被读作负号；镜像设 `MALLOC_ARENA_MAX=2`（api 常驻内存约 520 MB → 运行中约 290 MB） |
| `18cf79e` | 11-07 | 无法渲染的表格规格从交付稿中移除并注明，不再拦截整份交付；公式核验窗口受预算约束；可选概念图低思考强度 |
| `a7ae62d` | 11-07 | 公式核验整篇原文上限 6 万字降到 2.4 万字；图示局部修订低思考强度 |
| `83a902a` | 11-07 | 来源附录中紧贴数字的公式标记规整 |
| `66f840e` | 11-07 | 写稿出口规整 `1$\times$1` 类紧贴数字公式；PDF 超宽表格压缩列宽不再报错 |

2026-11-05 先发布 **`fd49aa9f9e0e428265918ba06c979b267827561d`**（调度空转、截断降档、交付门限分级与写作提示词 v2，见 [主计划](IMPROVEMENT_PLAN.md) S70–S74，含 `b58e338`、`52bb123`、`5044ee4`、`99c0e3a`）。

- 发布方式：同前，仅替换 api 容器（前端随镜像构建，运行页新增待核清单）；切换前无进行中的任务（已完成 16、出错 4、待复核 47、已取消 3）。
- 镜像：`sha256:5c641cfcb062eeeb8f05e97db656e1160ffcf2110801dba8fd7a4741b096a987`；回滚标签 `deep-research-agent:rollback-bb5015d35550-20261105t002618z`。
- 备份：`20261105T002618Z-tiered-v2-fd49aa9f9e0e`（88 MB）；回执 `/www/wwwroot/deep-research-agent-deploy/tiered-v2-fd49aa9f9e0e-20261105T002618Z/deployed.json`。
- 数据库仍为 **0045**，无迁移、无依赖变化。
- 行为变化：只剩可修订问题的任务以「完成」结束并生成正式文件，相关段落标〔待核〕；写作默认使用 v2 提示词，`DR_PROMPT_VARIANT=legacy` 可回退。
- 验证：容器 healthy，本地与公网 `/readyz` 均 200，切换后日志无错误。发布前本地全量回归 4163 通过、2 跳过，ruff、mypy、前端类型检查与运行页测试通过。

2026-11-04 先发布 **`bb5015d35550bb952a9365c2681bb37ca977dc92`**（第七轮线上真测的修复，见 [主计划](IMPROVEMENT_PLAN.md) S68–S69，含 `74a7266`）。

- 发布方式：同前，仅替换 api 容器。旧版本上的幻灯片测试任务 `71c138f3` 已主动取消，第八轮在新版本上完整重测；切换前无进行中的任务（已完成 16、出错 4、待复核 47、已取消 2）。
- 镜像：`sha256:1066993adbcece511084efef3232c794a52d43d81254054e3418a5d8225c4d34`；回滚标签 `deep-research-agent:rollback-8ad53edcd9e7-20261104t052408z`。
- 备份：`20261104T052408Z-online-round7-fixes-bb5015d35550`（86 MB）；回执 `/www/wwwroot/deep-research-agent-deploy/online-round7-fixes-bb5015d35550-20261104T052408Z/deployed.json`。
- 数据库仍为 **0045**，无迁移、无依赖变化、无前端变化。
- 配置（非代码）：用户在模型档案中把 deepseek 的最大输出由 20000 改为 200000（原值设置有误，见 S25 第七轮）。
- 验证：本地与公网 `/readyz` 均 200，切换后日志无错误。发布前本地全量回归 4120 通过、2 跳过，ruff、mypy 通过。

2026-11-04 先发布 **`8ad53edcd9e7d9aa368c6d9f7023ca69e159f117`**（第六轮线上真测前半程的修复，见 [主计划](IMPROVEMENT_PLAN.md) S64–S67，含 `3fdca48`）。

- 发布方式：同前，仅替换 api 容器。旧版本上的幻灯片测试任务 `f27af6cf` 已主动取消（第六轮剩余项改在新版本上测），准备与切换前均确认无进行中的任务（已完成 16、出错 2、待复核 42、已取消 1）。
- 镜像：`sha256:8a7768f6bc499d7ad153f0968e398f4c6e9b7997178f47150e418dd214f451a3`；回滚标签 `deep-research-agent:rollback-d74c38ba1161-20261104t035911z`。
- 备份：`20261104T035911Z-online-round6-fixes-8ad53edcd9e7`（79 MB）；回执 `/www/wwwroot/deep-research-agent-deploy/online-round6-fixes-8ad53edcd9e7-20261104T035911Z/deployed.json`。
- 数据库仍为 **0045**，无迁移、无依赖变化、无前端变化。
- 验证：本地与公网 `/readyz` 均 200，切换后日志无错误。发布前本地全量回归 4116 通过、2 跳过（排除 PostgreSQL 用例），ruff、mypy 通过。

2026-11-04 先发布 **`d74c38ba1161ea0b1e92606d30d09d287cfe0daa`**（第五轮线上真测的修复，见 [主计划](IMPROVEMENT_PLAN.md) S58–S63，含 `5ea6e36` 与 `d310387`）。

- 发布方式：同前，仅替换 api 容器；准备与切换前均确认无进行中的任务（已完成 16、出错 2、待复核 39）。
- 镜像：`sha256:19b86bc6bedc88695a8a59eb0ec8d74eed6a69fb96f77d4a8383a6a616c498ea`；回滚标签 `deep-research-agent:rollback-a0decc7e48ba-20261104t010911z`。
- 备份：`20261104T010911Z-online-round5-fixes-d74c38ba1161`（数据库导出 69 MB，`gzip -t` 通过）；回执 `/www/wwwroot/deep-research-agent-deploy/online-round5-fixes-d74c38ba1161-20261104T010911Z/deployed.json`。
- 数据库仍为 **0045**，无迁移、无依赖变化、无前端变化。
- 验证：容器 healthy，本地与公网 `/readyz` 均 200，切换后日志无错误。发布前本地全量回归 4105 通过、2 跳过（排除 PostgreSQL 用例），ruff、mypy 通过。

2026-11-04 先发布 **`a0decc7e48baa22e0de1e30568d2d911d7d3f131`**（第四轮线上真测的修复，见 [主计划](IMPROVEMENT_PLAN.md) S50–S57，含 `1eb136a`）。

- 发布方式：同前，仅替换 api 容器；准备与切换前均确认无进行中的任务（已完成 16、待复核 34）。
- 镜像：`sha256:b80fb2ef19da905af1efb527308722323690131dae1851526aa2b801993d6436`；回滚标签 `deep-research-agent:rollback-4bdc511a4521-20261103t160724z`。
- 备份：`20261103T160724Z-online-round4-fixes-a0decc7e48ba`（数据库导出 50 MB，`gzip -t` 通过）；回执 `/www/wwwroot/deep-research-agent-deploy/online-round4-fixes-a0decc7e48ba-20261103T160724Z/deployed.json`。
- 数据库仍为 **0045**，无迁移、无依赖变化、无前端变化。
- 验证：容器 healthy，本地与公网 `/readyz` 均 200，切换后日志无错误。发布前本地全量回归 4098 通过、2 跳过（排除 PostgreSQL 用例；S56 的改动在该轮开跑后写入，另由相关 280 项测试覆盖），ruff、mypy 通过。
- 运维备注：这次本机经代理连接服务器被关闭，直连正常，与 11-03 情况相反；两种方式都要备用。

## 历史记录：2026-11-03

2026-11-03 发布 **`4bdc511a45219f3b3d255df916f05651172738f2`**（第三轮线上真测的修复，见 [主计划](IMPROVEMENT_PLAN.md) S41–S49，含 `11e2127` 与 `9b1c0b3`）。

- 发布方式：同前，服务器 `git merge --ff-only origin/main`，按 `docker-compose.yml` + 发布覆盖文件构建 `deep-research-agent:release-4bdc511a4521`，仅替换 api 容器；准备与切换前均确认无进行中的任务（已完成 16、待复核 30）。
- 镜像：`sha256:22385b05c6f72d9264f22ba7c8bf3b3fc7cba7766b88646e8ce2e08019586f50`；回滚标签 `deep-research-agent:rollback-88ff116cfa8c-20261103t115350z`。
- 备份：`20261103T115350Z-online-round3-fixes-4bdc511a4521`（数据库导出 40 MB，`gzip -t` 通过）；回执 `/www/wwwroot/deep-research-agent-deploy/online-round3-fixes-4bdc511a4521-20261103T115350Z/deployed.json`。
- 数据库仍为 **0045**，无迁移、无依赖变化、无前端变化。
- 验证：容器 healthy，本地与公网 `/readyz` 均 200，切换后日志无错误。发布前本地全量回归 4073 通过、2 跳过（排除 PostgreSQL 用例），ruff、mypy 通过。线上真实模型场景测试的结果记入主计划 S25。
- 运维备注：当天本机直连服务器 22 端口在密钥协商阶段被重置，经本机 HTTP 代理（`ProxyCommand connect -H`）连接正常。

## 历史记录：2026-11-01

2026-11-01 发布 **`88ff116cfa8cf9cbc2f2961e90d718ca449ebfae`**（第二轮线上真测的修复，见 [主计划](IMPROVEMENT_PLAN.md) S32–S40，含未单独发布的 `8b850d3`）。
当天先发布 `923d062344309dc09bcb6a633073a2f032106244`（第一轮线上真测的修复 S26–S31）。

- 发布方式：同前，服务器 `git merge --ff-only origin/main`，按 `docker-compose.yml` + 发布覆盖文件构建 `deep-research-agent:release-<sha12>`，仅替换 api 容器；两次切换前均确认无进行中的任务。
- 当前镜像：`sha256:c01629e8613b31a9205c652a048de47189c1f3803196722a0c138d629779c516`；回滚标签 `deep-research-agent:rollback-923d06234430-20261031t222116z`，`923d062` 发布前打的是 `deep-research-agent:rollback-e4d59fd31e87-20261031t173224z`（`923d062` 的镜像为 `sha256:b5e4d1e8933c551dcbcf010b7ed9a80f33bb039c40486e450ae3ca49ceebaa77`）。
- 备份：`20261031T173224Z-online-round1-fixes-923d06234430`（数据库导出 12 MB）与 `20261031T222116Z-online-round2-fixes-88ff116cfa8c`（24 MB），`gzip -t` 均通过。
- 回执：`/www/wwwroot/deep-research-agent-deploy/` 下 `online-round1-fixes-923d06234430-20261031T173224Z` 与 `online-round2-fixes-88ff116cfa8c-20261031T222116Z` 的 `deployed.json`。
- 数据库：`923d062` 迁移到 **0045**（结果与发现增加写入位置，S26）；`88ff116` 无迁移、无依赖变化。交付格式指纹仍为 68，核验策略版本未变，历史任务不因这两次发布重新渲染。
- 验证：两次切换后容器均 healthy，本地与公网 `/readyz` 均 200，日志无错误。`88ff116` 发布前本地全量回归 4019 通过、2 跳过（排除 PostgreSQL 用例），ruff、mypy 通过。线上真实模型场景测试的结果记入主计划 S25。

## 历史记录：2026-10-31

2026-10-31 发布 **`e4d59fd31e87b5b6df5a3f26b770afa118684ab1`**（PDF 中文字体子集化，见 [主计划](IMPROVEMENT_PLAN.md) S24）。
当天先发布 `60039cd5e50d3aed637778f3ad542c96f7f5002c`（交付内容质量规则 S15–S17、S21–S23，以及 10-30 本地完成的 S11–S14：Word 行距、PDF 分段函数间距、幻灯片真实标题、导图分支页）。

- 发布方式：同前，服务器 `git merge --ff-only origin/main`，按 `docker-compose.yml` + 发布覆盖文件构建 `deep-research-agent:release-<sha12>`，仅替换 api 容器；两次切换前均确认无进行中的任务。
- 镜像：`sha256:e89abdc08fceefa0675dc48ea36b0def8010f980d109b4c5486df176b9834be5`；回滚标签 `deep-research-agent:rollback-60039cd5e50d-20261031t104834z`，`60039cd` 发布前打的是 `deep-research-agent:rollback-80236f80f99a-20261031t095209z`。
- 备份：`20261031T095209Z-delivery-quality-60039cd5e50d` 与 `20261031T104834Z-pdf-font-subset-e4d59fd31e87`（数据库导出约 5.3 MB，`gzip -t` 通过）。
- 回执：`/www/wwwroot/deep-research-agent-deploy/` 下 `delivery-quality-60039cd5e50d-20261031T095209Z` 与 `pdf-font-subset-e4d59fd31e87-20261031T104834Z` 的 `deployed.json`。
- 数据库仍为 **0044**，无迁移、无依赖变化。交付格式指纹由 66 升至 68，历史任务下次打开时按新规则重算交付登记并重新渲染。
- 验证：容器 healthy，本地与公网 `/readyz` 均 200，切换后日志无错误；公网前端分包已含「生成幻灯片」「生成思维导图」与「证据来源」。发布后复核历史任务，发现线上 PDF 内嵌整套 Noto Serif CJK 字体（单页报告约 20 MB），修复后同一任务的 PDF 为 142 KB。线上真实模型场景测试的结果记入主计划 S25。

## 历史记录：2026-10-28 至 2026-10-29

2026-10-29 发布 **`80236f80f99a82352015f64f73246110579a7347`**（任务记录谱纹改为独立的「研究谱」列，见 [主计划](IMPROVEMENT_PLAN.md) S10）。
当天先发布 `8c62e1a`（一问一谱：每次研究读作一组思路线，S9；任务列表接口每条任务新增 `spectrum`，按子问题顺序：未检索 -1，检索过但无可用证据 0，否则为核验且语义支持的发现数，每页两条聚合查询，不加载证据正文）。
此前 2026-10-28 依次发布 `84d86e6`（谱线主题）、`f61aea8`（过渡 / 观感 / 场景链路 / 精读原文视图，新增只读接口 `GET /api/runs/{run_id}/reader/{document_id}/passages`）、`eb0a24e` 与 `c187607`（访客进入时只收拢谱线并在登录框背后保持收拢）、`dad01f2`（任务记录勾选后才显示批量删除）。

- 发布方式：服务器 `git merge --ff-only origin/main`，按 `docker-compose.yml` + 发布覆盖文件构建 `deep-research-agent:release-<sha12>`，仅替换 api 容器；每次切换前确认无进行中的任务。
- 当前镜像：`sha256:03e498cd09bf19835bb73de460dc49001445b78ea9bc2b7607bbc2a52b8cd477`；回滚标签 `deep-research-agent:rollback-8c62e1a4ae4b-20261029t151417z`（更早的回滚标签逐次保留）。
- 备份：每次发布前在 `/www/wwwroot/deep-research-agent-backups/` 下新建目录保存数据库导出；`20261028T023101Z-*` 与 `20261028T095636Z-*` 两次另含 appdata、artifacts 卷归档，均已校验可读。本次为 `20261029T151417Z-spectrum-column-80236f80f99a`，`8c62e1a` 为 `20261029T045319Z-thought-spectrum-8c62e1a4ae4b`（数据库导出均约 5.2 MB，`gzip -t` 通过）。
- 回执：`/www/wwwroot/deep-research-agent-deploy/` 下对应目录的 `deployed.json`（本次 `spectrum-column-80236f80f99a-20261029T151417Z`）。
- 数据库仍为 **0044**，无迁移、无依赖变化；本次只改前端，接口不变。
- 验证：切换前无进行中的任务；容器 healthy，本地与公网 `/readyz` 均 200，切换后两分钟内日志无错误；公网任务记录页分包与样式已是新版「研究谱」列。`8c62e1a` 发布时另在生产 PostgreSQL 上验证任务列表查询：18 个任务耗时 101 ms（含建立连接），17 个带谱纹、共 12 条暗线。未进行真实付费模型评测。

---

## 历史记录：2026-10-09 及更早

2026-10-09 发布 `e298b09e5234`（普通问答默认直接回答，严格核验显式启用），记录见 [Aliyun 直接问答修正发布](archive/2026-10/deployment-direct-qa-20261009.md)。
2026-10-16 曾发布 `8bbddcb` 与 `ac39b3c`，回执位于服务器 `deep-research-agent-deploy/` 下对应目录。

## 历史记录：2026-10-06

以下保留此前发布的验证范围，不代表当前运行版本。

发布：2026-10-06。
用户要求的场景优化已上线：<https://search.lxinyer.com/>。真实内容由用户线上人工验收。
后续仅文档提交不改变此处的运行应用版本，当前任务状态见 [主计划](IMPROVEMENT_PLAN.md)。

## 发布身份与回滚

- 应用提交：`8edb37ed57f0f76e6e1725db3f4140882b57edb6`。
- 镜像：`deep-research-agent:release-8edb37ed57f0`。
- 生产镜像配置 ID：`sha256:e8ff7b8b6b973bc332ea68ef8dad0af6b7e4b13498f4b2bcfaa81bfef3dd63a9`。
- 上传归档 SHA-256：`bf33c0d90cc2715d691e5155aafcbaaedca2ed7bca7aafce27694f83b5c8e031`。
  Docker Desktop 本地 image-store ID 为 `sha256:2cdbadb77aa50377d90dc0ed05802f74f4a7db7f1e544d52d1663dfcc442763d`；
  已从同一归档 `manifest.json` 提取 config 并计算 SHA，确认与生产配置 ID 完全一致。
- 原应用：`6d91be7e702e2efd3af0766c8ee31d4275b9718a`。
- 原镜像：`sha256:ec11f1e324c5ba4506a994b3d1e2a75ed4db17e72554897a5bc840e27ecf67c5`。
- 回滚标签：`deep-research-agent:rollback-6d91be7e702e-20261006t091801z`。
- 已验证备份：`/www/wwwroot/deep-research-agent-backups/20261006T091801Z-optimization-8edb37ed57f0`。
- 发布回执：`/www/wwwroot/deep-research-agent-deploy/optimization-8edb37ed57f0-20261006T091801Z/deployed.json`。

数据库仍为 **0041**，未执行生产迁移或数据恢复。沿用 inline、任务并发 1、渲染并发 1、
1 GiB、PID 256 和只读根；新增 `DR_APP_REVISION` 仅用于记录应用版本，其余配置与原版逐项比对保持一致。

## 本轮已交付

七类任务的取证/条件/评审/统计记录与交付改进，人工验收记录及最小问题包，版本绑定阅读导览，
PPT 原生图表/可编辑公式及用户 PDF 区域选择，导图分支分页与索引，长对话约束保护，真实请求与运行统计，
工作台和移动端布局改进。支持范围与具体增量见 [主计划](IMPROVEMENT_PLAN.md)。

发布前修复 PDF 页底公式脚注缩小、Word 长表表头不重复、阅读导览缓存绕过版本检查、延迟模型角色绑定递归，
以及 Linux NBSP 被误判为导图节点丢失。没有降低原事实、引用、统计或格式质量门。

## 验证证据

- 冻结候选完整后端：**3,685 通过，覆盖率 90.96%**，单次退出 0，650 个源文件前后哈希一致。
  随后的 Linux NBSP 最小修复另通过 11 项回归及真实 Linux PNG 检查。
- PostgreSQL：**74 通过**。前端：**75 文件 / 518 项通过**；Lint、构建、API 契约和类型检查通过。
  后端 Ruff check 与 Mypy 274 模块通过；额外 Ruff formatter 全仓检查仍有格式债（含 89 个本轮未修改文件），
  未将全仓格式化混入此次发布，也不宣称所有格式检查或所有远程 CI 项均通过。
- 浏览器：完整受控 **42/42**、隔离真实 API **14/14**；包括登录归属、版本冲突、旧文件字节、PDF 定位、
  人工验收记录、区域框选、阅读导览缓存与迟到响应、运行统计。
- 最终镜像真实 API lifespan + SqlRenderQueue/RenderProcess：两个并发请求由最多一个真实子进程执行，
  N7 28 节点、13 PNG、审核绑定有效，长 PDF 19 页；均一次完成，进程、租约、物理槽均释放。
  **1 GiB / 无交换 / 只读根 / PID 256** 下峰值 **821.5 MiB**，无 max/OOM/OOM-kill 事件。
  此项不包含真实模型负载或 PPT 并发，不能外推任意负载容量。
  Linux Noto CFF 字体在 `Document.subset_fonts()` 阶段仍出现 `Reserved charstring byte c=0x0` 警告，
  最小中文样本的中间 PDF 约 20 MB，PNG 视觉与节点校验通过；字体未有效子集化的问题未修复，
  与已修复的 NBSP 节点误报分开记录。最终镜像容量验证包含当前字体路径的实际开销。
- Office 检查：LibreOffice 实际渲染表格/柱图/折线/裁剪图片与 Word 长表；安装 Math 组件后 Word 公式可见。
  WPS 实际打开并导出的表格/柱图 PPT 中公式目视正确。LibreOffice 7.4 未显示 PPT DrawingML 数学扩展，
  不宣称该软件对可编辑公式兼容；PPT 建议用支持原生 Office Math 的 PowerPoint/WPS 验收。
  WPS 多文档 COM 清理发生过 RPC 错误，已保留成功导出的证据，不据此宣称全版本自动化稳定。

最初全量遇到测试运行期间 64/65 格式版本混用，后固定版本重核；另一次 worker 集成测试误把
`_drain` 关机逻辑当成完成等待，20 秒主动取消了正常任务。修正测试驱动后保留原 45 秒测试上限和
生产 20 秒关闭预算，同例带覆盖率约 27 秒完成；最终完整回归为独立一次全绿结果。

## 实际线上核对

- 容器 healthy，公网 `/readyz` 200；入口由 `index-DL460P1C.js` 更新为 `index-BDUz7YAT.js`。
- 匿名桌面/手机 16 个视图、六个深链、32 个 JS/CSS 状态与 MIME 正常；无页面溢出、JS 异常或额外网络错误。
  仅有预期匿名配置探测 401；缺失静态资源正确返回 404。浏览器会话已关闭。
- 新容器中真实 PostgreSQL + HTTP 八项通过：合成 QA 存储、运行统计、阅读原文与版本绑定、
  N0 登记、导出回执、旧版 409、旧验收包及旧导出字节保持。未调用模型。
- 首次线上冒烟因测试脚本的合成 claim_id 超过 PostgreSQL VARCHAR(32) 失败；隔离 PG 复现后只修脚本，
  完整 PG 自验与线上重跑通过，未修改业务 schema。两次均完成合成数据清理。
- 合成数据清理后，研究/问答/渲染 ID 与状态指纹与部署前完全一致，活动任务均为 0。

本地证据在 `artifacts/optimization-deployed.json`、`artifacts/optimization-live-ui/`、
`artifacts/optimization-render/` 和 `docs/validation/optimization-release-nonpg.{log,xml}`。
代码与记录已推送 GitHub main；生产运行应用提交与后续仅文档更新分别记录。

## 人工验收边界

没有新增真实付费模型质量评测，不宣称科研正确率或所有新输入首次成功率已达标。
重复长表的通用推断、XMind 工程文件、XeLaTeX/latexmk 等此前不支持的能力未被标为可用。
建议用户新建任务验收，并通过任务页的人工验收记录保存问题位置、版本和原文依据。

本地生成的 `.n12`、`.n7linux-check` 清理被自动审批拒绝（仅返回 `blocked by policy`）；
已排除 Git 提交、源码 bundle 和发布镜像，作为本地残留保留，不影响生产发布。
