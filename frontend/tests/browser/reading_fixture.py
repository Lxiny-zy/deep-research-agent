"""Controlled reading-map run for the isolated real-API browser harness."""

from __future__ import annotations

import hashlib
from typing import Any


async def seed_reading_fixture(api: Any, app: Any) -> dict[str, str]:
    import pymupdf

    from deep_research.models import Report, ResearchResult, Source
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workbench.attachments import (
        ATTACHMENTS_SCRATCH_KEY,
        AttachmentChunk,
        attachment_url,
        parse_attachment,
        save_original,
    )
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
    from deep_research.workbench.prose_review import reviewer_for_report
    from deep_research.workbench.templates import get_template
    from tests.fakes import FakeLLM, verified_finding

    settings, repo = app.state.settings, app.state.repo
    alpha, beta = "Alpha scored 95", "Beta scored 96"
    with pymupdf.open() as pdf:
        first = pdf.new_page()
        first.insert_text((72, 72), "Controlled reading-map document. Introductory page.")
        second = pdf.new_page()
        second.insert_text((72, 150), beta)
        second.insert_text((72, 180), "This is the unique result on page two.")
        page_texts = [pdf[i].get_text("text") for i in range(len(pdf))]
        raw = pdf.tobytes()
    attachment = await parse_attachment(raw, "reading-map-paper.pdf", "application/pdf")
    # The fixture stores actual per-page text, not guessed model page positions.
    attachment.chunks = [
        AttachmentChunk(
            ordinal=i,
            locator=f"page {i + 1}",
            content=text,
            page=i + 1,
        )
        for i, text in enumerate(page_texts)
    ]
    attachment.char_count = sum(len(text) for text in page_texts)
    save_original(settings, attachment.id, raw)
    attachment.stored = True
    template = get_template("autoResearch")
    assert template is not None
    query = "Controlled reading-map: compare Alpha and Beta evidence."
    execution = create_initial_execution(query, template.workflow, settings)
    scratch = execution.checkpoint.setdefault("scratch", {})
    scratch[CONTRACT_SCRATCH_KEY] = build_contract(template, query).model_dump(mode="json")
    scratch[ATTACHMENTS_SCRATCH_KEY] = [attachment.model_dump(mode="json")]
    run_id, _ = await repo.create_run_once(
        query,
        request_hash="",
        owner_id="browser-alice",
        execution=execution,
    )
    alpha_source = Source(
        url="https://example.invalid/reading-map-alpha",
        title="Controlled repeated text",
        content=f"First experiment. {alpha}. Second experiment. {alpha}.",
        locator="controlled text, repeated quote",
    )
    beta_source = Source(
        url=attachment_url(attachment.id, 1),
        title=attachment.filename,
        content=page_texts[1],
        locator="page 2",
    )
    findings = []
    for label, quote, source in [("Alpha", alpha, alpha_source), ("Beta", beta, beta_source)]:
        source.content_hash = hashlib.sha256(source.content.encode()).hexdigest()
        finding = verified_finding(quote, source.url, quote)
        finding.entity = label
        finding.verification.source_content_hash = source.content_hash
        finding.verification.claim_id = "browser-reading-" + label.lower()
        findings.append(finding)
    await repo.save_report(
        run_id,
        Report(
            query=query,
            markdown=f"## 方法结果\n\n{alpha} [1].\n\n## 独立论文结果\n\n{beta} [2].",
            citations=[alpha_source.url, beta_source.url],
        ),
    )
    await repo.save_result(run_id, ResearchResult(sub_question="Results", findings=findings))
    await repo.save_sources(run_id, [alpha_source, beta_source])
    detail = await repo.get_run(run_id)
    assert detail is not None and detail.report is not None and detail.orchestration is not None
    scratch = detail.orchestration.checkpoint["scratch"]
    checker = reviewer_for_report(
        FakeLLM(),
        query,
        detail.results,
        detail.report.citations,
        scratch,
        50000,
        sources=detail.sources,
    )
    assert checker is not None
    record = await checker.review(detail.report.markdown)
    bound, _ = checker.check(detail.report.markdown, record)
    if not bound:
        raise RuntimeError("Controlled reading-map review must be bound before browser testing")
    scratch["prose_review"] = record
    await repo.save_orchestration(run_id, detail.orchestration)
    await repo.set_status(run_id, "done")
    metadata = {
        "reading_run_id": run_id,
        "reading_pdf_document_id": "att-" + attachment.id,
        "reading_alpha_quote": alpha,
        "reading_beta_quote": beta,
    }
    metadata.update(await seed_counter_fixture(api, app))
    return metadata


