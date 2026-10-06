# 前端候选发布验收（2026-10-06）

本记录对应当前 `improvement/scenario-quality-20261006` 工作树；验证完成时尚未提交或部署。
部署后的真实提供方模型质量仍由用户人工评测，以下不冒充生产验收。

## 最终完整检查

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 完整 Vitest | 75 文件 / 518 项通过 | `artifacts/browser-regression/unit-final.log` |
| ESLint | 通过 | `npm run lint -- --quiet` |
| 应用类型检查与构建 | 通过，PDF.js 字体/cMap 已复制 | `artifacts/browser-regression/build-final.log` |
| OpenAPI 一致性 | 重新生成后通过 | `npm run api:check` |
| 浏览器测试类型检查 | 通过 | `npm run test:browser:check` |
| 测试服务器 Ruff | 通过 | `server.py`、`reading_fixture.py` |
| 首屏包体检查 | 通过，初始 JS gzip 120,643 B | `npm run check:bundle` |
| 完整受控浏览器 | 42 通过 / 0 失败 / 0 跳过 | `artifacts/browser-regression/results.json` |
| 完整隔离真实 API 浏览器 | 14 通过 / 0 失败 / 0 跳过 | `artifacts/browser-integration/results.json` |

两份完整浏览器 JSON 的 `metadata.scope` 均为 `full`，不是范围筛选结果。
受控套件于 2026-10-06 07:04:31 UTC 开始；真实 API 套件于 07:04:54 UTC 开始。
最终重新生成的 OpenAPI 类型通过检查后再次构建成功。

最终入口：`/assets/index-BDUz7YAT.js`，SHA-256：
`e50b7d2c6c7e78decb9408b21b415f5263bb15a2f96ffcd32d0c030d25c1750c`。

## 实际覆盖

- 工作台重叠/覆盖、桌面/手机/低高度、明暗主题、配置展开、错误与焦点、七类任务输入契约。
- 登录身份与任务归属、QA/Reader 头像、真实分段 SSE、停止/刷新/继续、模型重试计数及未知用量。
- N0 默认 pending、显式片段选择、版本 409 保留输入、首次与恢复分开、旧问题包保持原字节。
- N6 本地 PDF 原 File 预览、页码与区域边界、同图号重复、共享四区域限额、元数据改变提交身份、手机原生触控。
- 阅读导览多候选、明确的 PDF 文档与完整引文定位、源字符偏移不充当 PDF 坐标、全文反例分区、真实第 1/2 页定位。
- 阅读导览缓存变版后 409；定位前重新校验，期间改变段落/页签/版本的迟到结果不能导航。
- N10 真实 SQLite 统计：个人范围、管理员全工作区、未返回用量保持未知、手机表格横向滚动、统计不含事件正文。

真实 API 测试使用独立 localhost 服务、临时 SQLite、真实文件与渲染进程，以及合成测试身份/记录。
签名审核夹具使用受控 FakeLLM 构造验证数据；没有调用付费模型。所有测试服务均由运行器关闭并清理临时数据。

## 修复与边界

- 曾发现全局 10 秒缓存让阅读导览跳过版本复验。已对导览读取设零新鲜期，并在定位动作前显式重验；没有改变全站缓存策略。
- 曾发现晚到任务确认把错误推到屏幕外。错误保持焦点期间重新保持可见，用户回到输入后不抢滚动。
- api:check 首次失败是生成类型落后于新增后端字段；仅同步生成文件后复验通过。
- 数据与渲染验证不代表任意真实模型科研判断正确，也不表示七类任务已完成人工内容验收。

详细行为分别见 [N9 基线](BROWSER_REGRESSION_N9_20261006.md)、[N0 页面](N0_FRONTEND_ACCEPTANCE_20261006.md)、
[N6 区域选择](N6_PDF_REGION_PICKER_20261006.md)、[阅读导览](READING_MAP_FRONTEND_20261006.md)。
