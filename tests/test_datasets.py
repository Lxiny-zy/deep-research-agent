"""数据分析输入回归：表格解析、多工作表选择、缺数据与超长数据的处理、数据来源记录。"""

from __future__ import annotations

import asyncio
import base64
import io

import httpx
import pytest
from httpx import ASGITransport

from deep_research.persistence.memory_repository import InMemoryRepository
from deep_research.workbench.analysis import DatasetError, analyse
from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, DATASET_MAX_CHARS
from deep_research.workbench.datasets import parse_table_file, profile_csv, select_sheet

CSV = "method,psnr\nA,30\nA,31\nA,32\nB,34\nB,35\nB,36\n"


def _xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_known_table_gives_the_expected_statistics() -> None:
    result = analyse(CSV, "两种方法差异显著吗")
    psnr = next(row for row in result.describe if row["variable"] == "psnr")
    assert (result.rows, psnr["n"], psnr["mean"], psnr["min"], psnr["max"]) == (6, 6, 33.0, 30, 36)
    assert "数据来源：粘贴的表格" in result.facts()


def test_profile_reports_rows_and_column_types() -> None:
    table = profile_csv("", CSV)
    assert table.rows == 6
    assert table.columns == [{"name": "method", "type": "文本"}, {"name": "psnr", "type": "数值"}]


def test_csv_exported_as_gb18030_is_decoded() -> None:
    raw = "方法,峰值信噪比\n甲,30\n乙,31\n".encode("gb18030")
    sheet = select_sheet(parse_table_file(raw, "结果.csv"), None)
    assert sheet["rows"] == 2 and sheet["columns"][0]["name"] == "方法"


def test_multi_sheet_workbook_requires_an_explicit_choice() -> None:
    raw = _xlsx(
        {
            "CAVE": [["method", "psnr"], ["A", 30.5], ["B", 34]],
            "KAIST": [["method", "psnr"], ["A", 29], ["B", 33.25], ["C", 35]],
            "说明": [],
        }
    )
    parsed = parse_table_file(raw, "results.xlsx")
    assert [sheet["name"] for sheet in parsed["sheets"]] == ["CAVE", "KAIST"]  # 空表跳过
    with pytest.raises(DatasetError, match="多个工作表"):
        select_sheet(parsed, None)
    kaist = select_sheet(parsed, "KAIST")
    assert kaist["rows"] == 3
    assert kaist["csv"].splitlines() == ["method,psnr", "A,29", "B,33.25", "C,35"]
    with pytest.raises(DatasetError, match="找不到"):
        select_sheet(parsed, "Harvard")


