"""结构化报告文档模型：报告先是结构，再是文本。

## 为什么需要它

改造前 ``Report`` 只有 ``{query, markdown: str, citations: list[str]}``——正文是一坨
LLM 生成的字符串，证据装置根本不在报告里，而是前端渲染时用 ``report.markdown`` +
``results[].findings[]`` + 事件流三方 join 出来的（``frontend/src/lib/evidence.ts``）。

这导致两件事做不成：

1. **多格式输出**。下载 .md 只拿到裸 markdown，装置全丢；PDF 无从谈起。要在后端加
   渲染器就得第二次实现同一套 join。
2. **带引用的表格与图表**。单元格里的 ``[n]`` 和协议脚注一旦被压进自由文本就再也
   取不回来。

## 核心不变量：图表必有源表

``ChartBlock`` **不携带任何数据**，只携带一个 ``source_table``（某个 ``TableBlock``
的 id）与取哪几列。这不是为了省字段，是为了让"凭空画一张图"在**类型层面**不可表达：

* 图上每个数据点都来自已通过证据门禁的表格单元格，因此都带 ``[n]``；
* Markdown 无法渲染矢量图，但因为源表一定存在，降级成表格是**无损**的；
* 模型永远碰不到图表数据——它只能在正文里写一个占位符说"这里放哪张图"。

一张编造的柱状图比一句编造的话危险得多，因为它看起来是"数据"。把这条做成结构性质
而不是流程约定，判定器被绕过也不会失效。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 分类色最多 3 个系列。这个数字不是审美选择，是 dataviz 调色板校验器在
# scatter 等 all-pairs 图形下的实测上限：前三个槽位在明暗两种表面、全部
# 两两组合下都通过 CVD 与常视分辨阈值，第四槽把黄与橙同时放上屏就不过。
MAX_CHART_SERIES = 3


class TableColumn(BaseModel):
    """一列的元数据。``unit`` 与 ``note`` 分开，是因为单位属于列，脚注属于口径。"""

    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    unit: str = ""
    align: Literal["left", "right"] = "left"
    numeric: bool = False
    # 口径脚注编号。同一指标在不同实验条件下的数值必须分列，列头靠它指向脚注，
    # 读者才知道这两列为什么不能直接比。
    note_ref: int | None = Field(None, ge=1)


class TableCell(BaseModel):
    """一个单元格。

    ``value`` 为空即"未报告"——渲染器必须原样呈现缺失，禁止补零或让模型填补。
    ``numeric`` 与 ``value`` 分开保存：前者给图表算坐标，后者是给人看的显示形式
    （含有效数字与正负号），两者不能互相推导。
    """

    model_config = ConfigDict(extra="ignore")

    value: str = ""
    numeric: float | None = None
    # 多个出处：交叉印证过的数值本来就该把两个独立来源都显示出来，
    # 那正是双源门禁的产物。单来源时列表长度为 1。
    citations: list[int] = Field(default_factory=list)
    note_ref: int | None = Field(None, ge=1, description="协议脚注编号")
    # 多个来源报告了不同数值。表里并列呈现（不静默挑一个），但**排除出图表**：
    # 有争议的值画成一个点等于替读者做了裁决。
    disputed: bool = False

    @property
    def reported(self) -> bool:
        return bool(self.value.strip())


class TableRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    citation: int | None = Field(None, ge=1)
    cells: dict[str, TableCell] = Field(default_factory=dict)

    def cell(self, key: str) -> TableCell:
        """取单元格；不存在时返回空单元格（＝未报告），而不是抛异常。

        缺列是数据的常态（某篇论文没报 FLOPs），不该让整份报告渲染失败。
        """
        return self.cells.get(key) or TableCell()


class TableBlock(BaseModel):
    """一张对照表。行是被比较的对象，列是维度。

    ``notes`` 是协议脚注。它在科学报告里不是装饰：同一个 PSNR 数字在 28 波段与
    31 波段、256×256 裁剪与全图、不同 mask 下并不可比，抄了数字不抄口径就是误导。
    """

    model_config = ConfigDict(extra="ignore")

    kind: Literal["table"] = "table"
    id: str
    title: str = ""
    columns: list[TableColumn] = Field(default_factory=list)
    rows: list[TableRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    caption: str = ""

    def column(self, key: str) -> TableColumn | None:
        return next((c for c in self.columns if c.key == key), None)


ChartForm = Literal["bar", "dot", "grouped_bar", "scatter", "line"]


class ChartBlock(BaseModel):
    """一张图。**不含数据**——只指向源表与取哪几列（见模块 docstring 的不变量）。

    ``form`` 的选择遵循"数据的任务决定形式"：
      * ``bar``——单一指标跨对象比**绝对量**。对象是无序名义类别（方法名），所以所有柱
        同一个颜色，**不能**按数值深浅上色（那会把柱长重复编码成色相）。柱长即数值，
        因此基线强制为零。
      * ``dot``——同样是单一指标跨对象，但要看的是**彼此差异**。点用位置编码，位置没有
        "从零开始"的语义，所以非零基线是诚实的。35 dB 基座上比 0.5 dB 差异用这个，
        而不是去截断柱状图的 Y 轴。
      * ``grouped_bar``——≤3 个指标并排，此时系列本身是主题，用分类色 + 图例。
      * ``scatter``——两个维度的权衡（如精度 vs 参数量）。
      * ``line``——沿连续轴的变化（光谱曲线、逐年趋势）。
    """

    model_config = ConfigDict(extra="ignore")

    kind: Literal["chart"] = "chart"
    id: str
    title: str = ""
    form: ChartForm = "bar"
    source_table: str = Field(..., description="TableBlock.id；图必有源表")
    value_columns: list[str] = Field(default_factory=list, max_length=MAX_CHART_SERIES)
    x_column: str = Field("", description="scatter / line 的横轴列；为空则用行标签")
    emphasis: str = Field("", description="要高亮的行标签；其余行转灰（emphasis 形式）")
    y_label: str = ""
    caption: str = ""


class ProseBlock(BaseModel):
    """LLM 写的叙述段落，含 ``[n]`` 角标。模型只拥有这一种块。"""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["prose"] = "prose"
    markdown: str = ""


Block = ProseBlock | TableBlock | ChartBlock


class ReferenceEntry(BaseModel):
    """参考来源一条。``reference`` 是学术引用文本，为空则回退裸 ``url``。"""

    model_config = ConfigDict(extra="ignore")

    index: int = Field(..., ge=1)
    url: str
    reference: str = ""

    def render(self) -> str:
        return self.reference or self.url


class EvidenceRecord(BaseModel):
    """证据附录的一条记录：交互侧栏在纸上的等价物。

    这里刻意把原先只活在 HTML ``title=`` 属性里的字段全部提升为正式内容——
    ``verification_reason``、``semantic_confidence``、``corroboration_reason`` 和完整
    ``content_hash``。tooltip 在触屏上不可达、打印时不输出，也就是说这些信息在
    HTML 移动端就已经在无声丢失，不是做 PDF 才出现的问题。
    """

    model_config = ConfigDict(extra="ignore")

    citation: int = Field(..., ge=1)
    claim_id: str = ""
    statement: str = ""
    quote: str = ""
    context: str = ""
    source_url: str = ""
    reference: str = ""
    source_section: str = ""
    content_hash: str = ""
    verbatim_verified: bool = False
    verification_reason: str = ""
    semantic_status: str = "not_checked"
    semantic_confidence: float = 0.0
    semantic_reason: str = ""
    consistency_status: str = "not_checked"
    contradicts_claim_ids: list[str] = Field(default_factory=list)
    contradiction_reason: str = ""
    corroboration_status: str = "not_checked"
    independent_source_count: int = 0
    corroboration_reason: str = ""
    # 结构化数值与其成立条件。条件不是装饰：同一个 PSNR 数字在 28 波段与 31 波段、
    # 不同 mask 与训练集下并不可比，抄了数字不抄条件，对照表越整齐越误导。
    quantity_label: str = Field("", description="如 PSNR = 38.36 dB")
    conditions_label: str = Field("", description="如 KAIST；10 scenes；28 波段")
    quantity_status: str = "not_applicable"
    quantity_reason: str = ""


class Overview(BaseModel):
    """报告头部的证据链概览。``blocked_sources`` 为 None＝本次事件流没有审计事件。"""

    model_config = ConfigDict(extra="ignore")

    records: int = 0
    verbatim_matched: int = 0
    semantically_supported: int = 0
    corroborated: int = 0
    conflicted: int = 0
    blocked_sources: int | None = None


# 快照免责声明。放在模型里而不是各渲染器里，是为了三种格式说的是同一句话——
# 这句话界定了系统到底声称了什么，不能因为渲染路径不同而措辞漂移。
DISCLAIMER = (
    "本报告展示的是检索服务返回的快照上下文，不等同于来源完整正文，也不等同于"
    "事实已获证实。系统保证的是出处可追溯、引用可逐字核验、单源/双源/冲突状态"
    "可判定；不保证论断在开放世界为真。"
)


class ReportDocument(BaseModel):
    """一份报告的完整结构。三个渲染器（Markdown / HTML / 打印）都只消费它。

    ``Report``（``{query, markdown, citations}``）**继续保留且语义不变**：前端按
    下标做 [n] 跳转、质量指标按 URL 与检索快照比对覆盖率，都依赖它。本模型是
    并列的增量产物，不是替代品——旧客户端与历史 run 完全不受影响。
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    query: str = ""
    blocks: list[Block] = Field(default_factory=list)
    references: list[ReferenceEntry] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    overview: Overview = Field(default_factory=Overview)
    disclaimer: str = DISCLAIMER

    def table(self, table_id: str) -> TableBlock | None:
        for block in self.blocks:
            if isinstance(block, TableBlock) and block.id == table_id:
                return block
        return None

    def evidence_for(self, citation: int) -> list[EvidenceRecord]:
        return [record for record in self.evidence if record.citation == citation]
