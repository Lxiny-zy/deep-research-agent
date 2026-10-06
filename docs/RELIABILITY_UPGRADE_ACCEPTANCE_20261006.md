# 科研交付与运行可靠性升级验收

第二阶段本地升级已完成并通过下述验收。工作树为
`D:/Cursor-edit/Project_test/deep-research-agent-upgrade`，分支 `upgrade/reliability-20261006`。
本阶段未推送、未部署、未修改生产数据库；第一阶段生产应用仍为 `9723575`，
发布记录见 [第一阶段发布](CONTEXT_AND_ROLE_RELEASE_20261006.md)。原 main 工作树检查为空。

## 完成范围

| 要求 | 已实现的行为 | 详细证据 |
| --- | --- | --- |
| D1/D2/D3 | 显式版本导出、旧页面冲突检测、完好文件独立可用、不可变修复版本、持久操作回执与断线恢复 | [交付接口](DELIVERY_VERSION_CONTRACT_20261006.md) |
| U1/U2/U3 | 数学表格、五类 SVG 图形、源数据表后备、键盘证据交互、窄屏滚动、带版本与局限说明的离线副本 | [前端验收](RELIABILITY_FRONTEND_EVIDENCE_20261006.md) |
| P1/P2 | 可终止渲染子进程、绝对执行期限与进度检查、已完成格式恢复、覆盖全部关闭阶段及晚登记任务的总期限 | [进程边界](RENDER_EXECUTION_RELIABILITY_20261006.md) |
| Q1/Q2 | 公平领取会话头、全局/用户接单上限、停止单轮并保留历史、显式复用落盘阶段、配置和来源变化失效 | [问答恢复](QA_RECOVERY_CONTRACT_20261006.md) |
| S1 | 保留机械拒绝的原选证据与缺失锚点、准确诊断进入局部修订器、数值/专名/条件/因果/多来源正负基准 | [科学核验校准](SCIENTIFIC_SUPPORT_CALIBRATION_20261006.md) |
| V1 | 文件损坏、版本变化、数据库锁等待、卡死子进程、退出、单轮停止和受控浏览器断线的可重放证据 | [故障矩阵](validation/RELIABILITY_FAULT_MATRIX_20261006.md) |

数据库仍为现有 schema `0041`。问答阶段复用私有请求字段，渲染回执复用现有队列和
ArtifactStore；没有为本次升级新增数据库迁移。

## 最终验证

| 验证 | 结果 | 产物 |
| --- | --- | --- |
| 完整非 PostgreSQL 后端套件 | **3,537 通过，0 失败**；795.829 秒 | [JUnit](validation/backend-upgrade-final.xml)、`validation/backend-upgrade-final.log` |
| 全部 PostgreSQL 套件 | **73 通过，0 失败**；115.81 秒 | [JUnit](validation/backend-upgrade-pg-final.xml)、`validation/backend-upgrade-pg-final.log` |
| 前端完整套件 | **66 文件、496 通过** | `validation/frontend-final.log` |
| 前端构建、ESLint、OpenAPI 类型一致性 | 通过 | `validation/frontend-build-final.log`、`validation/frontend-lint-final.log` |
| Ruff、Mypy | 通过；**255 个 Python 模块** | 最终源码检查 |
| Windows / Linux 真实渲染与退出 | 专项 **97 / 23 项通过**，完整套件再次覆盖 Windows | `validation/render-reliability-20261006.txt`、`validation/render-reliability-linux-20261006.txt` |
| 并行宿主负载下独立渲染复验 | **10 次通过；20 个任务均一次完成，无隐含重试** | [逐轮报告](validation/library-render-stress-20261006.md) |
| 三尺寸科学展示、导出恢复、问答/精读停止与继续 | 通过 | `artifacts/reliability-ui/` 截图、真实下载文件和浏览器 JSON 日志 |

日志和截图是本地验收产物，部分受现有 `.gitignore` 忽略，当前工作树中保留。
两套后端测试没有交叉计数，合计 **3,610 项通过**。测试结束后临时 PostgreSQL 容器、
独立前端验收服务及浏览器会话均已关闭。

## 首次失败与修复

首轮后端完整运行有 15 项失败。修复了两个实际整合回归：关闭过程中登记的新后台任务
没有被收拢，以及文件提交成功、数据库完成写入失败后，读取登记没有同步完成状态。
旧测试对父进程渲染函数的注入改为显式测试夹具，生产实现没有线程回退。
新增损坏文件条件下不能错误恢复 done、三级晚登记任务清理等回归，最终完整重跑全部通过。

首次完整 PostgreSQL 运行有 4 项失败，原因是新建临时库没有预先迁移基础 schema，
而四项旧测试要求现成 schema。只对本地临时库执行正常迁移后，全部 73 项通过。

另有一条资料库自动交付的 RuntimeError 在首轮出现，旧日志丢失了原异常类型和代码位置，
无法确认根因。现在父/子进程只记录安全的异常链类型与代码位置，不记录消息、局部变量、
原始内容或全路径。10 次真实子进程复验及最终完整顺序重跑均未复现，不能据此声称已找到原始原因。

## 实际边界

- 本阶段没有调用真实付费模型重新测量科研正确率，原线上论文简答的 fallback 历史未修改。
  正负案例证明核验/修订契约，不能替代真实模型质量统计。
- 浏览器故障验收使用真实组件和受控 HTTP fixture；真实数据库、文件和进程故障由后端
  集成测试覆盖，不能称为生产网络及生产数据验收。
- 渲染有可终止进程边界。任意不响应取消的 Python 协程仍须由 API 总关闭期限及外部 supervisor
  最终终止进程；数据库不可响应时明确返回状态尚未确认，不伪造完成。
- 继续问答是用户显式发起的新请求，只复用完整阶段；未返回的上游响应不自动重发。
  没有可用阶段或超过快照上限时不承诺恢复，也不承诺追回上游已产生的费用。

## 重复执行

使用 Python 3.11 和已安装依赖；Windows 使用短临时路径：

```powershell
python -m pytest -q -m "not pg" --basetemp D:/tmp/dr-repeat --no-cov
python -m ruff check deep_research tests eval/scientific_support_cases.py
python -m mypy deep_research
```

仅对专用本地 PostgreSQL 测试库设置 `DATABASE_URL`，先运行 `python -m deep_research.migrate`，
再运行 `python -m pytest -q -m pg --basetemp D:/tmp/dr-repeat-pg --no-cov`。
不要对生产数据库执行测试。前端在 `frontend` 下依次运行 `npm test`、`npm run build`、
`npm run lint`、`npm run api:check`；浏览器重放命令见前端验收记录。
