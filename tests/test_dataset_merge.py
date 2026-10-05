"""Explicit table joins remain auditable and never invent independent observations."""

import pytest

from deep_research.workbench.analysis import analyse


def test_repeated_subjects_do_not_enter_independent_tests_or_pearson_p_values():
    result = analyse(
        "subject_id,visit,mass_g,length_mm\n"
        "s1,before,3200,30\ns1,after,3400,31\n"
        "s2,before,3300,32\ns2,after,3700,34\n"
        "s3,before,3500,33\ns3,after,3900,36",
        "比较访视前后的体重和长度，并分析相关",
    )
    assert result.rows == 6
    assert result.describe and not result.tests and not result.correlations
    assert any("重复测量" in issue and "subject_id" in issue for issue in result.issues)


def test_identifier_spelling_does_not_collapse_distinct_subjects():
    from deep_research.workbench.analysis import parse_dataset

    frame = parse_dataset("subject_id,value\n001,10\n1,12")
    assert frame.subject_id.tolist() == ["001", "1"]


def test_unique_row_numbers_do_not_hide_repeated_subjects():
    result = analyse(
        "row_id,subject_id,visit,mass\n1,A,before,10\n2,A,after,12\n"
        "3,B,before,11\n4,B,after,15", "比较访视差异",
    )
    assert not result.tests
    assert any("重复测量" in issue for issue in result.issues)


def test_visit_column_cannot_be_used_to_disguise_repeated_subjects():
    from deep_research.workbench.analysis import DatasetError, parse_dataset
    from deep_research.workbench.analysis_scope import AnalysisScope, validate_scope

    frame = parse_dataset("subject_id,visit,mass\nA,1,10\nA,2,12\nB,1,11\nB,2,15")
    scope = AnalysisScope(measures=["mass"], subject_columns=["subject_id", "visit"])
    with pytest.raises(DatasetError, match="标识"):
        validate_scope(frame, scope)


def merge_request():
    return {
        "tables": [
            {"name": "Measurements", "csv": "subject_id,mass_g\n001,3200\n002,3400\n003,3600"},
            {"name": "Subjects", "csv": "code,group\n001,A\n002,B\n004,C"},
        ],
        "base": "Measurements",
        "joins": [{
            "sheet": "Subjects", "left_keys": ["subject_id"], "right_keys": ["code"],
            "how": "left", "relationship": "one_to_one",
        }],
    }


def test_join_uses_declared_keys_and_discloses_unmatched_rows():
    from deep_research.workbench.dataset_merge import DatasetMerge, merge_tables

    merged = merge_tables(DatasetMerge.model_validate(merge_request()))
    assert merged["rows"] == 3
    assert merged["csv"].splitlines() == [
        "subject_id,mass_g,Subjects.group", "001,3200,A", "002,3400,B", "003,3600,",
    ]
    assert merged["merge"]["joins"][0]["unmatched_left"] == 1
    assert merged["merge"]["joins"][0]["unmatched_right"] == 1
    assert len(merged["merge"]["tables"][0]["input_sha256"]) == 64


@pytest.mark.parametrize("bad_rows", ["001,A\n001,B", ",A\n002,B"])
def test_ambiguous_or_missing_keys_do_not_multiply_or_match_rows(bad_rows):
    from deep_research.workbench.analysis import DatasetError
    from deep_research.workbench.dataset_merge import DatasetMerge, merge_tables

    request = merge_request()
    request["tables"][1]["csv"] = "code,group\n" + bad_rows
    with pytest.raises(DatasetError, match="键"):
        merge_tables(DatasetMerge.model_validate(request))


async def test_create_run_recomputes_and_freezes_the_join_plan_and_inputs(api_repo):
    from tests.test_datasets import _client, _contract

    api, repo = api_repo
    request = merge_request()
    async with _client(api.app) as client:
        preview = await client.post("/api/datasets/merge", json=request)
        assert preview.status_code == 200, preview.text
        created = await client.post("/api/runs", json={
            "query": "分析合并后的体重与组别", "template": "dataAnalysis", "clarified": True,
            "dataset_merge": request, "dataset_source": {"filename": "study.xlsx"},
        })
    assert created.status_code == 202, created.text
    contract = await _contract(repo, created.json()["run_id"])
    assert contract["dataset_csv"] == preview.json()["csv"]
    assert contract["dataset_source"]["merge"] == preview.json()["merge"]
    assert contract["dataset_tables"] == request["tables"]


