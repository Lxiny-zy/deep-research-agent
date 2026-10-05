"""交付质量策略：所有可在前端「设置 → 交付质量」里调整的质量阈值。

策略是**唯一真源**：
* ``Settings.quality`` 持有当前值，随 ``/api/config`` 读写与持久化；
* 创建运行时随运行设置一起冻结进 checkpoint，运行中途改设置不影响已开始的运行；
* 写作者按它决定返工次数，验收门按它判定 pass / warn / fail；
* ``QUALITY_FIELDS`` 同时是前端表单与悬浮说明的数据来源（``/api/config/quality-schema``），
  文档与界面不会各写一份说明而漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FieldKind = Literal["int", "bool"]


def effective_citation_minimum(requested: int, available: int) -> int:
    """Writing and delivery require the same attainable number of sources."""
    return min(max(0, requested), max(0, available))


@dataclass(frozen=True)
class QualityField:
    key: str
    label: str
    group: str
    kind: FieldKind
    help: str
    minimum: int = 0
    maximum: int = 0
    unit: str = ""


class QualityPolicy(BaseModel):
    """交付质量阈值。默认值面向严肃科研交付，偏严格。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    # ---- 引用与来源 ----
    survey_min_citations: int = Field(20, ge=1, le=200)
    research_min_citations: int = Field(3, ge=0, le=100)
    max_citation_cluster: int = Field(7, ge=2, le=20)
    max_evidence_quote_chars: int = Field(600, ge=60, le=2000)
    forbid_abstract_citations: bool = True
    recency_check: bool = True
    # ---- 文体与结构 ----
    register_check: bool = True
    max_sentence_chars: int = Field(160, ge=60, le=600)
    require_limitations: bool = True
    # ---- 返工与收敛 ----
    max_revisions: int = Field(2, ge=0, le=4)
    qa_claim_max_revisions: int = Field(2, ge=0, le=4)
    qa_max_revisions: int = Field(1, ge=0, le=4)
    qa_max_model_calls: int = Field(16, ge=1, le=128)
    extraction_max_revisions: int = Field(1, ge=0, le=4)
    review_evidence_rounds: int = Field(1, ge=0, le=4)
    fail_on_quality: bool = False
    min_evidence_findings: int = Field(3, ge=0, le=50)

    def min_citations_for(self, template_key: str, template_default: int) -> int:
        """模板的引用下限：综述与课题调研由策略覆盖，其它任务沿用模板。"""
        if template_key == "litReview":
            return self.survey_min_citations
        if template_key == "autoResearch":
            return self.research_min_citations
        return template_default


