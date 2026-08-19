# 供应链与可复现交付

项目将人工维护的依赖范围与实际交付锁文件分开：

- `requirements.txt` / `requirements-dev.txt` 声明直接依赖及允许版本范围；
- `requirements.lock` / `requirements-dev.lock` 锁定完整传递依赖并记录发行包哈希；
- `frontend/package-lock.json` 锁定前端完整依赖树及 npm integrity；
- Docker 和 CI 只使用锁文件安装依赖，Python 安装强制启用 `--require-hashes`。

修改依赖范围后运行 `make lock` 并提交两个 Python 锁文件。`make dependency-check`
会离线检查每个直接依赖是否存在于对应锁文件且版本满足声明范围；CI 会据此阻止只修改
依赖声明、遗漏锁文件的提交。

`make audit` 对 Python 生产依赖和完整前端依赖执行漏洞审计。CI 对高危及严重前端漏洞、
以及任何存在于 Python Advisory Database 中的生产依赖漏洞直接失败。修复应升级依赖并
重新生成锁文件，不应通过忽略列表永久绕过；短期无法修复时，应在变更说明中记录公告
编号、影响分析、缓解措施、责任人和到期时间。

`make sbom` 在 `sbom/` 生成 Python 和前端 CycloneDX SBOM。CI 的供应链 job 会
从生产 Python 锁文件生成 CycloneDX SBOM，并与前端 SBOM 一起作为构建产物保存 14 天。
SBOM 是每次 CI 运行的交付证据，不提交到 Git。

Dependabot 每周检查 Python、npm 和 GitHub Actions 更新。依赖更新应通过供应链审计、
后端测试、前端 lint/构建/测试、wheel 隔离安装和生产容器冒烟后再合并。
