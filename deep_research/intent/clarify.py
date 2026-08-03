"""澄清判定：什么时候应该反问，而不是猜着往下跑。

**为什么需要它**：级联的弃权（``unknown`` / 低置信）目前一律走默认流程。
这在「问题本身清楚、只是分类器没把握」时是对的，但在「问题本身就有歧义」
时是错的——用默认流程研究一个歧义问题，产出的报告大概率答非所问，
用户还得从头再来一次。反问一句的成本远低于跑完一次错的研究。

**澄清的门槛必须高**：每一次反问都是把工作推回给用户，是体验成本。
因此这里的设计原则与风险规则一致——**宁可漏问不可乱问**：
  1. 只在**同时**满足「置信度低」与「有具体的歧义信号」时才澄清；
  2. 光是置信度低不澄清（那只说明分类器没把握，不代表问题有歧义）；
  3. 拒识优先于澄清（被拒的请求没有澄清的必要）；
  4. 多轮消解已经成功时不澄清（上下文已经消歧了）。

**为什么给候选选项而不只给问题**：开放式追问（「你想问什么？」）把认知负担
全推回用户。给出候选解读，用户一次点选就能消歧——这是澄清的价值所在。
"""

from __future__ import annotations

import re

from .model import normalize
from .types import ClarificationRequest, IntentDecision, IntentSlots

# 低于此置信度才考虑澄清。与 MIN_ROUTING_CONFIDENCE 分开定义：
# 「不确定到不敢路由」（0.6）与「不确定到要反问用户」是两个不同的门槛，
# 后者必须更严格——路由错了只是流程不最优，乱问则是直接骚扰用户。
MAX_CLARIFY_CONFIDENCE = 0.45

# 过短的输入几乎必然缺信息，但也可能是完整的专有名词查询（"RAG"）。
# 因此长度只作为**必要条件之一**，不单独触发。
MIN_SELF_SUFFICIENT_CHARS = 6

# --- 歧义信号：问题本身模糊，而不是分类器没把握 ---
#
# 这里只保留**显式表达了「我没想清楚」**的句式。曾经还有一条 `bare_topic`
# （匹配任意 12 字以内的短语），它把「向量数据库」「测试问题」这类裸名词短语
# 全部判为需要澄清——听起来合理，实际是灾难：短查询在真实流量里占比极高，
# 而「裸名词」与「简洁但明确的提问」在正则层面无法区分。结果是大面积打扰，
# 与本模块声明的「宁可漏问不可乱问」完全相反。
#
# 现在的取舍：只在用户**自己说了「看看」「随便」**这类无方向措辞时才问。
# 代价是「向量数据库」这类裸主题不会被澄清（走默认流程研究一遍），
# 这是刻意选择的失败方向——多跑一次研究的代价，远低于打断一个本来清楚的用户。
_AMBIGUITY_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "vague_directive",
        "想了解哪个方面？",
        # 「看看」「研究下」后面没有具体对象
        re.compile(
            r"^(?:帮我)?(?:看看|瞧瞧|研究一?下|了解一?下|分析一?下|调研一?下)"
            r"[^。！？\n]{0,8}$"
            r"|^(?:随便|随意)"
        ),
    ),
    (
        "underspecified_comparison",
        "希望从哪个维度对比？",
        # 只在**说了要对比但没说对比谁**时才问。早期版本匹配任意「区别/对比」，
        # 于是「Rust 和 Go 的区别」这种再清楚不过的提问也会被反问维度——
        # 用户已经指明了对象，缺维度不构成歧义（默认全维度对比即可）。
        # 因此要求：有对比动词，且**没有**并列连词把两个对象串起来。
        re.compile(
            r"^(?:帮我|请)?(?:对比|比较)(?:一?下)?[^。！？\n]{0,4}$"
            r"|^(?:选|用)哪(?:个|种)(?:好|比较好)?[？?]?$"
            r"|^(?:哪个更好|怎么选|如何选择)[？?]?$"
        ),
    ),
)

# 有这些就说明用户已经给了足够的方向，不该再问。
_HAS_DIRECTION = re.compile(
    r"(?:成本|性能|安全|价格|速度|效率|原理|机制|趋势|现状|优劣|适用|场景|"
    r"如何|怎么|为什么|哪些|多少|是什么)"
)


def _ambiguity_signal(query: str) -> tuple[str, str] | None:
    """返回 (歧义 code, 建议追问)。没有具体歧义信号则 None。"""
    normalized = normalize(query)
    if _HAS_DIRECTION.search(normalized):
        # 用户已经指明了关注方向，即便分类器没把握也不该反问。
        return None
    for code, question, pattern in _AMBIGUITY_PATTERNS:
        if pattern.search(normalized):
            return code, question
    return None


def _comparison_options(slots: IntentSlots) -> list[str]:
    """对比类歧义的候选维度。有实体时把实体带进选项，让追问更具体。"""
    subject = "、".join(slots.entities[:2]) if slots.entities else ""
    prefix = f"{subject}的" if subject else ""
    return [f"{prefix}性能与效率", f"{prefix}成本与投入", f"{prefix}生态与易用性", "以上都要"]


def plan_clarification(
    decision: IntentDecision, query: str
) -> ClarificationRequest | None:
    """判断是否需要澄清；需要则给出问题与候选选项。

    四道闸门（任一不满足就不澄清）：拒识优先、上下文已消解、置信度足够低、
    存在具体歧义信号。
    """
    if decision.blocked:
        return None
    if decision.context_resolved:
        # 多轮消解成功意味着上下文已经把话补全了，此时再问就是明知故问。
        return None
    if decision.confidence > MAX_CLARIFY_CONFIDENCE:
        return None

    signal = _ambiguity_signal(query)
    if signal is None:
        return None
    code, question = signal

    # 极短输入若已抽到实体，说明用户至少指明了对象，降级为不澄清。
    if len(normalize(query)) < MIN_SELF_SUFFICIENT_CHARS and decision.slots.entities:
        return None

    if code == "underspecified_comparison":
        options = _comparison_options(decision.slots)
    else:
        options = ["某项技术的原理", "几个方案的对比选型", "某个领域的现状调研", "最新进展与趋势"]

    return ClarificationRequest(
        question=question,
        options=options,
        reason=f"意图置信度 {decision.confidence:.2f} 且命中歧义信号 {code}",
    )
