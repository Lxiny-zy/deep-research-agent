"""评估用例集：每条给出问题与考察要点（供 judge 参考）。

覆盖三类问题，用于对照不同工作流/预算下的表现差异：
  - 简单事实型（fact-*）：答案明确、检索量小——考察轻量流程（quick）是否已够用，
    深流程在这类问题上多烧的 token 是否换来了质量；
  - 复杂多面型（multi-*）：需要多角度拆解与综合——深流程/评审流程的主场；
  - 时效敏感型（fresh-*）：答案随时间变化——考察检索时效与来源标注，快照型回答应扣分。
"""

from __future__ import annotations

from dataclasses import dataclass

# 分类常量：dataset 内自用 + 结果文档按类计数
CATEGORY_FACT = "简单事实型"
CATEGORY_MULTI = "复杂多面型"
CATEGORY_FRESH = "时效敏感型"


@dataclass
class EvalCase:
    id: str
    query: str
    notes: str = ""  # 理想答案应覆盖的要点（判分要点）
    category: str = ""  # 简单事实型 / 复杂多面型 / 时效敏感型


CASES: list[EvalCase] = [
    # --- 简单事实型：答案明确，考察「轻流程是否够用」 ---
    EvalCase(
        "fact-transformer-origin",
        "Transformer 架构是哪一年由谁提出的？出自哪篇论文？",
        "应给出 2017 年、Google 团队（Vaswani 等人）、论文《Attention Is All You Need》，"
        "并简述自注意力机制取代循环结构这一核心贡献。",
        CATEGORY_FACT,
    ),
    EvalCase(
        "fact-python-gil",
        "Python 的 GIL 是什么？为什么它会限制多线程的 CPU 密集任务？",
        "应解释全局解释器锁的作用（同一时刻仅一个线程执行字节码）、对 CPU 密集 vs IO 密集"
        "任务的不同影响，并提到 multiprocessing / free-threaded 构建等绕开手段。",
        CATEGORY_FACT,
    ),
    EvalCase(
        "fact-http3-quic",
        "HTTP/3 与 HTTP/2 的主要区别是什么？它基于什么传输协议？",
        "应指出 HTTP/3 基于 QUIC（UDP 之上），解决 TCP 队头阻塞，"
        "内建 TLS 1.3、连接迁移与更快握手（0-RTT）。",
        CATEGORY_FACT,
    ),
    EvalCase(
        "fact-moe-inference",
        "什么是混合专家（MoE）模型？它为什么能在参数量很大时仍降低推理成本？",
        "应解释稀疏激活（每 token 只路由到少数专家）、总参数量 vs 激活参数量的区别、"
        "路由器/负载均衡问题，并举出代表性 MoE 模型。",
        CATEGORY_FACT,
    ),
    # --- 复杂多面型：需要多角度拆解与综合，深流程的主场 ---
    EvalCase(
        "multi-agent-frameworks",
        "2026 年主流 AI Agent 框架有哪些？各自的设计取舍是什么？",
        "应覆盖 LangGraph / AutoGen / CrewAI / LlamaIndex 等，并比较编排模型、状态管理、生态。",
        CATEGORY_MULTI,
    ),
    EvalCase(
        "multi-rag-vs-longctx",
        "在长上下文模型变强的 2026 年，RAG 还有必要吗？",
        "应讨论成本、时效性、可溯源、私有数据等权衡，并给出场景化结论。",
        CATEGORY_MULTI,
    ),
    EvalCase(
        "multi-selfhost-vs-api",
        "企业在私有化部署开源大模型与调用商业 API 之间该如何选型？",
        "应比较成本结构（固定算力 vs 按量付费）、数据合规与隐私、运维复杂度、"
        "模型能力差距，并给出分场景建议（如敏感数据走私有化、长尾需求走 API）。",
        CATEGORY_MULTI,
    ),
    EvalCase(
        "multi-vector-db-choice",
        "生产环境选择向量数据库需要考虑哪些因素？主流方案如何取舍？",
        "应覆盖召回质量与索引类型（HNSW/IVF）、过滤与混合检索、规模与成本、运维复杂度，"
        "并对比 pgvector、专用向量库（Milvus/Qdrant/Weaviate 等）与托管服务的适用场景。",
        CATEGORY_MULTI,
    ),
    EvalCase(
        "multi-llm-code-eval",
        "如何科学评估大模型的编程能力？现有基准存在哪些缺陷？",
        "应覆盖 HumanEval / SWE-bench 等基准及其局限（数据污染、与真实工程任务脱节）、"
        "对战式评测（Arena/Elo）与真实任务评测的互补性，并给出可信评估的组合建议。",
        CATEGORY_MULTI,
    ),
    # --- 时效敏感型：答案随时间变化，考察检索时效与来源标注 ---
    EvalCase(
        "fresh-embodied-ai",
        "具身智能领域近一年有哪些值得关注的技术进展与代表团队？",
        "应覆盖 VLA 模型、数据采集、仿真到真实迁移、代表公司，且信息应是近一年内的。",
        CATEGORY_FRESH,
    ),
    EvalCase(
        "fresh-frontier-models",
        "近三个月主流大模型厂商发布了哪些重要的模型或产品更新？",
        "应给出具体厂商、版本号与发布时间，信息需在近三个月内且注明来源；罗列过时旧闻应扣分。",
        CATEGORY_FRESH,
    ),
    EvalCase(
        "fresh-open-weights",
        "最近半年开源权重大模型有哪些重要发布？彼此性能对比如何？",
        "应列出近半年的开源权重模型（名称/参数规模/许可证），并引用基准分数做横向对比，"
        "注明数据来源与测评时间。",
        CATEGORY_FRESH,
    ),
    EvalCase(
        "fresh-ai-regulation",
        "全球 AI 监管在近一年有哪些最新进展？欧盟 AI 法案的落地情况如何？",
        "应覆盖欧盟 AI 法案分阶段生效的时间线与义务分级、美国/中国等主要经济体的最新动向，"
        "以及对模型提供方与企业用户的实际影响。",
        CATEGORY_FRESH,
    ),
]