async def seed_counter_fixture(api: Any, app: Any) -> dict[str, str]:
    """A deliberately rejected absence claim with a real bound source counterexample."""
    import pymupdf

    from deep_research.models import Report, ResearchResult
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workbench.attachments import (
        ATTACHMENTS_SCRATCH_KEY,
        parse_attachment,
        save_original,
    )
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
    from deep_research.workbench.fulltext_review import FullTextChecks, FullTextTarget
    from deep_research.workbench.prose_review import reviewer_for_report
    from deep_research.workbench.templates import get_template
    from tests.fakes import FakeLLM, verified_finding
    from tests.test_fulltext_absence import FullTextJudge

    quote = "Implementation uses T = 4."
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 120), quote)
        raw = pdf.tobytes()
    settings, repo = app.state.settings, app.state.repo
    attachment = await parse_attachment(raw, "counterexample-paper.pdf", "application/pdf")
    save_original(settings, attachment.id, raw)
    attachment.stored = True
    source = attachment.sources()[0]
    source.content_hash = hashlib.sha256(source.content.encode()).hexdigest()
    query = "核对实现细节是否给出了T的取值"
    template = get_template("autoResearch")
    assert template is not None
    execution = create_initial_execution(query, template.workflow, settings)
    scratch = execution.checkpoint.setdefault("scratch", {})
    scratch[CONTRACT_SCRATCH_KEY] = build_contract(template, query).model_dump(mode="json")
    scratch[ATTACHMENTS_SCRATCH_KEY] = [attachment.model_dump(mode="json")]
    run_id, _ = await repo.create_run_once(
        query, request_hash="", owner_id="browser-alice", execution=execution
    )
    await repo.save_report(
        run_id,
        Report(
            query=query,
            markdown="## 实现细节\n\n本论文未给出 T 的具体取值 [1]。",
            citations=[source.url],
        ),
    )
    await repo.save_sources(run_id, [source])
    finding = verified_finding(quote, source.url, quote)
    finding.verification.source_content_hash = source.content_hash
    await repo.save_result(
        run_id, ResearchResult(sub_question="Implementation", findings=[finding])
    )

    class CounterJudge(FakeLLM):
        async def parse(self, system, user, schema, **kwargs):
            if schema in {FullTextChecks, FullTextTarget}:
                return await FullTextJudge(counter=quote).parse(system, user, schema, **kwargs)
            return await super().parse(system, user, schema, **kwargs)

    detail = await repo.get_run(run_id)
    assert detail is not None and detail.report is not None and detail.orchestration is not None
    scratch = detail.orchestration.checkpoint["scratch"]
    checker = reviewer_for_report(
        CounterJudge(),
        query,
        detail.results,
        detail.report.citations,
        scratch,
        50000,
        sources=detail.sources,
    )
    assert checker is not None
    record = await checker.review(detail.report.markdown)
    bound, _ = checker.check(detail.report.markdown, record)
    if not bound or not any(
        d.get("fulltext_review", {}).get("status") == "refuted"
        for d in record["decisions"]
        if d.get("fulltext_review")
    ):
        raise RuntimeError("Counterexample fixture requires a bound, rejected absence claim")
    scratch["prose_review"] = record
    await repo.save_orchestration(run_id, detail.orchestration)
    await repo.set_status(run_id, "needs_review")
    return {
        "reading_counter_run_id": run_id,
        "reading_counter_quote": quote,
        "reading_counter_document_id": "att-" + attachment.id,
    }
