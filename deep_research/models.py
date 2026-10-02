"""各 Agent 的结构化数据模型（Pydantic v2）。

把每个 Agent 的输入/输出都建模成 schema，是本项目「可靠性」的核心：
LLM 被强制产出符合 schema 的 JSON，下游可直接消费而无需脆弱的字符串解析。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SubQuestion(BaseModel):
    question: str = Field(..., description="一个可独立检索的子问题")
    rationale: str = Field("", description="为什么需要研究它")
    search_queries: list[str] = Field(
        default_factory=list,
        description="实际检索用的简洁关键词组；保留范围、年份与实体，不把完整问答说明当搜索词",
    )
    depends_on: list[int] = Field(
        default_factory=list,
        description="依赖的前驱子问题序号（本计划内 0 起始下标）；为空表示无依赖、可立即并行检索",
    )

    @field_validator("search_queries")
    @classmethod
    def clean_search_queries(cls, queries: list[str]) -> list[str]:
        return list(dict.fromkeys(query.strip() for query in queries if query.strip()))


class ResearchPlan(BaseModel):
    interpretation: str = Field(..., description="对原始问题的理解与边界界定")
    sub_questions: list[SubQuestion] = Field(default_factory=list)


class ScholarlyMetadata(BaseModel):
    """一条学术来源的出处元数据。

    存在的理由是「出处」在科学场景里不等于 URL：同一篇工作常同时存在预印本、
    期刊版与机构库三个 URL，而真正可引用、可去重、可判独立性的标识是 DOI 与
    ``work_id``。字段全部可选且默认空——抽不到就是抽不到，绝不补默认值，
    因为一个编造出来的年份或期刊比缺失更糟。

    ``peer_reviewed`` 刻意用三态 ``bool | None``：预印本平台缺少 journal_ref
    并不等于「未经评审」，只等于「不知道」。把未知压成 False 会让报告给出
    自己并不掌握的结论。
    """

    model_config = ConfigDict(extra="ignore")

    doi: str = ""
    work_id: str = Field("", description="OpenAlex / S2 的同一工作聚类 ID，用于跨库判定同一篇")
    authors: list[str] = Field(default_factory=list, max_length=32)
    affiliations: list[str] = Field(default_factory=list, max_length=32)
    venue: str = ""
    year: int | None = None
    version: str = Field("", description="预印本版本号，如 arXiv 的 v2")
    peer_reviewed: bool | None = Field(None, description="None＝未知，不是 False")
    retracted: bool | None = None
    citation_count: int | None = None
    # 本文引用的其它工作（OpenAlex work_id 列表）。与 citation_count 的区别是方向：
    # 后者只是「被引了多少次」这个标量，前者是有向边，能回答「哪篇方法基于哪篇」。
    # 综述最需要的正是这个结构。OpenAlex 在同一个响应里免费返回该字段，所以取它
    # 不增加任何 API 调用。上限防止一篇综述的数百条引用把单行元数据撑爆。
    referenced_works: list[str] = Field(default_factory=list, max_length=512)
    oa_pdf_url: str = Field("", description="可合法获取的开放全文 PDF；全文解析阶段消费")
    section: str = Field("", description="该证据取自哪一节；全文分节后填充")


class Source(BaseModel):
    title: str = ""
    url: str
    content: str = ""
    content_hash: str = ""
    # Generic page/section/paragraph locator. Unlike ``scholarly.section`` it
    # also applies to uploaded reports, internal notes and ordinary web pages.
    locator: str = ""
    # Structured section boundaries, unlike a display locator, establish
    # whether uploaded chunks cover a whole parser-delimited section.
    section_title: str = ""
    section_start: bool = False
    section_end: bool = False
    # Explicit document metadata does not by itself establish academic publication status.
    document_authors: list[str] = Field(default_factory=list, max_length=32)
    # 非空即表示「这是一条学术来源」。通用网页后端保持 None，因此既有部署的
    # 行为与产物完全不变——学术元数据是增量信息，不是新的必填契约。
    scholarly: ScholarlyMetadata | None = None


class RunManifest(BaseModel):
    """Non-secret inputs that make one research run reproducible."""

    schema_version: int = 1
    created_at: datetime
    workflow_name: str
    workflow_hash: str
    query_hash: str
    # Search selections are lists; the quality policy is a flat mapping of scalars;
    # the remaining non-secret settings are scalars.
    settings: dict[str, bool | int | float | str | list[str] | dict[str, bool | int] | None] = (
        Field(default_factory=dict)
    )
    llm_model: str = ""
    llm_endpoint: str = ""
    search_backend: str = ""
    catalog_snapshot_hash: str = ""
    catalog_model_profiles: list[dict[str, object]] = Field(default_factory=list)


class QualityMetrics(BaseModel):
    """Deterministic run metrics; no judge model is involved."""

    total_findings: int = 0
    verbatim_verified: int = 0
    semantically_supported: int = 0
    report_eligible: int = 0
    corroborated: int = 0
    conflicted: int = 0
    disputed: int = 0
    source_snapshots: int = 0
    cited_sources: int = 0
    cited_source_snapshot_coverage: float = 0.0
    verified_finding_rate: float = 0.0
    supported_finding_rate: float = 0.0
    eligible_finding_rate: float = 0.0
    independent_publishers: int = 0
    blocked_sources: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0


class Quantity(BaseModel):
    """一条论断里的结构化数值。

    科学论断的核心不是句子而是"某指标 = 某数值"，所以数值必须脱离自由文本单独
    建模——否则出对照表时只能从 statement 里正则抠数字，那等于把编造风险搬到了
    报告层。

    ``rendered`` 保留原文写法（``38.36``），它决定容差口径：声明两位小数就按
    ±0.005 判，声明整数就按 ±0.5 判。丢掉它就只能拍一个固定 epsilon，
    "38.36 与 38.4 是否一致"这类问题会被判错。

    ``comparator`` 区分"超过 35 dB"（下界）与"等于 35 dB"（点值）。吞掉它会让
    报告给出比证据更强的结论。
    """

    model_config = ConfigDict(extra="ignore")

    metric: str = Field("", description="指标名，如 PSNR / SSIM / SAM / 参数量")
    value: float | None = None
    unit: str = Field("", description="原文单位写法，如 dB / M / nm；无单位留空")
    rendered: str = Field("", max_length=64, description="原文中的数值写法，决定容差")
    uncertainty: float | None = Field(None, description="± 值（标准差 / 置信区间半宽）")
    comparator: Literal["", "=", ">", ">=", "<", "<="] = ""

    def is_empty(self) -> bool:
        return self.value is None and not self.metric.strip()


class ExperimentConditions(BaseModel):
    """数值成立的实验条件。

    这不是可选的元数据，而是数值可比性的前提：同一个 PSNR 数字在 28 波段与 31
    波段、256×256 裁剪与全图、不同 mask 与训练集下并不可比。抄了数字不抄条件，
    对照表越整齐越误导。

    字段按本项目面向的高光谱计算成像领域挑选，但都是通用形状（数据集 / 划分 /
    规模 / 协议 / 训练数据 / 硬件），换领域不需要改结构。
    """

    model_config = ConfigDict(extra="ignore")

    dataset: str = Field("", description="KAIST / CAVE / ICVL / 真实采集…")
    split: str = Field("", description="测试划分，如 10 scenes / test set")
    bands: int | None = Field(None, ge=1, le=4096, description="光谱波段数")
    spectral_range: str = Field("", description="光谱范围，如 400–700 nm；原文未写则留空")
    scenes: str = Field("", description="场景数量或编号，如 10 scenes / S1–S10")
    acquisition: str = Field(
        "", description="采集方式，如 simulated / real capture；原文未写则留空"
    )
    spatial_size: str = Field("", description="空间尺寸，如 256×256")
    calibration: str = Field("", description="标定信息；原文未报告时留空")
    prototype_validation: str = Field("", description="原型/真实系统验证信息；原文未报告时留空")
    coding_mode: str = Field("", description="编码方式，如 CASSI / coded mask")
    dispersive_element: str = Field("", description="色散元件，如 prism / grating")
    protocol: str = Field("", description="其他关键口径：mask 类型、位移步长…")
    train_data: str = ""
    hardware: str = ""
    notes: str = Field("", max_length=300)

    def is_empty(self) -> bool:
        return not (
            self.dataset
            or self.split
            or self.bands
            or self.spectral_range
            or self.scenes
            or self.acquisition
            or self.spatial_size
            or self.calibration
            or self.prototype_validation
            or self.coding_mode
            or self.dispersive_element
            or self.protocol
            or self.train_data
            or self.hardware
            or self.notes
        )

    def describe(self) -> str:
        """人类可读的条件摘要；表格脚注与证据附录共用。"""
        parts: list[str] = []
        if self.dataset:
            parts.append(self.dataset)
        if self.split:
            parts.append(self.split)
        if self.bands:
            parts.append(f"{self.bands} 波段")
        if self.spectral_range:
            parts.append(self.spectral_range)
        if self.scenes:
            parts.append(f"场景 {self.scenes}")
        if self.acquisition:
            parts.append(f"采集 {self.acquisition}")
        if self.spatial_size:
            parts.append(self.spatial_size)
        if self.calibration:
            parts.append(f"标定 {self.calibration}")
        if self.prototype_validation:
            parts.append(f"原型验证 {self.prototype_validation}")
        if self.coding_mode:
            parts.append(f"编码 {self.coding_mode}")
        if self.dispersive_element:
            parts.append(f"色散元件 {self.dispersive_element}")
        if self.protocol:
            parts.append(self.protocol)
        if self.train_data:
            parts.append(f"训练集 {self.train_data}")
        if self.hardware:
            parts.append(self.hardware)
        if self.notes:
            parts.append(self.notes)
        return "；".join(parts)


class SourceIdentity(BaseModel):
    """判定"是否同一发布方"所需的最小信息集。

    为什么要把它denormalize 到 Finding 上：交叉印证判定在 ``ClaimConsistencyVerifier``
    里跨全部子问题的结果进行，那时手里只有 ``Finding``，早已没有 ``Source``。而
    ``EvidenceVerifier.verify`` 是唯一同时握有两者的时刻——``source_title`` /
    ``source_reference`` / ``source_content_hash`` 已经是按这个理由存下来的。

    存**原始值**而不是预先归一化的键：归一化规则将来会改进（作者名罗马化变体、
    标题差异），存原始值意味着历史 run 重新判定时也能受益于改进后的规则。

    刻意不含机构（``affiliations``）：同一机构不等于同一团队，大学里两个互不相关的
    组报同一个数是真的独立验证。"同一团队"这个真正的信号由作者重叠覆盖。
    """

    model_config = ConfigDict(extra="ignore")

    doi: str = ""
    work_id: str = Field("", description="OpenAlex / arXiv 的同一工作标识")
    title: str = ""
    authors: list[str] = Field(default_factory=list, max_length=32)
    domain: str = Field("", description="registrable domain，兜底判据与向后兼容")
    # 学术出处状态不是发布方身份信号，但证据强度表需要保留三态状态。
    # 缺失元数据只能显示 unknown，不能推断为未评审。
    peer_reviewed: bool | None = None
    # Unknown retraction metadata must remain distinct from an explicit retraction.
    retracted: bool | None = None
    section: str = Field("", description="证据取自全文哪一节")

    def is_empty(self) -> bool:
        # Retraction/section are provenance attributes, not identity signals.  Keeping
        # them out of this check preserves the legacy URL-domain fallback for records
        # that contain only newly-added metadata.
        return not (self.doi or self.work_id or self.title or self.authors or self.domain)


class EvidenceVerification(BaseModel):
    """Program-produced evidence status for one finding.

    The LLM may propose an evidence quote, but only the deterministic verifier may
    promote the status to ``verified``.
    """

    status: Literal["unverified", "verified"] = "unverified"
    method: Literal["none", "normalized_quote"] = "none"
    source_content_hash: str = ""
    source_title: str = ""
    # 程序渲染的学术引用（作者/标题/期刊/年/DOI）。在这里而不是在 Synthesizer 里
    # 现算，是因为只有验证时刻同时握有 Finding 与 Source；下游（报告参考来源列表、
    # 表格脚注、CSV 导出）都只消费这一份，避免三处各渲染一遍导致漂移。
    # 非学术来源保持空串，报告即回退到原来的裸 URL 形式。
    source_reference: str = Field("", max_length=600)
    # 数值校验：程序把模型声明的 Quantity 拿去和 evidence_quote 里真实出现的
    # "数值+单位"比对。三态而不是布尔，因为"没有声明数值"与"声明了但对不上"
    # 是完全不同的两件事——前者是绝大多数定性论断的常态，不该影响准入。
    quantity_status: Literal["not_applicable", "verified", "unsupported"] = "not_applicable"
    quantity_reason: str = ""
    # 发布方身份，用于交叉印证时判"是否同一篇工作 / 同一团队"。
    # 历史记录为 None，此时判定退回按 source_url 的 registrable domain——
    # 也就是改造前的行为，旧 run 回放结果不变。
    source_identity: SourceIdentity | None = None
    evidence_context: str = Field(
        "",
        max_length=1200,
        description="程序从检索快照中截取的证据上下文，不由模型生成",
    )
    # 逐字命中在来源正文里的精确字符区间（左闭右开，按归一化匹配后回填到原文偏移）。
    # 校验时本来就算出了这个区间——evidence_context 正是用它截的窗口。把区间本身也
    # 存下来，引用锚点就从「哪一篇」升级为「哪一段」：配合 source_content_hash 钉住
    # 的同一份快照，任何一条引用都能被独立重新定位并复核，而不只是看程序渲染好的
    # 上下文。历史记录为 None，此时退回只有窗口文本的既有行为。
    quote_start: int | None = Field(default=None, ge=0)
    quote_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_quote_span(self) -> EvidenceVerification:
        """区间要么完整且非退化，要么整体缺席——不接受半个区间。"""
        start, end = self.quote_start, self.quote_end
        if (start is None) != (end is None):
            raise ValueError("quote_start 与 quote_end 必须同时存在或同时缺席")
        if start is not None and end is not None and end <= start:
            raise ValueError("quote_end 必须大于 quote_start")
        return self

    reason: str = ""
    semantic_status: Literal["not_checked", "supported", "unsupported", "uncertain"] = "not_checked"
    semantic_confidence: float = Field(0.0, ge=0.0, le=1.0)
    semantic_reason: str = ""
    claim_id: str = ""
    consistency_status: Literal["not_checked", "clear", "conflicted"] = "not_checked"
    contradicts_claim_ids: list[str] = Field(default_factory=list)
    contradiction_reason: str = ""
    corroboration_status: Literal["not_checked", "single_source", "corroborated", "disputed"] = (
        "not_checked"
    )
    independent_source_count: int = Field(0, ge=0)
    corroborates_claim_ids: list[str] = Field(default_factory=list)
    corroboration_reason: str = ""


class FindingContent(BaseModel):
    """Model-authored content only; verification and provenance belong to the program."""

    statement: str = Field(..., description="一条具体、自洽的事实/发现")
    source_url: str = Field(..., description="该发现的出处 URL（必须来自给定来源）")
    evidence_quote: str = Field(
        "",
        description=(
            "支持该发现的必要连续原文；必须逐字来自 source_url 对应内容。"
            "保留归属、条件及表格标题/表头/相关行，不限于单句，不得拼接或省略"
        ),
    )
    confidence: float = Field(0.7, ge=0.0, le=1.0, description="置信度 0~1")
    # 该论断所描述的对象——对照表里的"行"。方法名 / 方案名 / 数据集名。
    # 不能用"论文"当行：一篇论文常同时报自己和多个 baseline 的数字，
    # 按论文聚合会把它们压成一格。
    entity: str = Field("", max_length=120, description="论断描述的对象，如 MST-L / SD-CASSI")
    # 结构化数值与其成立条件。定性论断留空即可——留空不影响准入（见
    # EvidenceVerification.quantity_status 的三态说明）。
    quantity: Quantity | None = None
    conditions: ExperimentConditions | None = None

    def as_unverified(self) -> Finding:
        # Whitelist even for a subclass returned by an adapter: a model must
        # never import a pre-existing 'verified' flag into the trust boundary.
        return Finding.model_validate(self.model_dump(include=set(FindingContent.model_fields)))


class Finding(FindingContent):
    verification: EvidenceVerification = Field(default_factory=EvidenceVerification)


class RepairFindingContent(FindingContent):
    quote_id: str = Field(
        "",
        description=(
            "可选：本候选给定的原文区间编号。使用时 evidence_quote 留空，由程序回填完整原文"
        ),
    )


class CandidateRepair(BaseModel):
    candidate_id: str
    action: Literal["repair", "drop"]
    findings: list[RepairFindingContent] = Field(default_factory=list)
    reason: str


class ExtractedFindingList(BaseModel):
    """Compact extraction response. Keep the persisted FindingList contract separate."""

    findings: list[FindingContent] = Field(default_factory=list)
    repairs: list[CandidateRepair] = Field(default_factory=list)


class FindingList(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class CandidateCheck(BaseModel):
    finding: Finding
    problems: list[str] = Field(default_factory=list)
    reused: bool = False


class ExtractionAttempt(BaseModel):
    round: int = 0
    action: Literal["initial", "repair", "drop", "error"] = "initial"
    proposals: list[FindingContent] = Field(default_factory=list)
    quote_ids: list[str] = Field(default_factory=list)
    checks: list[CandidateCheck] = Field(default_factory=list)
    reason: str = ""


class QuoteOption(BaseModel):
    id: str
    source_url: str
    source_content_hash: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str


class ExtractionCandidate(BaseModel):
    id: str
    original: FindingContent
    attempts: list[ExtractionAttempt] = Field(default_factory=list)
    accepted: bool = False
    quote_options: list[QuoteOption] = Field(default_factory=list)


class ExtractionAudit(BaseModel):
    version: int = 2
    question: str
    sources: list[Source] = Field(default_factory=list)
    candidates: list[ExtractionCandidate] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ResearchResult(BaseModel):
    sub_question: str
    findings: list[Finding] = Field(default_factory=list)
    extraction_audit: ExtractionAudit | None = None

    def material_data(self) -> dict:
        """Diagnostics are not new evidence and must not invalidate old prose reviews."""
        return self.model_dump(mode="json", exclude={"extraction_audit"})


class Reflection(BaseModel):
    is_sufficient: bool = Field(..., description="现有证据是否足以回答原问题")
    gaps: list[str] = Field(default_factory=list, description="仍缺失的信息点")
    new_sub_questions: list[str] = Field(default_factory=list, description="为补洞而新增的子问题")
    search_queries: dict[str, list[str]] = Field(
        default_factory=dict,
        description="新增子问题原文到简洁检索式的映射；每个问题单独给出必要的关键词组",
    )


class Report(BaseModel):
    query: str
    markdown: str
    citations: list[str] = Field(default_factory=list, description="按 [n] 顺序排列的来源 URL")
