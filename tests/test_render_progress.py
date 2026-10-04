"""Completed formats survive a crash before the first delivery commit."""

from collections import Counter

import pytest

from deep_research.models import Report
from deep_research.persistence.repository import RunDetail
from deep_research.workbench.delivery_render import render_bundle
from deep_research.workbench.delivery_store import build_or_load, current_version, delivery_store
from deep_research.workbench.gates import GateResult


class ProcessStopped(BaseException):
    pass


def detail(body="A verified result."):
    return RunDetail(
        id="render-resume", query="Q", status="running", report=Report(query="Q", markdown=body)
    )


def build(value):
    return render_bundle(
        {
            "title": "Report",
            "stem": "report",
            "markdown": value.report.markdown,
            "canonical_markdown": value.report.markdown,
            "template": "autoResearch",
            "extras": {},
            "citations": [],
            "evidence": [],
            "references": {},
            "base_gates": [
                GateResult(name, "pass").to_dict()
                for name in ("markdown", "structure", "length", "prose_evidence")
            ],
            "wants": ["md", "html", "docx", "pdf"],
            "blocked": False,
            "fail_on_quality": False,
            "generated_at": "2026-10-05T00:00:00Z",
            "meta": "",
            "kicker": "Research",
        },
        [],
    )


def counted_renderers(monkeypatch):
    from deep_research.workbench.delivery import docx, html, pdf

    counts = Counter()
    for name, module, method in [
        ("docx", docx, "render_docx"),
        ("html", html, "render_html"),
        ("pdf", pdf, "render_pdf"),
    ]:
        original = getattr(module, method)

        def render(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            if _name == "pdf" and counts[_name] == 1:
                raise ProcessStopped()
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, method, render)
    return counts


def test_formats_are_checkpointed_before_the_first_bundle_commit(tmp_path, monkeypatch):
    counts = counted_renderers(monkeypatch)
    value = detail()
    with pytest.raises(ProcessStopped):
        build_or_load(value, str(tmp_path), None, build)
    assert current_version(value, str(tmp_path)) is None
    store, slug = delivery_store(value, str(tmp_path))
    assert store.list_artifacts(slug) == []
    resumed = build_or_load(value, str(tmp_path), None, build)
    assert counts == {"html": 1, "docx": 1, "pdf": 2}
    assert resumed.status == "pass"
    assert {file.format for file in resumed.files} == {"md", "html", "docx", "pdf"}
    assert current_version(value, str(tmp_path)) == resumed.content_version


def test_changed_input_cannot_reuse_previous_format_checkpoints(tmp_path, monkeypatch):
    counts = counted_renderers(monkeypatch)
    with pytest.raises(ProcessStopped):
        build_or_load(detail(), str(tmp_path), None, build)
    resumed = build_or_load(detail("A different verified result."), str(tmp_path), None, build)
    assert counts == {"html": 2, "docx": 2, "pdf": 2}
    assert b"different verified result" in next(
        file.data for file in resumed.files if file.format == "html"
    )


def test_crash_while_committing_does_not_repeat_rendering(tmp_path, monkeypatch):
    from deep_research.artifacts import ArtifactStore
    from deep_research.workbench.delivery import docx

    counts = []
    original_render = docx.render_docx

    def render(*args, **kwargs):
        counts.append(1)
        return original_render(*args, **kwargs)

    monkeypatch.setattr(docx, "render_docx", render)
    original = ArtifactStore.write_control_json

    def stop_at_commit(self, name, data, **kwargs):
        if name == "deliveries/index.json" and data.get("versions"):
            raise ProcessStopped()
        return original(self, name, data, **kwargs)

    monkeypatch.setattr(ArtifactStore, "write_control_json", stop_at_commit)
    with pytest.raises(ProcessStopped):
        build_or_load(detail(), str(tmp_path), None, build)
    monkeypatch.setattr(ArtifactStore, "write_control_json", original)
    resumed = build_or_load(detail(), str(tmp_path), None, build)
    assert resumed.status == "pass" and len(counts) == 1


def test_damaged_pending_format_is_regenerated_without_repeating_other_formats(
    tmp_path, monkeypatch
):
    from deep_research.workbench.delivery_store import INDEX
    from deep_research.workbench.publish import delivery_fingerprint

    counts = counted_renderers(monkeypatch)
    value = detail()
    with pytest.raises(ProcessStopped):
        build_or_load(value, str(tmp_path), None, build)
    store, slug = delivery_store(value, str(tmp_path))
    pending = store.read_control_json(INDEX)["pending"][delivery_fingerprint(value)]
    store.write(
        slug,
        pending["storage_stage"],
        "report.docx",
        b"damaged",
        area="output",
        update_manifest=False,
    )
    result = build_or_load(value, str(tmp_path), None, build)
    assert counts == {"html": 1, "docx": 2, "pdf": 2}
    assert result.status == "pass"


def test_published_files_are_never_repaired_by_overwriting_the_original(tmp_path, monkeypatch):
    from deep_research.workbench.delivery_store import INDEX

    value = detail()
    first = build_or_load(value, str(tmp_path), None, build)
    store, slug = delivery_store(value, str(tmp_path))
    registry = store.read_control_json(INDEX)["versions"][first.content_version]
    store.write(
        slug,
        registry["storage_stage"],
        "report.docx",
        b"damaged",
        area="output",
        update_manifest=False,
    )
    calls = []

    def forbidden(_):
        calls.append(1)
        raise AssertionError("must not rerender a committed version")

    with pytest.raises(ValueError, match="校验失败"):
        build_or_load(value, str(tmp_path), None, forbidden)
    assert calls == []


