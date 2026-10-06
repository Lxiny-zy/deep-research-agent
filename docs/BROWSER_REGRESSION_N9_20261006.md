# N9 浏览器回归基线

## 状态与证据范围

2026-10-06：受控接口及隔离真实 API 两层基线已建立；**N9 部分完成，未部署、未做生产整链验收**。
测试使用实际 React 页面、CSS、浏览器布局、原生文件选择和 fetch/SSE 消费代码。
普通接口在浏览器路由层返回明确的受控响应；QA 流通过临时 localhost HTTP 服务逐段发送，
必须先出现未完成片段，再由测试显式发布 complete。没有模型调用。

模拟数据、模拟权限和受控取消响应不证明后端认证、数据库隔离、取消写入隔离或科研正确率。
原有 API/数据库故障矩阵仍承担其相应契约；本文件只记录浏览器层证据。

## 运行

在 `frontend` 目录执行：

```powershell
npm ci
npm run test:browser:install
npm run test:browser:check
npm run test:browser
```

入口为 [playwright.config.ts](../frontend/playwright.config.ts)，测试位于
[tests/browser](../frontend/tests/browser)。Playwright 自动启动独立的 `127.0.0.1:5199` Vite 服务，
以无界面 Chromium 执行，结束后关闭服务与测试 context，不复用其他浏览器会话。
端口被占用时直接失败，不接入未知服务。浏览器 API 请求必须有显式夹具；未声明 API、外网请求、
未捕获 JS 异常均失败。API 夹具不使用通配返回空数组的兜底。

按变更范围执行，不要求每次小改全站重测：

```powershell
npm run test:browser -- controlled-layout.spec.ts
npm run test:browser -- controlled-qa.spec.ts
npm run test:browser -- controlled-identity.spec.ts
npm run test:browser -- controlled-composer.spec.ts
npm run test:browser:report
```

Vitest 仅发现 `src/**/*.test.{ts,tsx}`；浏览器测试单独发现、单独类型检查，不混入 jsdom 单测。

## 已覆盖的 42 个受控场景

| 范围 | 浏览器断言 | 数量 |
| --- | --- | --- |
| 七类任务输入 | 选择实际任务标签，输入问题，上传文本附件或 CSV，选资料库，提交请求匹配模板/数据/附件；受控 503 后保留输入且显示错误 | 7 |
| 身份 | 匿名无效/有效密钥通过真实登录表单，管理员/研究者/访客导航与深链限制，凭据变更后清除原会话展示 | 5 |
| 工作台布局 | 1504×993、1280×720、1024×768、901×700、900×700、760×800、390×844、320×640、1200×580；明/暗两套 | 18 |
| QA/Reader | 桌面/手机真实品牌头像有尺寸和背景、回答保留、刷新后恢复 | 4 |
| 停止与继续 | 正在运行请求→停止→刷新→保留历史→显式继续携带原始问题/来源/父消息且使用新请求 ID→完成后原轮仍停止 | 1 |
| SSE | localhost HTTP 未关闭前显示增量，收到 complete 后保留回答；请求失败/重试显示 2 次请求含 1 次重试，用量未知不冒充 0；重放及刷新不重复计数 | 1 |
| 工作台效率 | 桌面/手机明暗首屏完整看到输入；横向提示仅实际溢出时出现，键盘切换保持任务可见及输入 | 5 |
| 晚到契约 | 失败提示出现后任务确认异步增高，仍保持错误可见；用户回到输入后不抢焦点 | 1 |

布局场景检查展开配置、预检错误、页面上/中/下滚动、关键控件焦点与命中、长英文错误换行、
按钮在操作条内、页面没有横向溢出、配置栏/主内容/操作条互不覆盖。
边界坐标容许 1 CSS 像素的滚动舍入误差；不以放宽到部分可见取代错误完整可见。

## 本轮验证

- 受控浏览器基线：**42 passed**。
- 隔离真实 API：N9 的 **5 项通过**，含真实登录/归属隔离、PDF/CSV 上传解析、桌面/手机 PDF 第 2 页定位、真实 UI 下载与旧回执原字节验证。加入 [N0 人工验收入口](N0_FRONTEND_ACCEPTANCE_20261006.md) 后，完整真实 API 套件 **7 项通过**。
- 浏览器类型检查、ESLint、Python fixture Ruff 通过；加入 N0/N10 后前端完整 Vitest **68 文件 / 503 项通过**。
- 初次完整脚手架运行：20 通过、16 失败。原因是受控契约省略必需数组，以及断言误将 relative 的
  `top:auto` 计算值 `0px` 当故障；修正夹具和等价计算值后通过。没有因此降低业务质量门或改写业务组件。
