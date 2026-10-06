from __future__ import annotations

import copy
import io

import pytest
from openpyxl import load_workbook

from deep_research.workbench.analysis import DatasetError, analyse, ledger_facts, parse_dataset
from deep_research.workbench.analysis_review import sample_size_scopes
from deep_research.workbench.analysis_scope import AnalysisScope, plan_scope, validate_scope
from deep_research.workbench.publish import _stats_xlsx

CSV = (
    "id,species,year,sex,mass,length,unused\n"
    "1,A,2020,F,10,2,100\n"
    "2,A,2020,M,12,3,200\n"
    "3,A,2021,,14,,300\n"
    "4,B,2020,F,20,4,400\n"
    "5,B,2021,M,22,6,500\n"
    "6,B,2021,F,,8,600\n"
)
SCOPE = AnalysisScope(
    measures=["mass", "length"], groups=["species"], background=["year", "sex", "id"]
)


@pytest.fixture
def result():
    return analyse(CSV, "比较体重与长度，年份和性别仅作背景", scope=SCOPE.model_dump())


def test_selected_roles_control_statistics_figures_and_keep_all_input_columns(result):
    assert result.columns == ["id", "species", "year", "sex", "mass", "length", "unused"]
    assert result.rows == 6 and result.missing["sex"] == 1
    assert result.numeric == ["mass", "length"] and result.categorical == ["species"]
    assert {r["variable"] for r in result.describe} == {"mass", "length"}
    assert {(r["group"], r["variable"]) for r in result.tests} == {
        ("species", "mass"),
        ("species", "length"),
    }
    assert [(r["a"], r["b"], r["n"]) for r in result.correlations] == [("mass", "length", 4)]
    assert [r["n"] for r in result.tests[0]["group_summaries"]] == [3, 2]
    assert [r["n"] for r in result.tests[1]["group_summaries"]] == [2, 3]
    assert [f.name for f in result.figures] == [
        "fig_01_distributions_species.png",
        "fig_02_correlation.png",
    ]
    composition = {row["column"]: row for row in result.composition}
    assert composition["species"]["levels"] == [{"label": "A", "n": 3}, {"label": "B", "n": 3}]
    assert composition["sex"]["missing"] == 1 and composition["sex"]["n"] == 5
    assert "不代表每项测量的有效样本量" in result.facts()
    assert "仅作样本背景的列：year, sex, id" in result.facts()


def test_selected_scope_and_composition_survive_frozen_restore_and_spreadsheet(result):
    frozen = result.snapshot()
    restored = analyse(CSV, result.question, frozen=frozen)
    assert frozen["version"] == 6
    assert restored.snapshot() == frozen
    assert restored.figures == result.figures
    without_facts = {k: v for k, v in frozen.items() if k != "facts"}
    assert ledger_facts(without_facts) == result.facts()
    workbook = load_workbook(io.BytesIO(_stats_xlsx(restored)))
    roles = dict(list(workbook["分析范围"].values)[1:])
    assert roles["year"] == "样本背景（不自动检验）"
    assert roles["unused"] == "保留在原始数据，本次未分析"
    counts = list(workbook["样本构成"].values)[2:]
    assert ("species", 6, 0, 2, "A", 3) in counts
    assert ("sex", 5, 1, 2, "F", 3) in counts
    assert {row[0] for row in list(workbook["描述统计"].values)[1:]} == {"mass", "length"}
    scopes = sample_size_scopes(frozen)
    assert {r["n"] for r in scopes if r["scope"] == "single_group"} == {2, 3}
    assert [r["n"] for r in scopes if r["scope"] == "variable_pair"] == [4]


def test_restore_rejects_changed_scope_data_or_inconsistent_frozen_roles(result):
    frozen = result.snapshot()
    with pytest.raises(DatasetError, match="分析范围与冻结"):
        analyse(CSV, frozen=frozen, scope=AnalysisScope(measures=["mass"]).model_dump())
    with pytest.raises(DatasetError, match="输入数据与任务统计"):
        analyse(CSV.replace("10,2", "11,2"), frozen=frozen)
    corrupt = copy.deepcopy(frozen)
    corrupt["numeric"].append("year")
    with pytest.raises(DatasetError, match="变量与分析范围"):
        analyse(CSV, frozen=corrupt)