def test_storage_failure_stops_before_rendering_additional_formats(tmp_path, monkeypatch):
    from deep_research.artifacts import ArtifactStore
    from deep_research.workbench.render_progress import RenderProgressError

    counts = counted_renderers(monkeypatch)
    original = ArtifactStore.write

    def fail_after_html(self, slug, stage, name, data, **kwargs):
        if name.endswith(".html"):
            raise OSError("disk full")
        return original(self, slug, stage, name, data, **kwargs)

    monkeypatch.setattr(ArtifactStore, "write", fail_after_html)
    with pytest.raises(RenderProgressError):
        build_or_load(detail(), str(tmp_path), None, build)
    assert counts == {"html": 1}


def test_checkpoint_handle_cannot_mutate_a_published_version(tmp_path):
    from deep_research.workbench.render_progress import RenderProgressError, current_progress

    handles = []

    def capture(value):
        handles.append(current_progress())
        return build(value)

    value = detail()
    saved = build_or_load(value, str(tmp_path), None, capture)
    with pytest.raises(RenderProgressError):
        handles[0].save(saved.files[0])
    assert (
        build_or_load(value, str(tmp_path), None, capture).files[0].sha256 == saved.files[0].sha256
    )


def test_explicit_format_retry_bypasses_any_ambient_initial_checkpoint(tmp_path, monkeypatch):
    from deep_research.workbench.delivery_store import _index
    from deep_research.workbench.publish import delivery_fingerprint
    from deep_research.workbench.render_progress import checkpoint_rendering, render_file

    value = detail()
    store, slug = delivery_store(value, str(tmp_path))
    calls = []

    def generate():
        calls.append(1)
        return b"Fresh source"

    with checkpoint_rendering(store, slug, delivery_fingerprint(value), _index(store)):
        render_file("report.md", "md", "source", "source", generate)
        render_file("report.md", "md", "source", "source", generate, checkpoint=False)
    assert len(calls) == 2


@pytest.mark.parametrize("stop_at", ["plot", "pdf", "commit"])
def test_statistical_pngs_survive_rendering_interruption(tmp_path, settings, monkeypatch, stop_at):
    from deep_research.artifacts import ArtifactStore
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workbench import analysis, delivery_render
    from deep_research.workbench.contract import TaskContract
    from deep_research.workbench.delivery import pdf
    from deep_research.workbench.publish import build_bundle

    csv = (
        "a,b,c,d,e\n1.1,2.1,3.1,4.1,5.1\n2.4,4.2,2.3,5.2,7.3\n"
        "3.2,2.9,7.1,2.4,8.7\n5.7,3.8,4.5,8.3,3.2\n"
    )
    computed = analysis.analyse(csv)
    execution = create_initial_execution("Data", "data_analysis", settings)
    execution.checkpoint["scratch"].update(
        {
            "task_contract": TaskContract(
                title="Data", template="dataAnalysis", original_request="", dataset_csv=csv
            ).model_dump(mode="json"),
            "analysis": computed.snapshot(),
            "workbench": {"template": "dataAnalysis", "extras": {}},
        }
    )
    value = RunDetail(
        id="analysis-render",
        query="Data",
        status="running",
        orchestration=execution,
        report=Report(query="Data", markdown=analysis.fallback_report(computed)),
    )
    counts = Counter()
    original_png, original_pdf = analysis._png, pdf.render_pdf
    original_xlsx = delivery_render._stats_xlsx
    original_write = ArtifactStore.write_control_json

    def png(fig):
        counts["png"] += 1
        if stop_at == "plot" and counts["png"] == 2:
            import matplotlib.pyplot as plt

            plt.close(fig)
            raise ProcessStopped()
        return original_png(fig)

    def render_pdf(*args, **kwargs):
        counts["pdf"] += 1
        if stop_at == "pdf" and counts["pdf"] == 1:
            raise ProcessStopped()
        return original_pdf(*args, **kwargs)

    monkeypatch.setattr(analysis, "_png", png)
    monkeypatch.setattr(pdf, "render_pdf", render_pdf)

    def xlsx(result):
        counts["xlsx"] += 1
        return original_xlsx(result)

    def stop_commit(self, name, data, **kwargs):
        if stop_at == "commit" and name == "deliveries/index.json" and data.get("versions"):
            raise ProcessStopped()
        return original_write(self, name, data, **kwargs)

    monkeypatch.setattr(delivery_render, "_stats_xlsx", xlsx)
    monkeypatch.setattr(ArtifactStore, "write_control_json", stop_commit)
    with pytest.raises(ProcessStopped):
        build_or_load(value, str(tmp_path), None, build_bundle)
    monkeypatch.setattr(ArtifactStore, "write_control_json", original_write)
    if stop_at in {"pdf", "commit"}:

        def no_recompute(*args, **kwargs):
            raise AssertionError("completed statistical rendering must be reused")

        monkeypatch.setattr(analysis, "analyse", no_recompute)
    resumed = build_or_load(value, str(tmp_path), None, build_bundle)
    assert counts["png"] == len(computed.figures) + (stop_at == "plot")
    assert counts["xlsx"] == 1
    assert len([file for file in resumed.files if file.format == "png"]) == len(computed.figures)
    assert all(file.status != "fail" for file in resumed.files)
