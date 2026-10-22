# Aliyun 直接问答修正发布

部署日期：2026-10-09。入口：https://search.lxinyer.com/ 。

本次将普通问答默认改为原文直接作答，严格核验显式启用。根因、调用上限与回归范围见 [直接问答修正](qa-direct-routing-fix-20261009.md)。

| 项目 | 结果 |
| --- | --- |
| 当前应用版本 | `e298b09e5234b055a9b55a46c67e655135d57ca8` |
| 上一应用版本 | `fc9a24ce26829962b1fb9f9dcdfbc01905f999b4` |
| 镜像 | `deep-research-agent:release-e298b09e5234` |
| 镜像 ID | `sha256:d4d7d4374a0a6ba5348f842d7d62f56974fb98061c9ec0c90270c7f6b2f07134` |
| 运行文件校验 | 290 个文件哈希匹配发布包 |
| 前端 | 构建、包体、类型、lint、OpenAPI 与 528 项测试通过 |
| 问答回归 | 324 项通过，4 项 PostgreSQL 专用用例排除 |
| 镜像冒烟 | 非 root、数据库就绪、公开资源、XLSX、中文 PDF 通过 |
| 公网检查 | `/readyz`、首页及引用的 JS/CSS 均 200，字节哈希与发布包一致 |
| 真实同题回放 | 1 次模型请求；27.832 秒出现正文；47.503 秒完成，未降级 |

回放使用原问题的原始论文和前两轮对话，没有重新做证据抽取、公式核验或自动修订，也没有向用户原会话写入测试消息。

## 备份与运行状态

- 一致备份：`/www/wwwroot/deep-research-agent-backups/20261009T090212Z-scenario-dispatch-e298b09e5234`。
- 发布回执：`/www/wwwroot/deep-research-agent-deploy/scenario-dispatch-e298b09e5234-20261009T090212Z/deployed.json`。
- 回滚镜像：`deep-research-agent:rollback-fc9a24ce2682-20261009t090212z`。
- 真实回放记录：`/www/wwwroot/deep-research-agent-deploy/scenario-release-20261009T082938Z/live-replay.json`，服务器私有文件。
- 数据库仍为 `0041`；生产任务并发 1、渲染并发 1、1 GiB 内存及既有模型/凭据配置沿用。

发布使用隔离的 Git 快照，部署服务器已快进到该版本；本地工作分支和暂存区未改变，没有推送 GitHub。

这是一次真实问题的调用与延迟验证，不将其等同于所有科研回答的正确率或固定响应时间。
