# feat-ai-researcher

Apevon Science「AI 研究员」功能仓库。Vela Agent 的 `repo_url` 指向这里,任务启动时由 Vela `git clone --depth=1` 拉取。

## 内容

`.claude/skills/` 是唯一的技能层。每个技能都是一个自包含的真实目录(`SKILL.md` + 按需的 `references/`、`templates/`、`scripts/`),没有软链、没有中间层:

| 技能 | 干什么 |
|---|---|
| `ai4s-agent` | 顶层元 SKILL:方向/选题 → 综述 → 实验 → 论文的编排剧本 |
| `research-explorer` | 选题探索:候选选题矩阵 + 预调研文献 |
| `literature-survey` | 综述生成(LaTeX/PDF) |
| `experiment-suite` | 实验包:设计 + 可运行代码 + 结果 + 发表级图 |
| `paper-writer` | 研究论文(LaTeX/PDF),含 `scripts/figure-studio/` 主框架图一次性出图 |
| `image-gen` | 通用出图:一个提示词 → 一张图 |
| `docx` / `pdf` / `pptx` / `xlsx` | 文档交付 |
| `_shared/references/` | 跨技能详规(交付契约、长任务窗口) |

规则入口是根目录 `AGENTS.md`(运行环境、目录约定、图的两条路径、交付底线),详规由它点名指向 `_shared/references/`。

## 目录

- `work/<slug>/<stage>/` —— 过程文件(代码、LaTeX 工程、脚本、日志、提示词)
- `output/<slug>/<stage>/` —— 成品(PDF、图、DOCX、HTML、model、`*-sources.zip`)

## 维护

本仓即唯一事实源:直接改 `.claude/skills/` 下的文件 → commit + push,下一个 Vela 任务 clone 到的就是新版。

> 历史:①2026-07-21 前技能内容从 `cap-*` 独立仓 vendor 同步,该机制已移除;②2026-08-20 前技能藏在 `caps/cap-*/.claude/skills/` 下、由 `.claude/skills/` 软链暴露,现已全部提为真实目录;③人在环的 `cap-paper-framework-figure-studio`(S0–S5 候选评选,39MB assets,无人值守任务里结构上跑不完)同时移出,存档在分支 `archive/paper-framework-figure-studio`,出图能力由 `image-gen` 覆盖。