@pytest.mark.parametrize(
    "scope",
    [
        AnalysisScope(measures=[]),
        AnalysisScope(measures=["absent"]),
        AnalysisScope(measures=["species"]),
        AnalysisScope(measures=["id"]),
        AnalysisScope(measures=["mass"], background=["mass"]),
        AnalysisScope(measures=["mass"], groups=["id"]),
    ],
)
def test_invalid_scope_never_falls_back_to_analysing_every_column(scope):
    with pytest.raises(DatasetError):
        validate_scope(parse_dataset(CSV), scope)


def test_explicit_numeric_groups_and_background_only_are_supported():
    result = analyse(CSV, scope=AnalysisScope(measures=["mass"], groups=["year"]).model_dump())
    assert result.numeric == ["mass"] and result.tests[0]["group"] == "year"
    background = analyse(CSV, scope=AnalysisScope(measures=[], background=["sex"]).model_dump())
    assert not background.tests and not background.correlations and not background.figures
    assert background.composition[0]["n"] == 5


async def test_plan_scope_retries_invalid_selection_and_caches_only_matching_input():
    class Planner:
        def __init__(self):
            self.prompts = []

        async def parse(self, system, user, schema, **kwargs):
            self.prompts.append(str(user))
            return AnalysisScope(measures=["absent"]) if len(self.prompts) == 1 else SCOPE

    planner = Planner()
    scratch = {}
    assert await plan_scope(planner, CSV, "比较体重与长度", scratch) == SCOPE
    assert "纠正无效范围" in planner.prompts[1]
    assert await plan_scope(planner, CSV, "比较体重与长度", scratch) == SCOPE
    assert len(planner.prompts) == 2
    await plan_scope(planner, CSV, "仅描述背景", scratch)
    await plan_scope(planner, CSV.replace("10,2", "11,2"), "仅描述背景", scratch)
    assert len(planner.prompts) == 4


async def test_invalid_plan_after_retry_is_not_cached():
    class Planner:
        async def parse(self, *args, **kwargs):
            return AnalysisScope(measures=["absent"])

    scratch = {}
    with pytest.raises(DatasetError):
        await plan_scope(Planner(), CSV, "分析", scratch)
    assert "analysis_scope" not in scratch


async def test_writer_uses_selected_scope_and_recovery_does_not_replan(settings):
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.observability import Tracer
    from deep_research.workbench.analysis import DataAnalyst
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
    from deep_research.workbench.templates import DATA_ANALYSIS
    from tests.fakes import FakeSearch
    from tests.test_prose_review import Judge

    class ScopedWriter(Judge):
        scope_calls = 0
        writer_prompts: list[str]

        def __init__(self):
            super().__init__()
            self.writer_prompts = []

        async def parse(self, system, user, schema, **kwargs):
            if schema is AnalysisScope:
                self.scope_calls += 1
                return SCOPE
            return await super().parse(system, user, schema, **kwargs)

        async def stream(self, system, user, **kwargs):
            self.writer_prompts.append(system)
            yield "\n\n".join(
                f"## {section.title}\n变量存在相关关系。" for section in DATA_ANALYSIS.sections
            )

    contract = build_contract(DATA_ANALYSIS, "比较体重与长度", attachments_csv=CSV)
    bb = Blackboard(
        query=contract.original_request,
        scratch={CONTRACT_SCRATCH_KEY: contract.model_dump(mode="json")},
    )
    llm = ScopedWriter()
    ctx = RunContext(llm=llm, search_tool=FakeSearch(), tracer=Tracer(), settings=settings)
    await DataAnalyst().step(bb, ctx)
    prompt = llm.writer_prompts[0]
    assert "【统计台账】" in prompt and "不得计算、改写或编造任何数字" in prompt
    assert "ledger_path" in prompt
    # Ledger tables must not inherit the incompatible paper-finding schema or
    # instructions to compute derived values and cite nonexistent references.
    assert "source_finding_id" not in prompt and "发现 ID 数组" not in prompt
    assert "写出带引用的显式算式" not in prompt
    assert "表格的每个事实或数据行都要有本次 [n] 引用" not in prompt
    frozen = copy.deepcopy(bb.scratch["analysis"])
    assert frozen["scope"] == SCOPE.model_dump()
    assert frozen["numeric"] == ["mass", "length"]
    await DataAnalyst().step(bb, ctx)
    assert llm.scope_calls == 1 and bb.scratch["analysis"] == frozen
