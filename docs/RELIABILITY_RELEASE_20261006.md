# 可靠性升级发布记录

用户在本地验收完成后明确授权上线。本次已于 2026-10-06 部署到
[search.lxinyer.com](https://search.lxinyer.com/)，最终检查通过。

## 发布身份与回滚

- 应用提交：`6d91be7e702e2efd3af0766c8ee31d4275b9718a`，已推送 GitHub main。
- 运行镜像：`deep-research-agent:release-6d91be7e702e`。
- 运行 image ID：`sha256:ec11f1e324c5ba4506a994b3d1e2a75ed4db17e72554897a5bc840e27ecf67c5`。
- 前一应用：`9723575a066270937cbb60de4041535c9d55fc6a`，原镜像和独立 rollback tag 均保留。
- 备份：`/www/wwwroot/deep-research-agent-backups/20261006T003823Z-qa-repair-6d91be7e702e`。
- 部署回执：`/www/wwwroot/deep-research-agent-deploy/qa-repair-6d91be7e702e-20261006T003823Z/deployed.json`。
- PostgreSQL 自定义格式备份及 data/artifacts 卷归档均验证成功。发布没有数据恢复或 schema 升降级。

## 上线前发现并处理的资源问题

完整本地验收对应提交 `60aa36f`：3,537 项后端、73 项 PostgreSQL、496 项前端测试通过。
在发布镜像的真实 API lifespan、只读根目录、非 root、PID 256、1 GiB 内存约束下，
两份两页中文 PDF 并发峰值达 923.4 MiB，其中匿名内存 910.6 MiB。虽然没有 OOM，
但不足以给同时运行的研究/问答留下合理余量。

因此在 `6d91be7` 加入 `DR_RENDER_MAX_PROCESSES`（默认 2），生产显式设为 **1**。
队列领取和跨进程物理槽使用同一配置，所有 API/worker 必须一致。
变更后 36 项相关回归及 Ruff/Mypy 通过；重新构建后的同约束镜像测试中，两个请求同时提交，
实际最多一个子进程，4 次 PDF/Markdown 导出均一次成功。总峰值降至 **521.4 MiB**，
匿名内存峰值 512.7 MiB，无交换、OOM 或限额事件。

现有 inline 模式、`MAX_ACTIVE_RUNS=1`、1 GiB 内存、PID 256、只读根目录和凭据/卷配置保留。
唯一有意增加的环境配置为渲染并发 1。数据库仍为 **0041**；未新增迁移。
XeLaTeX/latexmk 仍未安装，没有把原不具备的能力标为可用。

## 实际上线验证

切换前、停止前和停止后均检查研究、问答、渲染任务为 0；状态指纹未改变。
运行镜像/提交一致，容器 healthy，公网 `/readyz` 返回 200。

- 生产容器：非 root、首页和图标资源、中文 PDF、XLSX 文本单元格安全检查通过。
- 实际 HTTP + PostgreSQL：使用独立合成记录验证重复导出复用回执、按 request_id 找回操作、
  内容 hash、版本冲突返回 409、历史导出字节保持不变。渲染一次完成。
- 实际取消接口：合成运行租约从 running 转为 cancelled，刷新仍为终态，历史保留。
  合成记录随后删除；没有发起模型调用。
- 桌面 1440 和手机 390 宽度：真实首页、登录框无横向溢出；六类深链接正常。
  35 个静态资源和 MIME 正常，缺失 JS/CSS 返回 404。
  只有匿名配置探测产生预期 401，没有 JS 异常或其他网络错误。
- 合成记录清理后，原研究/问答/渲染记录的 ID/状态指纹与发布前一致，活动任务为 0。

本地证据位于升级工作树：

- `artifacts/reliability-image-smoke-6d91be7e702e.json`
- `artifacts/reliability-deployed.json`
- `artifacts/reliability-live-result.json`
- `artifacts/qa-repair-deploy.log`
- `artifacts/reliability-release-ui/`（截图、网络、路由、控制台及汇总）

第一次取消冒烟的合成夹具误用了只存历史正文的 `SqlQaStore.append`，请求 ID 没有落库，
导致检查返回 404；修正为原子登记独立合成租约后检查通过。这是验收夹具错误，
没有据此修改线上业务代码，也没有修改用户会话。

## 保留的边界

[本地完整验收](RELIABILITY_UPGRADE_ACCEPTANCE_20261006.md) 中的科学质量与未知异常边界仍然有效。
早期一条偶发渲染 RuntimeError 的原始根因尚未确认，后续完整重跑及 10 次真实进程复验未复现，
现已保留安全诊断。这次上线不等于保证没有未知缺陷。
浏览器检查覆盖公共入口，认证后的功能由实际 API 冒烟覆盖；未重新调用付费模型测量科研正确率。