def test_composite_keys_and_multiple_join_steps_preserve_their_column_origins():
    from deep_research.workbench.dataset_merge import DatasetMerge, merge_tables

    result = merge_tables(DatasetMerge.model_validate({
        "tables": [
            {"name": "values", "csv": "site,id,value\nA,01,10\nB,01,20"},
            {"name": "people", "csv": "code,site,group\n01,A,X\n01,B,Y"},
            {"name": "groups", "csv": "group,label\nX,control\nY,treated"},
        ],
        "base": "values", "joins": [
            {"sheet": "people", "left_keys": ["site", "id"], "right_keys": ["site", "code"]},
            {"sheet": "groups", "left_keys": ["people.group"], "right_keys": ["group"]},
        ],
    }))
    assert result["csv"].splitlines() == [
        "site,id,value,people.group,groups.label", "A,01,10,X,control", "B,01,20,Y,treated",
    ]
    assert result["merge"]["columns"][-1] == {
        "output": "groups.label", "table": "groups", "column": "label",
    }


def test_many_to_one_is_explicit_and_does_not_duplicate_left_observations():
    from deep_research.workbench.analysis import DatasetError
    from deep_research.workbench.dataset_merge import DatasetMerge, merge_tables

    data = merge_request()
    data["tables"][0]["csv"] = "subject_id,mass_g\n001,3200\n001,3300\n002,3400"
    with pytest.raises(DatasetError, match="键不唯一"):
        merge_tables(DatasetMerge.model_validate(data))
    data["joins"][0]["relationship"] = "many_to_one"
    merged = merge_tables(DatasetMerge.model_validate(data))
    assert merged["rows"] == 3
    result = analyse(merged["csv"], source={"merge": merged["merge"]})
    assert not result.tests and any("重复测量" in issue for issue in result.issues)


@pytest.mark.parametrize(
    "kind", ["missing_table", "duplicate_table", "missing_key", "key_count", "duplicate_header"]
)
def test_invalid_join_plans_fail_before_using_partial_data(kind):
    from deep_research.workbench.analysis import DatasetError
    from deep_research.workbench.dataset_merge import DatasetMerge, merge_tables

    data = merge_request()
    if kind == "missing_table":
        data["joins"][0]["sheet"] = "absent"
    elif kind == "duplicate_table":
        data["tables"][1]["name"] = data["base"]
    elif kind == "missing_key":
        data["joins"][0]["left_keys"] = ["absent"]
    elif kind == "key_count":
        data["joins"][0]["left_keys"] = ["subject_id", "mass_g"]
    else:
        data["tables"][1]["csv"] = "code,code\n001,A\n002,B"
    with pytest.raises(DatasetError):
        merge_tables(DatasetMerge.model_validate(data))


async def test_invalid_join_and_ambiguous_dual_input_do_not_create_runs(api_repo):
    from tests.test_datasets import _client

    api, repo = api_repo
    payload = {
        "query": "分析合并数据", "template": "dataAnalysis", "clarified": True,
        "dataset_merge": merge_request(),
    }
    async with _client(api.app) as client:
        dual = await client.post("/api/runs", json={**payload, "dataset": "x\n1\n2"})
        payload["dataset_merge"]["joins"][0]["left_keys"] = ["absent"]
        invalid = await client.post("/api/runs", json=payload)
    assert dual.status_code == invalid.status_code == 422
    assert await repo.list_runs(limit=10) == []


