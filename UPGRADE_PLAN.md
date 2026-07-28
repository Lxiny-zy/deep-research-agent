# 简历项目升级方案(Resume Upgrade Plan)

> 目标:把本项目已有的两条稀缺资产——**证据链**与**故障恢复**——从"代码里有"升级为
> "一眼看得见、有数字可写进简历"。四个升级项互不耦合,均复用现有基础设施,总量约 4 天。
> 与第一个简历项目(SparkOffer:LangGraph + 记忆 + RAG + 个性化)完全错开,不重复。
>
> 执行顺序:**先完成全部 bug 修复并验证** → 3(chaos 依赖并行合并修复)→ 1 → 2 → 4。

---

## 升级项 1:证据卡片 UI——"可审计的研究报告"(纯前端,约 1 天)

**一句话卖点**:报告里每个引用都能点开,看到论断出自哪个网页的哪段原文、验证状态与内容哈希;
验证不过的论断进不了报告。

**现状**:`GET /api/runs/{id}` 的 `findings[]` 已返回完整证据数据
(`statement`、`evidence_quote`、`source_url`、`verification.status/semantic_status/consistency_status/source_content_hash`、矛盾 claim 链接)——见 `frontend/src/types.ts:47-49`。后端零改动。

**实施步骤**:
1. **引用可点击**:`ReportView.tsx` 渲染 markdown 时把 `[n]` 引用标记转成可点击元素
   (渲染后处理或 remark 插件),`n` 对应报告"参考来源"列表序号 → `source_url`。
2. **证据侧栏**:点击引用打开侧栏/浮层,按 `source_url` 匹配 findings,展示:
   论断 → 逐字 quote(高亮)→ 验证徽章(verified / conflicted / 语义状态)→ 哈希缩写;
   `conflicted` 论断渲染反向 claim 链接。
3. **证据链概览条**:报告头部显示 N 论断 / M verified / K conflicted / 来源拦截数
   (拦截数来自事件流中的 source policy 审计事件,复用 `EventTimeline` 的解析)。
4. 样式沿用 `design-system.css` 卡片语言;RunPage 测试:mock 详情响应,断言点击 `[1]`
   出现对应 quote。

## 升级项 2:对抗性评测——给护栏打出数字(约 1 天)

**一句话卖点**:自建对抗评测集,验证 Prompt 注入拦截率 X%、伪造引用拦截率 100%、矛盾标记召回 Y%。

**现状**:`tests/fakes.py` 的注入体系完整;`guardrails.py` 的
SourcePolicy → EvidenceVerifier → ClaimConsistencyVerifier 链可脱离 orchestrator 直接调用。

**实施步骤**:
1. 新建 `eval/adversarial.py` + `eval/adversarial_cases.py`,三类用例各 10~20 条:
   - **注入类**:Source 的 title/content/URL query 埋中英文注入指令(现有检测信号词 + 变体);
   - **伪引用类**:Finding.evidence_quote 与 source content 不匹配(截断/改写/拼接);
   - **矛盾类**:两个 source 互斥论断(数字/结论相反)。
2. 执行器用 FakeSearchTool 喂毒源、FakeLLM 返回预设 findings,跑**真实** guardrails 链。
3. 输出表:注入拦截率 / 伪引用拦截率 / 矛盾标记召回,每条附命中的策略原因(审计事件已有)。
   伪引用类理论上 100%——若不是,那本身是要修的 bug。
4. 并入 pytest 断言率值下限防回归;README 亮点区加一行结果。

**已完成(2026-07-26),实测数字**:`python -m eval.adversarial`(20 注入 + 13 伪引用 + 2 对照
+ 10 矛盾对)——**注入拦截 20/20 = 100%、伪引用拦截 13/13 = 100%(对照逐字引用正确放行 2/2)、
矛盾标记召回 10/10 = 100%**。过程亮点:评测首轮暴露 4 类真实绕过(英文同义词、零宽字符拆词、
中文把字句语序、URL 连字符路径),随后在 SourcePolicy 中以归一化 + 规则扩展修复并配防误报
反例测试——"用对抗评测发现并修复绕过"本身即是面试叙事。

**V2 已完成(2026-07-28)**：增加多来源交叉印证门禁与 4 组关系对抗场景。关系模型只提出候选，
程序按 registrable domain 去重独立发布方并让矛盾优先；有效佐证传播 **3/3 = 100%**，同发布方
子域/IDN 别名与直接/关联冲突绕过拦截 **4/4 = 100%**。前端支持全局及 per-run 严格双源开关，
证据侧栏展示 `single_source / corroborated / disputed` 状态和反向来源链接。

