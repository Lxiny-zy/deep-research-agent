# 06 — Global Rules（全局规则摘要）

注入到每个 Step 执行时的 system prompt 中，作为全局约束。

以下内容从 Vela 平台的 AGENTS.md 中提取并改造，去掉了平台特有字段：

---

## 运行环境

- 无人值守（headless）模式运行
- 绝不停下来等确认：step prompt 就是已批准工单；需要决策时自行决断并在产物中写明理由
- 研究不完整但已有结果置 `partial` 并说明缺口；只有明确不该执行才置 `skipped`
- 认证/存储/调度错误由执行器判定，Agent 不伪装成 `failed`

## 目录约定

一个任务一个 slug，两棵树同名同结构，严禁混用：

| 目录 | 内容 | 权限 |
|------|------|------|
| `work/<slug>/<stage>/` | 代码、LaTeX、`.bib`、脚本、数据、日志、中间态 | 可写 |
| `output/<slug>/<stage>/` | PDF、PNG、DOCX、HTML 等可直接打开的成品 | 可写 |
| `.framework/` | 编排状态 | 禁放产物/中间文件 |

- `<stage>` 为 `explore`/`survey`/`experiment`/`paper`/`final`
- 在 `work/` 构建，完成后复制到 `output/`
- Planner 必须给每个 step 写明具体输出路径

## 资料检索

- 默认免费路线：WebSearch → WebFetch → 立即写 bibliography
- 仅在需要真实被引数、DOI 或论文正文细节时，显式路由到学术检索
- 检索遇到 `429` 或缺少凭据时降级回 WebSearch/WebFetch，记录降级但不停工

## 交付规范

- 用户要可阅读文档但未指定格式时，同源交 `.md`/`.docx`/`.pdf`/`.html` 四份
- 该配图就配图，且本任务生成的图必须进入终稿
- 交付 `.md` 里不许出现裸 HTML 标签和页内锚点
- 正文引用写纯文本 `[N]`，链接只用绝对 http(s) URL

## 长任务

- 单 step 每窗口 4 小时、最多 3 个窗口，整个任务 48 小时
- 进度必须增量落盘，不要杀掉正常进程
- 续跑时从上次 checkpoint 继续，不从头重来
