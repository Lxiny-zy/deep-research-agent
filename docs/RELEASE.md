# 最新上线记录

发布：2026-10-06。本文件固定维护最近一次实际部署，后续上线直接更新，旧版本记录保留在 Git 历史。
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
