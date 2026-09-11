from deep_research.maintenance import inspect_artifacts
from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository


async def test_read_only_inventory_distinguishes_referenced_and_unreferenced_runs(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite'}"
    engine = make_engine(url)
    await create_all(engine)
    try:
        run_id = await SqlRepository(make_sessionmaker(engine)).create_run("q")
    finally:
        await engine.dispose()
    root = tmp_path / "artifacts"
    for name in (run_id, "unreferenced"):
        path = root / "runs" / name
        path.mkdir(parents=True)
        (path / "report.md").write_text("content", encoding="utf-8")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = await inspect_artifacts(url, root, grace_days=0)
    assert result["referenced_runs"] == 1 and result["read_only"]
    assert [item["path"] for item in result["unreferenced_directories"]] == ["runs/unreferenced"]
    # SQLite readers may create shared-memory indexes for WAL mode. Business
    # data and every artifact must remain byte-for-byte unchanged.
    after = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file() and p.name not in {"test.sqlite-shm", "test.sqlite-wal"}
    }
    assert before == after
