# 供应链与可复现交付

项目将人工维护的依赖范围与实际交付锁文件分开：

- `requirements.txt` / `requirements-pdf.txt` / `requirements-workbench.txt` / `requirements-dev.txt` 声明各组直接依赖及允许版本范围；
- 对应的四份 `requirements*.lock` 锁定完整传递依赖并记录发行包哈希；
- `frontend/package-lock.json` 锁定前端完整依赖树及 npm integrity；
- Docker 和 CI 的 Python 依赖安装强制启用 `--require-hashes`，前端使用 `npm ci`。

四组 Python 锁分别服务于基础运行、PDF 渲染/全文解析、科研工作台、开发测试，不能当成缓存删除。
`make lock` 按基础 → PDF → 工作台 → 开发的顺序生成；后续组以上游锁为约束，避免共同依赖
被锁成不同版本。修改依赖范围后应运行该命令，并提交所有受影响的锁文件。

`make dependency-check` 离线检查直接依赖范围及跨锁文件共同依赖的版本一致性；CI 会据此阻止
声明与锁不同步、或者单份合法但组合安装冲突的提交。此检查不替代实际安装与漏洞审计。

`make audit` 对 Python 基础/PDF/工作台生产依赖和完整前端依赖执行漏洞审计。CI 对中危及以上前端漏洞、
以及任何存在于 Python Advisory Database 中的生产依赖漏洞直接失败。修复应升级依赖并
重新生成锁文件，不应通过忽略列表永久绕过；短期无法修复时，应在变更说明中记录公告
编号、影响分析、缓解措施、责任人和到期时间。

`make sbom` 在 `sbom/` 生成 Python 和前端 CycloneDX SBOM。CI 的供应链 job 会
从生产 Python 锁文件生成 CycloneDX SBOM，并与前端 SBOM 一起作为构建产物保存 14 天。
SBOM 是每次 CI 运行的交付证据，不提交到 Git。

Dependabot 每周检查 Python、npm 和 GitHub Actions 更新。依赖更新应通过供应链审计、
后端测试、前端 lint/构建/测试、OpenAPI 类型一致性、bundle 预算、wheel 隔离安装和生产
容器冒烟后再合并。浏览器与跨进程 HTTP 验收补充 mock 单测没有覆盖的真实响应边界。