@pytest.mark.parametrize("repeated", [False, True])
async def test_joined_data_reaches_inline_and_worker_analysis_and_saved_deliverables(
    scenario_app, monkeypatch, repeated,
):
    from deep_research.execution import RunExecutor
    from deep_research.orchestrator import DeepResearchAgent
    from deep_research.workbench.templates import get_template
    from tests.fakes import FakeSearch
    from tests.test_library_task_paths import finish_run
    from tests.test_workbench import WorkbenchLLM

    client, repo = scenario_app
    body = "\n\n".join(
        f"## {section.title}\n观察变量分布。" for section in get_template("dataAnalysis").sections
    )

    async def build_agent(self, settings, **kwargs):
        search = FakeSearch()
        return (
            DeepResearchAgent(settings, llm=WorkbenchLLM(body), search_tool=search, **kwargs),
            search,
        )

    monkeypatch.setattr(RunExecutor, "build_agent", build_agent)
    request = merge_request()
    if repeated:
        request["tables"][0]["csv"] = "subject_id,mass_g\n001,3200\n001,3400\n002,3600"
        request["joins"][0]["relationship"] = "many_to_one"
    created = await client.post("/api/runs", json={
        "query": "描述体重分布", "template": "dataAnalysis", "clarified": True,
        "dataset_merge": request, "dataset_source": {"filename": "study.xlsx"},
    })
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]
    await finish_run(repo)
    detail = await repo.get_run(run_id)
    assert detail.status in {"done", "needs_review"}
    scratch = detail.orchestration.checkpoint["scratch"]
    assert scratch["task_contract"]["dataset_tables"] == request["tables"]
    assert scratch["analysis"]["rows"] == 3
    assert scratch["analysis"]["describe"][0]["mean"] == 3400
    assert any("未匹配" in note for note in scratch["analysis"]["issues"])
    if repeated:
        assert scratch["analysis"]["tests"] == scratch["analysis"]["correlations"] == []
        assert "当前尚不支持" in detail.report.markdown and "重复测量" in detail.report.markdown
    response = await client.get(f"/api/runs/{run_id}/deliverables")
    assert response.status_code == 200
    assert "xlsx" in {item["format"] for item in response.json()["items"]}
    artifact = next(item for item in response.json()["items"] if item["format"] == "xlsx")
    downloaded = await client.get(f"/api/runs/{run_id}/deliverables/{artifact['name']}")
    assert downloaded.status_code == 200 and downloaded.content.startswith(b"PK")


def test_single_table_request_hash_stays_compatible_with_existing_submission_ids():
    import hashlib
    import json

    from deep_research.api import CreateRunRequest, _run_request_hash

    request = CreateRunRequest(query="test", template="dataAnalysis", dataset="x\n1\n2")
    old = request.model_dump(mode="json")
    old.pop("dataset_merge")
    digest = hashlib.sha256(
        json.dumps(old, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert _run_request_hash(request) == digest


def test_new_identifier_detection_does_not_reclassify_a_frozen_legacy_measurement(monkeypatch):
    from deep_research.workbench import analysis_inputs
    from deep_research.workbench.analysis_scope import AnalysisScope

    csv = "patient,value\n1,10\n2,20\n3,40"
    original = analysis_inputs.identifier_column
    with monkeypatch.context() as context:
        context.setattr(
            analysis_inputs, "identifier_column", lambda name: name != "patient" and original(name)
        )
        old = analyse(csv, scope=AnalysisScope(measures=["patient", "value"]).model_dump())
    restored = analyse(csv, frozen=old.snapshot())
    assert restored.snapshot() == old.snapshot()
    assert restored.figures == old.figures


def test_joined_column_prefixes_do_not_bypass_identifier_calendar_or_background_roles():
    from deep_research.workbench.dataset_merge import DatasetMerge, merge_tables

    data = merge_request()
    data["tables"][1]["csv"] = (
        "code,year,sex,patient\n001,2020,F,01\n002,2021,M,02\n004,2020,F,03"
    )
    merged = merge_tables(DatasetMerge.model_validate(data))
    result = analyse(merged["csv"], "描述体重分布")
    assert result.numeric == ["mass_g"]
    assert not result.tests
    assert "Subjects.sex" not in result.categorical


from tests.test_datasets import api_repo as api_repo  # noqa: E402
from tests.test_scenario_paths import scenario_app as scenario_app  # noqa: E402
