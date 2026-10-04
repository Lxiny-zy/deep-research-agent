from __future__ import annotations

import multiprocessing
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from deep_research.artifacts import ArtifactStore
from deep_research.models import Report
from deep_research.persistence.repository import RunDetail
from deep_research.workbench.delivery_store import (
    INDEX,
    DeliveryConflict,
    _lock,
    build_or_load,
    delivery_store,
    load_version,
    retry_format,
    workspace_files,
)
from deep_research.workbench.publish import DeliveryBundle, DeliveryFile, build_bundle
from deep_research.workbench.workspace import read_workspace_file


def detail(body="version one"):
    return RunDetail(
        id="run-one",
        query="q",
        status="done",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        report=Report(query="q", markdown=body, citations=[]),
    )


def bundle(run):
    return DeliveryBundle(
        "test",
        "title",
        [DeliveryFile("report.md", "md", "title", "source", run.report.markdown.encode())],
        [],
        "pass",
        "2026-01-01T00:00:00Z",
    )


def test_restart_and_versioned_download_reuse_exact_saved_bytes(tmp_path):
    first = build_or_load(detail(), str(tmp_path), None, bundle)

    def forbidden(_):
        raise AssertionError("a saved version must not render again")

    restored = build_or_load(detail(), str(tmp_path), None, forbidden)
    second = build_or_load(detail("version two"), str(tmp_path), None, bundle)
    assert first.content_version == restored.content_version != second.content_version
    assert restored.files[0].data == first.files[0].data == b"version one"
    assert (
        load_version(detail(), str(tmp_path), first.content_version).files[0].data == b"version one"
    )
    files = workspace_files(detail(), str(tmp_path))
    assert len(files) == 2
    for record in files:
        data, _, _ = read_workspace_file(detail(), str(tmp_path), record["path"])
        assert data in {b"version one", b"version two"}


def test_support_policy_change_forces_a_new_delivery_version(tmp_path, monkeypatch):
    from deep_research.workbench import support

    first = build_or_load(detail(), str(tmp_path), None, bundle)
    monkeypatch.setattr(support, "SUPPORT_POLICY_VERSION", support.SUPPORT_POLICY_VERSION + 1)
    calls = []

    def rebuild(run):
        calls.append(run.id)
        return bundle(run)

    second = build_or_load(detail(), str(tmp_path), None, rebuild)
    assert calls and first.content_version != second.content_version
    assert (
        load_version(detail(), str(tmp_path), first.content_version).files[0].data == b"version one"
    )


def test_corrupted_version_is_not_overwritten_or_silently_regenerated(tmp_path):
    build_or_load(detail(), str(tmp_path), None, bundle)
    store, _ = delivery_store(detail(), str(tmp_path))
    path = workspace_files(detail(), str(tmp_path))[0]["path"]
    store.absolute_path(path).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="校验失败"):
        build_or_load(detail(), str(tmp_path), None, bundle)


def test_failed_commit_does_not_advertise_partial_files(tmp_path, monkeypatch):
    original = ArtifactStore.write_control_json

    def fail(self, name, *args, **kwargs):
        if name == INDEX and args and args[0].get("versions"):
            raise OSError("simulated commit failure")
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(ArtifactStore, "write_control_json", fail)
    with pytest.raises(OSError):
        build_or_load(detail(), str(tmp_path), None, bundle)
    assert workspace_files(detail(), str(tmp_path)) == []
    monkeypatch.setattr(ArtifactStore, "write_control_json", original)
    assert build_or_load(detail(), str(tmp_path), None, bundle).files[0].data == b"version one"


def _process_build(root: str) -> str:
    def build(run):
        with (Path(root) / "build-count.txt").open("a") as handle:
            handle.write("render\n")
        time.sleep(0.2)
        return bundle(run)

    return build_or_load(detail(), root, None, build).content_version


def test_two_processes_share_one_render(tmp_path):
    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
        versions = list(pool.map(_process_build, [str(tmp_path), str(tmp_path)]))
    assert versions[0] == versions[1]
    assert (tmp_path / "build-count.txt").read_text().splitlines() == ["render"]


def test_empty_lock_file_serializes_contenders_without_writing_locked_bytes(tmp_path):
    store, _ = delivery_store(detail(), str(tmp_path))
    waiting, entered = threading.Event(), threading.Event()

    def contender():
        waiting.set()
        with _lock(store):
            entered.set()

    with ThreadPoolExecutor(1) as pool:
        with _lock(store):
            assert store.control_path("deliveries/render.lock").stat().st_size == 0
            future = pool.submit(contender)
            assert waiting.wait(5)
            assert not entered.wait(0.1)
        future.result(timeout=5)
    assert entered.is_set()