QUALITY_FIELDS: tuple[QualityField, ...] = (
    QualityField(
        "max_evidence_quote_chars",
        "证据引文长度上限",
        "引用与来源",
        "int",
        "每条事实只保留足以支持它的最短连续原文，默认最多 600 字。超长引文须从原文重新选择，"
        "保留必要的归属、条件和表头；不能通过截掉限定条件或拼接片段来缩短。",
        60,
        2000,
        "字",
    ),
    QualityField(
        "survey_min_citations",
        "综述引用下限",
        "引用与来源",
        "int",
        "文献综述至少要引用多少个不同的已核验来源。达不到时系统先扩大检索、"
        "再让写作者返工；仍不足则交付物标为「部分完成」并写明缺口，"
        "绝不用无关文献凑数。默认 20。",
        1,
        200,
        "篇",
    ),
    QualityField(
        "research_min_citations",
        "课题调研引用下限",
        "引用与来源",
        "int",
        "课题调研报告至少引用多少个不同的已核验来源。低于下限会触发返工，"
        "仍不足时在交付登记里标注。设为 0 表示不设下限。",
        0,
        100,
        "篇",
    ),
    QualityField(
        "max_citation_cluster",
        "单处引用上限",
        "引用与来源",
        "int",
        "同一处最多并列多少个引用角标，例如 [1, 2, 3]。超过即视为「引用堆砌」："
        "一句话同时挂很多来源，读者无法判断每个来源支撑了什么。"
        "超限会要求写作者拆分论断。",
        2,
        20,
        "个",
    ),
    QualityField(
        "forbid_abstract_citations",
        "摘要禁止引用",
        "引用与来源",
        "bool",
        "摘要 / 一句话总结部分不得出现 [n] 引用角标。摘要常被单独检索和转载，"
        "脱离正文后角标无从解析；来源归属应放在正文。",
    ),
    QualityField(
        "recency_check",
        "时效覆盖检查",
        "引用与来源",
        "bool",
        "任务里点名了年份（如「2025 年以来」）时，检查已核验来源中是否真有该年份及之后的文献。"
        "没有则判为不合格并触发补充检索，避免「问 2025 年、答的全是旧文献」。",
    ),
    QualityField(
        "register_check",
        "学术文体检查",
        "文体与结构",
        "bool",
        "检查口语化措辞、成对套话句式（如「不是……而是……」「既……又……」）、"
        "中文正文夹半角标点、以及「作为 AI」「用户上传」等生产过程描述。"
        "命中后要求写作者改写为有证据支撑的学术表述。",
    ),
    QualityField(
        "max_sentence_chars",
        "单句最大长度",
        "文体与结构",
        "int",
        "单个句子超过这个字数视为超长句，会提示拆分。超长句通常同时承载多个论断，"
        "读者难以分辨每个论断的证据。仅作为提醒，不单独判为不合格。",
        60,
        600,
        "字",
    ),
    QualityField(
        "require_limitations",
        "必须说明局限",
        "文体与结构",
        "bool",
        "综述、调研、精读与审稿必须明确写出局限、不确定性或未解决问题。"
        "只报成绩不报边界的科研交付物会误导读者。",
    ),
    QualityField(
        "max_revisions",
        "最多返工次数",
        "返工与收敛",
        "int",
        "质量检查不合格时，把问题清单交回写作者重写的最大次数。0 表示不返工、"
        "只标注问题。问答中仅计引用和数值等机械问题，断言问题另计轮数，"
        "两者还受问答自动修订总次数约束。"
        "每次返工都会消耗额外 token；报告返工后仍取问题最少的一版交付。",
        0,
        4,
        "次",
    ),
    QualityField(
        "qa_max_revisions",
        "问答自动修订总次数",
        "返工与收敛",
        "int",
        "整轮问答的局部修订总上限，默认一次；同时受机械和断言的分项上限约束。"
        "局部修订失败不会再启动整篇重写，保留可核验内容与待确认点。",
        0, 4, "次",
    ),
    QualityField(
        "qa_max_model_calls",
        "问答模型调用总上限",
        "返工与收敛",
        "int",
        "单轮问答内所有角色共用的模型请求次数上限，包含摘要、规划、抽取、核验和重试。"
        "到达上限不再请求模型，不会将未完成核验的草稿当作通过。",
        1, 128, "次",
    ),
    QualityField(
        "qa_claim_max_revisions",
        "问答断言修订轮数",
        "返工与收敛",
        "int",
        "问答中结论未得到引用支持时的修订次数，与补引用、修正数值的机械修订分别计数。"
        "0 表示仅核验不修订断言；用尽后保留可核验内容。仍受问答修订总次数和调用总上限约束。",
        0,
        4,
        "次",
    ),
    QualityField(
        "fail_on_quality",
        "质量不合格即判失败",
        "返工与收敛",
        "bool",
        "开启后，返工用尽仍有硬性问题（引用越界、缺章节、引用不足等）的运行判为失败，"
        "不交付正式格式；关闭时照常交付，但运行标为「部分完成」并在交付页列出问题。",
    ),
    QualityField(
        "extraction_max_revisions",
        "抽取候选修复轮数",
        "返工与收敛",
        "int",
        "仅针对未通过原文、数值或语义检查的候选进行定向修复，保留已通过的发现。"
        "0 表示只保存失败记录。修复仍须重新核验；遇到服务错误或没有实质修改时停止，"
        "不重新抽取全文。",
        0,
        4,
        "次",
    ),
    QualityField(
        "review_evidence_rounds",
        "综述关键证据补读轮数",
        "返工与收敛",
        "int",
        "仅指定文献综述在写作前逐篇核对任务所需证据；有缺口时优先补读相关章节，"
        "保留已有发现。0 表示只检查不补读。补读仍须核验，未覆盖关键内容时不交付正式综述。",
        0,
        4,
        "次",
    ),
    QualityField(
        "min_evidence_findings",
        "进入写作的最少证据数",
        "返工与收敛",
        "int",
        "检索类任务至少积累多少条通过逐字核验的发现才开始写作。"
        "不足时反思环节会继续补洞（受补洞轮数限制），而不是用极少的证据硬写一篇报告。",
        0,
        50,
        "条",
    ),
)


def quality_schema() -> list[dict[str, Any]]:
    """前端表单与悬浮说明的数据来源。"""
    defaults = QualityPolicy()
    return [
        {
            "key": field.key,
            "label": field.label,
            "group": field.group,
            "kind": field.kind,
            "help": field.help,
            "min": field.minimum if field.kind == "int" else None,
            "max": field.maximum if field.kind == "int" else None,
            "unit": field.unit,
            "default": getattr(defaults, field.key),
        }
        for field in QUALITY_FIELDS
    ]


def coerce_policy(value: Any) -> QualityPolicy:
    """从 Settings / 持久化值 / checkpoint 还原策略；非法值回退默认，不让坏配置炸穿运行。"""
    if isinstance(value, QualityPolicy):
        return value
    if isinstance(value, dict):
        try:
            return QualityPolicy.model_validate(value)
        except ValueError:
            return QualityPolicy()
    return QualityPolicy()


def policy_from(settings: Any) -> QualityPolicy:
    return coerce_policy(getattr(settings, "quality", None))


def writer_policy(value: Any, scratch: dict[str, Any]) -> QualityPolicy:
    policy = coerce_policy(value)
    metadata = scratch.get("_active_step_metadata")
    if isinstance(metadata, dict) and metadata.get("completion_repair") is True:
        # The original draft is the seed; its first correction is the one
        # additional completion-repair round, with no further writer loop.
        return policy.model_copy(update={"max_revisions": 0})
    return policy


def completion_feedback(scratch: dict[str, Any]) -> list[str]:
    metadata = scratch.get("_active_step_metadata")
    if not isinstance(metadata, dict) or metadata.get("completion_repair") is not True:
        return []
    return [item for item in metadata.get("completion_repair_issues", []) if isinstance(item, str)]


__all__ = [
    "QUALITY_FIELDS",
    "QualityField",
    "QualityPolicy",
    "coerce_policy",
    "policy_from",
    "quality_schema",
]
