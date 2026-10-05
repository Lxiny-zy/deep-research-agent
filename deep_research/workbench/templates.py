"""科研任务模板注册表。

每个模板描述一类科研任务的**完整契约**：用户要提供什么、系统承诺交付什么、
走哪条工作流、正文必须包含哪些章节、交付哪几种格式。模板是纯数据：

* 前端据此渲染任务入口（图标、说明、输入提示、示例、交付物清单）；
* API 据此把用户原话规整为研究任务契约（见 ``contract``）；
* 写作者角色据此拼提示词与章节骨架；
* 验收门据此检查「承诺的章节是否都在」「承诺的格式是否都产出了」。

把这些放在一处，是为了让「新增一类任务」只改一张表，而不是在
前端、路由、提示词、验收门四处各写一份会漂移的副本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DeliverableFormat = Literal["md", "docx", "pdf", "html", "pptx", "mindmap", "xlsx", "zip"]
InputKind = Literal["topic", "paper", "dataset"]
# 研究策略：任务「怎么找证据」，与任务「交付什么」正交。
#   none  —— 只用用户提供的材料（指定论文、上传数据），不做开放检索；
#   quick —— 规划后检索一轮即写作；
#   deep  —— 多子问题检索 + 逐字核验 + 反思补洞（原「深度研究」链路）。
Strategy = Literal["none", "quick", "deep"]

STRATEGY_LABELS: dict[str, tuple[str, str]] = {
    "none": ("不检索", "只使用你提供的材料"),
    "quick": ("快速检索", "规划后检索一轮即写作，省时省 token"),
    "deep": ("深度检索", "多子问题并行检索、逐字核验证据、反思补洞"),
}


@dataclass(frozen=True)
class SectionSpec:
    """正文必须出现的一个章节。``aliases`` 用于验收时容忍等价标题。"""

    key: str
    title: str
    guidance: str
    aliases: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class TaskTemplate:
    key: str
    title: str
    tagline: str
    description: str
    icon: str
    # 策略 → 工作流名。第一个键之外的顺序无意义；默认策略见 ``default_strategy``。
    strategies: dict[str, str]
    default_strategy: Strategy
    input_kind: InputKind
    input_label: str
    input_placeholder: str
    sections: tuple[SectionSpec, ...]
    deliverables: tuple[DeliverableFormat, ...]
    examples: tuple[str, ...] = ()
    # 写作者提示词里追加的任务特定要求（风格、打分、语言等）。
    writer_brief: str = ""
    # 正文最少字数（中文按字、英文按词近似）；0 = 不设下限。
    min_length: int = 0
    # 需要至少多少条已验证引用才算合格；0 = 允许无引用（如数据分析）。
    min_citations: int = 0
    accepts_attachments: bool = False
    tier_default: Literal["light", "standard", "deep"] = "standard"
    tags: tuple[str, ...] = field(default_factory=tuple)
    # Whether a selected private-library project is consumed by this task.
    # Specialised paper/data workflows use their own fixed inputs instead.
    supports_library: bool = True

    @property
    def workflow(self) -> str:
        """默认策略对应的工作流。"""
        return self.strategies[self.default_strategy]

    def workflow_for(self, strategy: str | None) -> str:
        """按策略取工作流；不支持的策略由调用方先用 ``supports`` 拦下。"""
        return self.strategies[strategy or self.default_strategy]

    def supports(self, strategy: str | None) -> bool:
        return strategy is None or strategy in self.strategies

    def section_titles(self) -> list[str]:
        return [section.title for section in self.sections]

    def to_public(self) -> dict[str, object]:
        """前端与 API 可见的模板描述（不含内部提示词细节）。"""
        return {
            "key": self.key,
            "title": self.title,
            "tagline": self.tagline,
            "description": self.description,
            "icon": self.icon,
            "workflow": self.workflow,
            "strategies": [
                {
                    "key": key,
                    "label": "仅指定文献"
                    if self.key == "litReview" and key == "none"
                    else "仅指定材料"
                    if self.key in {"slides", "mindmap"} and key == "none"
                    else STRATEGY_LABELS[key][0],
                    "description": "比较上传文件与指定论文，不补充外部文献"
                    if self.key == "litReview" and key == "none"
                    else "整理上传文件与指定链接，不补充外部资料"
                    if self.key in {"slides", "mindmap"} and key == "none"
                    else STRATEGY_LABELS[key][1],
                    "workflow": name,
                }
                for key, name in self.strategies.items()
            ],
            "default_strategy": self.default_strategy,
            "input_kind": self.input_kind,
            "input_label": self.input_label,
            "input_placeholder": self.input_placeholder,
            "sections": [
                {"key": s.key, "title": s.title, "guidance": s.guidance, "required": s.required}
                for s in self.sections
            ],
            "deliverables": list(self.deliverables),
            "examples": list(self.examples),
            "accepts_attachments": self.accepts_attachments,
            "tier_default": self.tier_default,
            "min_citations": self.min_citations,
            "tags": list(self.tags),
            "supports_library": self.supports_library,
        }


_DOC_SUITE: tuple[DeliverableFormat, ...] = ("md", "docx", "pdf", "html")

AUTO_RESEARCH = TaskTemplate(
    key="autoResearch",
    title="课题调研",
    tagline="围绕一个开放问题检索、核验证据并给出带引用的调研报告",
    description=(
        "适合开题、选题和技术路线摸底：拆解问题检索文献，逐条核对原文证据，"
        "给出现状、分歧与结论。检索深度可选快速或深度。"
    ),
    icon="network",
    strategies={"deep": "research", "quick": "research_quick"},
    default_strategy="deep",
    input_kind="topic",
    input_label="研究问题",
    input_placeholder="例如：快照式高光谱成像中，深度展开网络相比端到端网络的优势与局限？",
    sections=(
        SectionSpec("summary", "摘要", "3–5 句给出核心结论", ("概述", "执行摘要", "Summary")),
        SectionSpec("analysis", "分析", "按主题展开，每段都有引用", ("详细分析", "主题分析")),
        SectionSpec("conclusion", "结论", "回答原始问题并指出不确定性", ("总结", "结论与建议")),
    ),
    deliverables=_DOC_SUITE,
    examples=(
        "CASSI 系统中编码孔径失配对重建质量的影响有哪些量化研究？",
        "2024–2026 年扩散模型用于光谱重建的代表工作与瓶颈",
    ),
    min_length=600,
    min_citations=3,
    tier_default="deep",
    tags=("检索", "综合"),
)

LIT_REVIEW = TaskTemplate(
    key="litReview",
    title="文献综述",
    tagline="按主题组织、对比主流方法、给出开放问题与参考文献列表",
    description=(
        "围绕一个主题撰写结构化、引用充分的文献综述：梳理研究脉络，"
        "按主题对比主要方法，最后总结开放问题并列出参考文献。"
    ),
    icon="library",
    strategies={"deep": "lit_review", "quick": "lit_review_quick", "none": "lit_review_provided"},
    default_strategy="deep",
    input_kind="topic",
    input_label="综述主题",
    input_placeholder="例如：面向快照光谱成像的深度展开重建方法",
    sections=(
        SectionSpec("abstract", "摘要", "综述范围、检索口径与主要发现", ("Abstract",)),
        SectionSpec("introduction", "引言", "研究背景、动机与综述边界", ("背景", "Introduction")),
        SectionSpec("themes", "主题综述", "按主题分节，每节对比代表工作", ("研究现状", "方法综述")),
        SectionSpec("comparison", "方法对比", "用表格或段落横向比较主流方法", ("对比分析", "比较")),
        SectionSpec("open_problems", "开放问题", "尚未解决的问题与未来方向", ("未来方向", "挑战")),
        SectionSpec("conclusion", "结论", "收束全文", ("总结",)),
    ),
    deliverables=_DOC_SUITE,
    examples=(
        "高光谱图像压缩感知重建：从优化方法到深度展开",
        "计算光谱成像中的衍射光学元件（DOE）端到端设计综述",
    ),
    writer_brief=(
        "以学术综述文体写作：按主题而不是按论文逐篇罗列；每个主题说明问题、"
        "代表方法、各自假设与局限；方法对比优先用 evidence-table 结构化表格规格。"
    ),
    min_length=1500,
    min_citations=6,
    accepts_attachments=True,
    tier_default="deep",
    tags=("综述", "写作"),
)

PEER_REVIEW = TaskTemplate(
    key="peerReview",
    title="同行评审",
    tagline="以严格审稿人视角给出摘要、优缺点、详细意见与 1–10 分推荐",
    description=(
        "针对一篇论文（arXiv 链接、DOI、PDF 或摘要文本）撰写严格的同行评审意见，"
        "包含五个固定部分，并给出 1–10 分的总体评分。"
    ),
    icon="shield",
    strategies={"none": "peer_review"},
    default_strategy="none",
    input_kind="paper",
    input_label="待评审论文",
    input_placeholder="粘贴 arXiv 链接 / DOI / 论文摘要，例如 https://arxiv.org/abs/2501.12705",
    sections=(
        SectionSpec("summary", "论文摘要", "用自己的话概括问题、方法与主要结论", ("Summary",)),
        SectionSpec("strengths", "优点", "逐条列出，每条说明为什么是优点", ("Strengths",)),
        SectionSpec("weaknesses", "不足", "逐条列出，区分根本性与可修复问题", ("Weaknesses",)),
        SectionSpec(
            "comments", "详细意见", "按章节给出具体可操作的修改建议", ("Detailed Comments",)
        ),
        SectionSpec(
            "recommendation",
            "总体推荐",
            "给出接收建议与 1–10 分评分及理由",
            ("Recommendation", "总体评价"),
        ),
    ),
    deliverables=("md", "docx", "pdf", "html"),
    examples=("https://arxiv.org/abs/2205.10102", "https://arxiv.org/abs/2501.12705"),
    writer_brief=(
        "依据用户指定的评审目的与评价维度，给出严格、公正的意见。用户未指定投稿场景时，"
        "不自行套用顶会录用门槛；说明本次评分依据。所有事实判断必须指向论文中的具体内容。"
        "区分原文证实的缺陷、需作者澄清的问题和建议增加的验证；"
        "未核实的信息不能作为确定缺陷或扣分事实，建议补充不等于原文没有。"
        "复用既有模块本身不是缺陷；先明确哪些组件沿用、哪些改动由作者提出，再评价增量及其证据。"
        "每项重要意见说明证据、对结论的影响和可执行的修改方法。"
        "摘要与实验复述保持简洁，不重复搬运全部数据表；只列支持具体评审判断所必需的比较。"
        "评分用「评分：N/10」的格式单独成行，N 为 1–10 的整数。"
    ),
    min_length=800,
    min_citations=1,
    accepts_attachments=True,
    tier_default="standard",
    tags=("评审", "论文"),
    supports_library=False,
)

PAPER_READ = TaskTemplate(
    key="paperRead",
    title="论文精读",
    tagline="一句话总结、核心贡献、方法、结果、局限与摘要忠实翻译",
    description=(
        "对一篇论文做结构化精读：提炼贡献与方法，复述主要结果，"
        "指出局限与未解问题，并给出摘要的忠实中文翻译。数学公式用 LaTeX 渲染。"
    ),
    icon="book",
    strategies={"none": "paper_read"},
    default_strategy="none",
    input_kind="paper",
    input_label="待精读论文",
    input_placeholder="粘贴 arXiv 链接 / DOI / PDF 地址，可附上你关心的问题",
    sections=(
        SectionSpec("one_liner", "一句话总结", "一句话说清这篇论文做了什么", ("TL;DR",)),
        SectionSpec("contributions", "核心贡献", "逐条列出作者声称的贡献", ("贡献",)),
        SectionSpec("method", "方法", "关键模型、公式与设计选择", ("方法概述",)),
        SectionSpec("results", "主要结果", "关键实验与数值结果", ("实验结果", "结果")),
        SectionSpec("limitations", "局限与未解问题", "作者承认与你发现的局限", ("局限",)),
        SectionSpec(
            "translation", "摘要翻译", "忠实、完整的中文翻译", ("摘要的忠实中文翻译", "摘要译文")
        ),
    ),
    deliverables=("md", "docx", "pdf", "html"),
    examples=("https://arxiv.org/abs/2205.10102 重点看误差是如何建模的",),
    writer_brief="数学公式用 $...$ 或 $$...$$ 的 LaTeX 形式；摘要翻译必须逐句对应原文，不增删。",
    min_length=800,
    min_citations=1,
    accepts_attachments=True,
    tier_default="standard",
    tags=("精读", "论文"),
    supports_library=False,
)

DATA_ANALYSIS = TaskTemplate(
    key="dataAnalysis",
    title="数据分析",
    tagline="分析计划、统计检验、可视化图表与中文分析报告",
    description=(
        "上传或粘贴表格数据（CSV / TSV），说明分析问题。系统给出分析计划，"
        "执行描述统计与显著性检验，生成图表并撰写中文分析报告。"
    ),
    icon="chart",
    strategies={"none": "data_analysis"},
    default_strategy="none",
    input_kind="dataset",
    input_label="数据与分析问题",
    input_placeholder="第一行写分析问题，下面粘贴 CSV 数据（含表头）",
    sections=(
        SectionSpec("plan", "分析计划", "要回答的问题与采用的方法", ("Analysis Plan",)),
        SectionSpec("data", "数据概况", "样本量、变量与缺失情况", ("数据描述",)),
        SectionSpec("statistics", "统计结果", "描述统计与检验结果（含 p 值）", ("统计分析",)),
        SectionSpec("figures", "图表", "每张图的含义与口径", ("可视化",)),
        SectionSpec("findings", "结论", "用中文总结发现与局限", ("发现", "总结")),
    ),
    deliverables=("md", "docx", "pdf", "html", "xlsx"),
    examples=("比较三种重建方法在 10 个场景上的 PSNR 是否有显著差异",),
    writer_brief="只解释给定统计结果中的数字，不得自行编造或改写数值。",
    # 数据分析的篇幅取决于数据本身（一张两列的小表就只有一项检验），
    # 用字数下限衡量它只会把规模小但完整的分析误报为「需关注」。
    min_length=0,
    min_citations=0,
    accepts_attachments=True,
    tier_default="light",
    tags=("数据", "统计"),
    supports_library=False,
)

SLIDES = TaskTemplate(
    key="slides",
    title="幻灯片",
    tagline="从主题、论文或研究结果生成可直接演示的 PPTX（含演讲备注）",
    description=(
        "围绕主题或论文生成一份逻辑完整的中文演示文稿：标题页、背景与动机、"
        "问题与目标、方法、关键结果、讨论与局限、结论、Q&A。每页 3–5 条要点并附演讲备注。"
    ),
    icon="presentation",
    strategies={"quick": "slides", "deep": "slides_deep", "none": "slides_provided"},
    default_strategy="quick",
    input_kind="topic",
    input_label="演示主题或论文",
    input_placeholder="例如：组会汇报——深度展开网络在 CASSI 重建中的最新进展",
    sections=(
        SectionSpec("title", "标题页", "题目、汇报人占位与日期"),
        SectionSpec("background", "背景与动机", "为什么这个问题重要"),
        SectionSpec("problem", "问题与目标", "要解决什么"),
        SectionSpec("method", "方法", "核心思路"),
        SectionSpec("results", "关键结果", "最重要的证据与数字"),
        SectionSpec("discussion", "讨论与局限", "边界与风险"),
        SectionSpec("conclusion", "结论", "三点带走的信息"),
        SectionSpec("qa", "Q&A", "预判的问题"),
    ),
    deliverables=("pptx", "md"),
    examples=("面向本科生的高光谱成像入门报告（15 分钟）",),
    writer_brief="每页 3–5 条简洁要点，避免大段文字；每页都写 2–4 句演讲备注。",
    min_citations=1,
    accepts_attachments=True,
    tier_default="standard",
    tags=("演示", "PPT"),
)

MINDMAP = TaskTemplate(
    key="mindmap",
    title="思维导图",
    tagline="把一个主题的知识结构整理为可交互的思维导图（HTML / PNG / Markdown）",
    description=(
        "按主题范围组织核心概念、已核验结论与待研究问题，分支与深度由内容决定，"
        "事实节点保留引用，交付可交互 HTML、静态 PNG 与完整 Markdown 大纲。"
    ),
    icon="mindmap",
    strategies={"quick": "mindmap", "deep": "mindmap_deep", "none": "mindmap_provided"},
    default_strategy="quick",
    input_kind="topic",
    input_label="导图主题",
    input_placeholder="例如：Transformer 架构知识图谱——注意力、位置编码、缩放定律与主要变体",
    sections=(),
    deliverables=("mindmap", "md"),
    examples=("高光谱计算成像的知识体系：光学编码、重建算法、数据集与评价",),
    writer_brief="覆盖用户指定范围，不凑节点数；区分概念、事实与问题，事实绑定引用，关系与层级清晰。",
    min_citations=0,
    tier_default="light",
    tags=("导图", "知识结构"),
)

TASK_TEMPLATES: dict[str, TaskTemplate] = {
    template.key: template
    for template in (
        AUTO_RESEARCH,
        LIT_REVIEW,
        PEER_REVIEW,
        PAPER_READ,
        DATA_ANALYSIS,
        SLIDES,
        MINDMAP,
    )
}

DEFAULT_TEMPLATE = AUTO_RESEARCH.key
# 同一工作流只归属一个模板；deep / quick 这类通用链路归「课题调研」。
TEMPLATE_BY_WORKFLOW: dict[str, TaskTemplate] = {
    name: template for template in TASK_TEMPLATES.values() for name in template.strategies.values()
}


def get_template(key: str | None) -> TaskTemplate | None:
    if not key:
        return None
    return TASK_TEMPLATES.get(key)


def template_for_workflow(workflow: str | None) -> TaskTemplate | None:
    if not workflow:
        return None
    return TEMPLATE_BY_WORKFLOW.get(workflow)


def public_templates() -> list[dict[str, object]]:
    return [template.to_public() for template in TASK_TEMPLATES.values()]


__all__ = [
    "DEFAULT_TEMPLATE",
    "TASK_TEMPLATES",
    "DeliverableFormat",
    "STRATEGY_LABELS",
    "SectionSpec",
    "Strategy",
    "TaskTemplate",
    "get_template",
    "public_templates",
    "template_for_workflow",
]