def test_nested_workspace_and_legacy_storage_remain_readable(tmp_path):
    root = tmp_path / ("nested-" + "x" * max(1, 139 - len(str(tmp_path))))
    first = build_or_load(detail(), str(root), None, bundle)
    store, slug = delivery_store(detail(), str(root))
    assert len(str(store.absolute_path(workspace_files(detail(), str(root))[0]["path"]))) < 250
    assert load_version(detail(), str(root), first.content_version).files[0].data == b"version one"

    # Simulate an already published version using the previous full-digest path.
    legacy_store, legacy_slug = delivery_store(detail(), str(tmp_path))
    registry = first.registry()
    legacy_store.write(
        legacy_slug,
        f"deliverables-{first.content_version}",
        "report.md",
        b"version one",
        area="output",
        update_manifest=False,
    )
    legacy_store.write_control_json(INDEX, {"versions": {first.content_version: registry}})
    assert (
        load_version(detail(), str(tmp_path), first.content_version).files[0].data == b"version one"
    )


def test_storage_prefix_collision_does_not_overwrite_an_existing_version(tmp_path, monkeypatch):
    from deep_research.workbench import delivery_store as module

    prefix = "a" * 16
    monkeypatch.setattr(module, "delivery_fingerprint", lambda run: prefix + "1" * 48)
    first = build_or_load(detail(), str(tmp_path), None, bundle)
    monkeypatch.setattr(module, "delivery_fingerprint", lambda run: prefix + "2" * 48)
    second = build_or_load(detail("second"), str(tmp_path), None, bundle)
    assert (
        load_version(detail(), str(tmp_path), first.content_version).files[0].data == b"version one"
    )
    assert load_version(detail(), str(tmp_path), second.content_version).files[0].data == b"second"


def _failed_pdf(tmp_path, monkeypatch):
    from deep_research.workbench.delivery import pdf

    def fail(*args, **kwargs):
        raise pdf.PdfRenderError("temporary PDF failure")

    with monkeypatch.context() as patch:
        patch.setattr(pdf, "render_pdf", fail)
        return build_or_load(detail(), str(tmp_path), None, build_bundle)


def test_retry_only_renders_failed_format_and_retains_immutable_previous_version(
    tmp_path, monkeypatch
):
    from deep_research.workbench.delivery import docx, html

    first = _failed_pdf(tmp_path, monkeypatch)
    assert first.failures[0]["format"] == "pdf"
    old_bytes = {f.name: f.data for f in first.files}

    def forbidden(*args, **kwargs):
        raise AssertionError("a successful format must not be rendered again")

    monkeypatch.setattr(html, "render_html", forbidden)
    monkeypatch.setattr(docx, "render_docx", forbidden)
    second = retry_format(detail(), str(tmp_path), None, first.content_version, "pdf", "retry-once")
    assert second.parent_version == first.content_version and second.attempt == 2
    assert second.input_version == first.content_version != second.content_version
    assert not second.failures
    assert all(
        next(f.data for f in second.files if f.name == name) == data
        for name, data in old_bytes.items()
    )
    assert any(f.format == "pdf" for f in second.files)
    assert (
        load_version(detail(), str(tmp_path), first.content_version).registry() == first.registry()
    )
    assert (
        build_or_load(detail(), str(tmp_path), None, forbidden).content_version
        == second.content_version
    )
    repeated = retry_format(
        detail(), str(tmp_path), None, first.content_version, "pdf", "retry-once"
    )
    assert repeated.registry() == second.registry()
    for path in workspace_files(detail(), str(tmp_path)):
        assert read_workspace_file(detail(), str(tmp_path), path["path"])[0]


def test_retry_conflicts_do_not_rewrite_changed_inputs_or_successful_formats(tmp_path, monkeypatch):
    first = _failed_pdf(tmp_path, monkeypatch)
    with pytest.raises(DeliveryConflict, match="定稿"):
        retry_format(
            detail("new body"), str(tmp_path), None, first.content_version, "pdf", "request-1"
        )
    with pytest.raises(DeliveryConflict, match="未生成失败"):
        retry_format(detail(), str(tmp_path), None, first.content_version, "docx", "request-1")
    second = retry_format(detail(), str(tmp_path), None, first.content_version, "pdf", "request-1")
    with pytest.raises(DeliveryConflict, match="同一重试"):
        retry_format(detail(), str(tmp_path), None, first.content_version, "docx", "request-1")
    with pytest.raises(DeliveryConflict, match="新版本"):
        retry_format(detail(), str(tmp_path), None, first.content_version, "pdf", "request-2")
    assert (
        build_or_load(detail(), str(tmp_path), None, build_bundle).content_version
        == second.content_version
    )