## 升级项 3:Chaos 恢复演示——kill -9 后死而复生(约 1 天)

**一句话卖点**:研究跑一半强杀进程,重启后 X 秒内自动接管续跑,断点续跑节省 Y% token。

**前置**:`workflow.py` 并行合并丢数据 bug 必须已修复(chaos 恢复场景正是其触发面)。

**实施步骤**:
1. 新建 `scripts/chaos_demo.py`(或 Makefile target):
   - 起 API 子进程 → `POST /api/runs` 提交多层工作流(fakes 里塞 `asyncio.sleep` 保证跑到中间层);
   - 事件流出现第 2 层节点开始时 `proc.kill()`;
   - 重启 API → 启动恢复扫描接管 → 轮询到终态;
   - 全程记录 kill 时刻、恢复接管时刻(恢复 attempt 首个事件)、各节点"续跑跳过 / 重执行"。
2. 输出两个数字:**接管耗时**(重启→首个恢复事件)与**续跑节省**(跳过节点 token 占比)。
3. 可视化零成本:前端 RunPage 回放该 run 的事件时间线,断点两侧 attempt 清晰可见。
   面试现场演示脚本 + 前端回放。

**已完成(2026-07-26),实测数字**:`make chaos-demo` 一键复现——deep 工作流跑到第 3 层节点
时硬杀 API 进程,重启后 **1.8s** 自动接管(租约 TTL 120s 的防脑裂等待单独标注,不计入),
planner/researcher 两层断点续跑跳过,仅重执行后两层,**断点续跑节省 75% token 重复调用**
(对照 run 全量 8000,恢复后仅新增 2000)。实现:`scripts/chaos_demo.py` +
`DR_DEMO_FAKE_BACKENDS` 演示钩子(默认零行为变化)。

## 升级项 4:编排对照实验——从功能列表到实验结论(约 0.5~1 天)

**一句话卖点**:实验表明 reviewed 相比 quick 质量 +X%、成本 ×Y;动态编排在简单问题上省 Z% token。

**现状**:`eval/run_eval.py` 已支持多工作流对照与相对差输出;缺墙钟维度、预算维度和足量用例。

**实施步骤**:
1. `EvalRow` 加墙钟时间(`time.monotonic` 包住 `agent.run`);CLI 加 `--budget` 透传 per-run
   params,跑"同一工作流不同预算下的质量曲线"。
2. `--workflows deep,quick,reviewed,auto,teams` 全量对照;`eval/dataset.py` 扩到 10+ 用例,
   覆盖简单事实 / 复杂多面 / 时效敏感三类问题。
3. 结果落 `eval/results/<date>.md`(明细表 + 汇总 + 两行结论),README/简历直接引用。
   判分用真实 LLM judge,跑前确认 `runtime_config.json` 模型配置。

**框架已就绪(2026-07-26),产出真实数字的推荐命令**:

```bash
# 全量五工作流对照(deep 为基线),结果写入 eval/results/<date>.md
python -m eval.run_eval --workflows deep,quick,reviewed,auto,teams --output

# 同一工作流不同预算下的质量对照
python -m eval.run_eval --workflows deep --budget 15000 --output eval/results/deep-b15k.md
python -m eval.run_eval --workflows deep --budget 30000 --output eval/results/deep-b30k.md
python -m eval.run_eval --workflows deep                 --output eval/results/deep-nolimit.md

# 动态编排省 token 结论(auto vs deep,默认组合)
python -m eval.run_eval --output
```

---

## 简历叙事建议

- **两项目定位区隔**:项目一 = 用成熟框架(LangGraph)做产品闭环(记忆/RAG/个性化);
  本项目 = 自研 Agent Runtime 讲基础设施(编排引擎、DAG 调度、checkpoint 恢复、租约 fencing、
  证据门禁)。面试必问"为什么不用 LangGraph",这个对比就是答案。
- **降权共同点**:SSE 流式、LLM-as-judge、FastAPI+React 两个项目都有,本项目条目里降权或换角度
  (judge 是"编排策略对照实验的度量工具"而非亮点本身)。
- **升级完成后可写的行**:
  - 可审计报告:引用点开即见逐字证据原文 + 程序验证状态 + 矛盾标记;
  - 对抗评测:注入拦截 _%、伪引用拦截 _%、矛盾召回 _%(跑完填数);
  - 可靠性:kill -9 后 _s 自动接管,断点续跑节省 _% token(跑完填数);
  - 编排取舍:五种工作流 × 质量/成本/耗时对照结论(跑完填数)。
- **不要做**(与项目一撞车):向量库/RAG 检索后端、长期记忆与画像、human-in-the-loop 中断、
  继续堆前端页面。
