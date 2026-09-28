"""导出能力探测与建表入口：两者都是部署期行为，按依赖是否存在给出确定结果。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from deep_research.persistence import init_db
from deep_research.report import capabilities


@pytest.fixture(autouse=True)
def _fresh_cache():
    capabilities.export_capabilities.cache_clear()
    yield
    capabilities.export_capabilities.cache_clear()


def test_deterministic_formats_are_always_available(monkeypatch):
    monkeypatch.setattr(capabilities.shutil, "which", lambda _name: None)
    formats = capabilities.export_capabilities()
    for name in ("md", "csv", "tex", "bib", "bundle"):
        assert formats[name] is True
    # 没有 TeX 工具链时论文 PDF 不可用，但不影响其他格式
    assert formats["paper_pdf"] is False


def test_optional_modules_are_probed(monkeypatch):
    def fake_import(name: str):
        if name == "weasyprint":
            raise OSError("missing native libs")  # weasyprint 缺系统库时抛 OSError 而非 ImportError
        return object()

    monkeypatch.setattr(capabilities, "import_module", fake_import)
    formats = capabilities.export_capabilities()
    assert formats["xlsx"] is True
    assert formats["pdf"] is False


def test_paper_pdf_needs_both_latexmk_and_xelatex(monkeypatch):
    monkeypatch.setattr(
        capabilities.shutil, "which", lambda name: "/usr/bin/x" if name == "latexmk" else None
    )
    assert capabilities.export_capabilities()["paper_pdf"] is False

    capabilities.export_capabilities.cache_clear()
    monkeypatch.setattr(capabilities.shutil, "which", lambda _name: "/usr/bin/x")
    assert capabilities.export_capabilities()["paper_pdf"] is True


def test_init_db_creates_sqlite_schema_idempotently(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "init.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    init_db.main()
    init_db.main()  # 第二次运行不能因表已存在而失败

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "research_run" in tables