def test_failed_retry_commit_keeps_current_version_and_can_repeat_request(tmp_path, monkeypatch):
    from deep_research.workbench.render_progress import RenderProgressError

    first = _failed_pdf(tmp_path, monkeypatch)
    write = ArtifactStore.write_control_json

    def fail_commit(self, *args, **kwargs):
        raise OSError("commit unavailable")

    monkeypatch.setattr(ArtifactStore, "write_control_json", fail_commit)
    with pytest.raises(RenderProgressError) as failed:
        retry_format(detail(), str(tmp_path), None, first.content_version, "pdf", "retry-request")
    assert isinstance(failed.value.__cause__, OSError)
    assert (
        build_or_load(detail(), str(tmp_path), None, build_bundle).content_version
        == first.content_version
    )
    monkeypatch.setattr(ArtifactStore, "write_control_json", write)
    second = retry_format(
        detail(), str(tmp_path), None, first.content_version, "pdf", "retry-request"
    )
    assert second.parent_version == first.content_version and not second.failures


def test_corrupt_pdf_candidate_does_not_mark_other_formats_failed(tmp_path, monkeypatch):
    from deep_research.workbench.delivery import pdf

    with monkeypatch.context() as patch:
        patch.setattr(pdf, "render_pdf", lambda *args, **kwargs: b"not a PDF")
        first = build_or_load(detail(), str(tmp_path), None, build_bundle)
    assert next(f for f in first.files if f.format == "pdf").status == "fail"
    assert all(f.status != "fail" for f in first.files if f.format in {"html", "docx"})
    assert any(f["format"] == "pdf" and f["retryable"] for f in first.failures)
    second = retry_format(detail(), str(tmp_path), None, first.content_version, "pdf", "repair-pdf")
    assert next(f for f in second.files if f.format == "pdf").data.startswith(b"%PDF")
    assert not second.failures


def _process_retry(args):
    from deep_research.workbench.delivery import pdf

    root, version = args
    original = pdf.render_pdf

    def render(*args, **kwargs):
        with (Path(root) / "retry-count.txt").open("a") as handle:
            handle.write("pdf\n")
        return original(*args, **kwargs)

    pdf.render_pdf = render
    return retry_format(detail(), root, None, version, "pdf", "shared-retry").content_version


def test_two_processes_share_one_format_retry(tmp_path, monkeypatch):
    first = _failed_pdf(tmp_path, monkeypatch)
    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
        versions = list(pool.map(_process_retry, [(str(tmp_path), first.content_version)] * 2))
    assert versions[0] == versions[1]
    assert (tmp_path / "retry-count.txt").read_text().splitlines() == ["pdf"]


@pytest.mark.parametrize(
    "template,format,module_name,function",
    [
        ("autoResearch", "html", "delivery.html", "render_html"),
        ("autoResearch", "docx", "delivery.docx", "render_docx"),
        ("slides", "pptx", "delivery.pptx", "render_pptx"),
        ("mindmap", "html", "delivery.mindmap", "render_mindmap_html"),
        ("mindmap", "png", "delivery.mindmap", "render_mindmap_png"),
        ("dataAnalysis", "xlsx", "delivery_render", "_stats_xlsx"),
    ],
)
async def test_other_task_formats_retry_from_frozen_context(
    template, format, module_name, function, tmp_path, monkeypatch, settings
):
    import importlib

    from deep_research.workbench import analysis
    from tests.test_workbench import _run

    run = detail()
    if template != "autoResearch":
        query = (
            "比较方法\nmethod,score\nA,1\nA,1.1\nA,1.2\nB,3\nB,3.2\nB,3.3"
            if template == "dataAnalysis"
            else "知识体系"
        )
        _, run, _ = await _run(template, query, "unused", settings)
    module = importlib.import_module("deep_research.workbench." + module_name)

    def fail(*args, **kwargs):
        raise RuntimeError("temporary renderer failure")

    with monkeypatch.context() as patch:
        patch.setattr(module, function, fail)
        first = build_or_load(run, str(tmp_path), None, build_bundle)
    assert any(item["format"] == format and item["retryable"] for item in first.failures)
    before = {file.name: file.data for file in first.files}

    def no_new_analysis(*args, **kwargs):
        raise AssertionError("a format retry must not recompute the analysis or figures")

    monkeypatch.setattr(analysis, "analyse", no_new_analysis)
    second = retry_format(run, str(tmp_path), None, first.content_version, format, "format-retry")
    assert not any(item["format"] == format for item in second.failures)
    assert any(file.format == format for file in second.files)
    assert all(
        next(file.data for file in second.files if file.name == name) == data
        for name, data in before.items()
    )