def test_oversized_sheet_is_reported_instead_of_truncated(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from deep_research.workbench import datasets

    monkeypatch.setattr(datasets, "MAX_ROWS", 3)
    raw = _xlsx(
        {
            "big": [["x"], *[[value] for value in range(10)]],
            "small": [["x"], [1], [2]],
        }
    )
    parsed = parse_table_file(raw, "mixed.xlsx")
    assert [sheet["name"] for sheet in parsed["sheets"]] == ["small"]
    assert parsed["skipped"][0]["name"] == "big"
    assert "未截断使用" in parsed["skipped"][0]["error"]


def test_too_many_characters_are_rejected_not_cut() -> None:
    text = "x\n" + "1\n" * (DATASET_MAX_CHARS // 2 + 1)
    with pytest.raises(DatasetError, match="字符上限"):
        profile_csv("", text)


def test_missing_data_never_falls_back_to_the_sample_implicitly() -> None:
    with pytest.raises(DatasetError, match="没有可分析的数据"):
        analyse("", "")
    demo = analyse("", "", allow_synthetic=True)
    assert demo.synthetic and "合成示例数据" in demo.facts()


def _client(app) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def api_repo(monkeypatch):  # type: ignore[no-untyped-def]
    from deep_research import api
    from deep_research.config import Settings

    repo = InMemoryRepository()
    monkeypatch.setattr(api.app.state, "catalog", None, raising=False)
    api.app.state.settings = Settings()
    api.app.state.repo = repo
    api.app.state.live, api.app.state.tasks = {}, set()
    api.app.state.run_tasks, api.app.state.cancellation_requested = {}, set()
    api.app.state.config_lock = asyncio.Lock()
    api.app.state.run_admission = api.RunAdmission(8, 32)

    async def _noop(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(api, "_execute", _noop)
    monkeypatch.setattr(api, "_run_limiter", api._RateLimiter(max_calls=1000, window_seconds=60.0))
    return api, repo


async def _contract(repo, run_id: str) -> dict:  # type: ignore[no-untyped-def]
    detail = await repo.get_run(run_id)
    assert detail is not None and detail.orchestration is not None
    return detail.orchestration.checkpoint["scratch"][CONTRACT_SCRATCH_KEY]


@pytest.mark.asyncio
async def test_data_task_needs_data_unless_the_user_chose_the_demo(api_repo) -> None:
    api, repo = api_repo
    body = {"query": "三种方法差异显著吗？", "template": "dataAnalysis", "clarified": True}
    async with _client(api.app) as client:
        missing = await client.post("/api/runs", json=body)
        demo = await client.post("/api/runs", json={**body, "demo_data": True})
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "dataset_required"
    assert demo.status_code == 202, demo.text
    contract = await _contract(repo, demo.json()["run_id"])
    assert contract["demo_data"] is True and contract["dataset_csv"] == ""
    assert len(await repo.list_runs(limit=10)) == 1


@pytest.mark.asyncio
async def test_uploaded_sheet_is_validated_and_its_source_recorded(api_repo) -> None:
    api, repo = api_repo
    raw = _xlsx({"CAVE": [["method", "psnr"], ["A", 30], ["B", 34]], "KAIST": [["m"], ["x"]]})
    async with _client(api.app) as client:
        parsed = await client.post(
            "/api/datasets",
            json={"filename": "results.xlsx", "data_base64": base64.b64encode(raw).decode()},
        )
        sheet = parsed.json()["sheets"][0]
        created = await client.post(
            "/api/runs",
            json={
                "query": "两种方法差异显著吗？",
                "template": "dataAnalysis",
                "clarified": True,
                "dataset": sheet["csv"],
                "dataset_source": {"filename": "results.xlsx", "sheet": sheet["name"]},
            },
        )
        invalid = await client.post(
            "/api/runs",
            json={
                "query": "分析",
                "template": "dataAnalysis",
                "clarified": True,
                "dataset": "\n\n",
            },
        )
        huge = await client.post(
            "/api/runs",
            json={
                "query": "分析",
                "template": "dataAnalysis",
                "clarified": True,
                "dataset": "x\n" + "1\n" * (DATASET_MAX_CHARS // 2 + 1),
            },
        )
        bad_file = await client.post(
            "/api/datasets", json={"filename": "a.docx", "data_base64": "UEs="}
        )
    assert parsed.status_code == 200, parsed.text
    assert [item["name"] for item in parsed.json()["sheets"]] == ["CAVE", "KAIST"]
    assert created.status_code == 202, created.text
    source = (await _contract(repo, created.json()["run_id"]))["dataset_source"]
    assert source["filename"] == "results.xlsx" and source["sheet"] == "CAVE"
    assert source["rows"] == 2 and source["columns"][1] == {"name": "psnr", "type": "数值"}
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "dataset_required"
    assert huge.status_code == 422 and huge.json()["detail"]["code"] == "dataset_too_large"
    assert bad_file.status_code == 422
    assert bad_file.json()["detail"]["code"] == "dataset_invalid"


@pytest.mark.asyncio
async def test_preview_profiles_pasted_data_with_the_analysis_rules(api_repo) -> None:
    api, _ = api_repo
    async with _client(api.app) as client:
        good = await client.post(
            "/api/templates/contract",
            json={"template": "dataAnalysis", "query": "差异显著吗？\n" + CSV},
        )
    payload = good.json()
    assert payload["dataset_profile"]["rows"] == 6
    assert payload["dataset_rows"] == 6
    assert payload["demo_data"] is False