- N11 调整后的完整回归曾暴露一处数据分析错误提示被晚到契约推到屏幕外的交互缺陷，已修复并添加确定性回归；首次 40/41 的失败不覆盖为首次成功。
- 没有调用付费模型；本结果不表示生产上线。

## 自动隔离真实 API 集成

```powershell
npm run test:browser:integration
```

[启动器](../frontend/scripts/browser-integration.mjs) 构建前端后，使用全新临时目录、SQLite、
真实 FastAPI lifespan 和文件渲染进程，固定监听 `127.0.0.1:5208`。继承环境只保留操作系统运行所需项，
不继承模型密钥/数据库配置，不读取 `.env`；测试身份均为合成身份。端口占用时失败，退出后关闭服务并
仅清理本次分配的临时目录。Python fixture 拒绝在未隔离目录中直接启动。

本层不拦截真实 API 响应。已完成研究和问答记录为明确的自有合成数据，模型执行入口在 wrapper 中禁止；
因此它验证真实认证、SQLite、上传解析、PDF.js、导出回执、下载及版本边界，不验证模型研究内容。
5 项结果位于 `artifacts/browser-integration/results.json`，另有 build/tests/server 日志与 PDF 定位截图。
首次初始化失败来自 fixture 控制路由被 SPA fallback 抢先匹配；修正路由优先级及 JSON 就绪检查后运行。
后续修正了隐藏侧栏标题的测试定位器，以及“2 页必然 2 片段”的不成立断言；上传现在核对实际解析包含目标原文。

结果保存于忽略的 `artifacts/browser-regression/`：`results.json`、HTML report、失败 trace、截图。
1504/390/320 宽布局保存当前视口及整页截图，QA/Reader 保存桌面/手机截图。
整页截图中的 sticky 顶栏位置与截图时的滚动位置有关；检查当前视口用 `viewport.png`。
每次完整运行会更新该目录；用于候选发布的证据应连同候选提交号另行归档。最终整合结果见 [前端发布验收](FRONTEND_RELEASE_ACCEPTANCE_20261006.md)：42 项受控、14 项真实 API 场景全部通过。
指定 `.spec.ts`、`--grep` 或 `--last-failed` 的范围运行另存各层的 `targeted/`，不覆盖完整回归 JSON/截图。

## 隔离真实 API 只读 smoke

仓库提供 [real-api.spec.ts](../frontend/tests/browser/real-api.spec.ts)，默认不发现、不运行。
先启动独立数据库和测试访问身份的后端，确保该 loopback 地址同时托管前端构建产物。
由安全环境注入 `DR_BROWSER_TEST_KEY`，禁止使用生产身份；本测试不读取 `.env`。

```powershell
$env:DR_BROWSER_REAL_API = '1'
$env:DR_BROWSER_BASE_URL = 'http://127.0.0.1:8008'
# DR_BROWSER_TEST_KEY 由测试环境注入；不要在命令或报告中输出。
npm run test:browser
Remove-Item Env:DR_BROWSER_REAL_API, Env:DR_BROWSER_BASE_URL
```

该模式拒绝非 loopback URL、不自动启动 Vite、不安装受控 API 响应，并关闭 trace/自动截图。
它验证真实匿名 401、测试身份登录、刷新后保留登录；浏览器阻止 API 写入和外网请求。
只读 smoke 不创建研究、问答、导出任务，也不验证已保存产物全链。

## 剩余工作

1. 七类任务成功执行后的各自专属产物阅读与格式验证。目前七类受控输入契约、实际上传解析和单个合成报告版本下载已覆盖，不冒充七类内容/输出全部验收。
2. 结构化大表和导图滚动、各类输出主要阅读路径；真实 PDF 来源记录选择与定位已经覆盖，全文与语义支持仍是其他验收层。
4. 中断与迟到写入需由真实服务配合验证；当前受控 SSE 检查浏览器呈现与请求恢复参数。
5. N11.1/N11.2 工作台首批已实施，见 [工作台设计记录](WORKBENCH_DESIGN_N11_20261006.md)；产物页面设计按范围继续。
