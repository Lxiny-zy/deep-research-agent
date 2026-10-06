"""Loopback-only browser fixture: real API/SQLite/files, synthetic completed research.

No dotenv is loaded. The launcher gives this process a new working directory and a
minimal environment. Model-producing endpoints are disabled by this test wrapper.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Request

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main() -> None:
    workspace = Path.cwd().resolve()
    if os.environ.get("DR_BROWSER_FIXTURE_BOOTSTRAP") != "1" or not workspace.name.startswith(
        "dr-browser-integration-"
    ):
        raise RuntimeError("Use npm run test:browser:integration to allocate an isolated workspace")
    for variable in ("DR_ARTIFACT_ROOT", "RUNTIME_CONFIG_PATH"):
        if not Path(os.environ[variable]).resolve().is_relative_to(workspace):
            raise RuntimeError("Browser fixture files must remain in its allocated workspace")
    expected_database = f"sqlite+aiosqlite:///{(workspace / 'browser.db').as_posix()}"
    if os.environ.get("DATABASE_URL") != expected_database:
        raise RuntimeError("Browser fixture requires its own temporary SQLite database")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    import uvicorn
    from fastapi import Depends, HTTPException
    from reading_fixture import seed_reading_fixture
    from starlette.responses import JSONResponse

    from deep_research import api
    from deep_research.models import Report, ResearchResult, Source
    from deep_research.orchestrator import create_initial_execution
    from deep_research.workbench.attachments import (
        ATTACHMENTS_SCRATCH_KEY,
        attachment_url,
        parse_attachment,
        save_original,
    )
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
    from deep_research.workbench.qa_store import QaMessage
    from deep_research.workbench.templates import get_template
    from tests.fakes import verified_finding

    metadata: dict[str, str] = {}

    @asynccontextmanager
    async def fixture_lifespan(app):
        async with api.lifespan(app):
            import pymupdf

            settings, repo = app.state.settings, app.state.repo
            document = pymupdf.open()
            document.new_page().insert_text((72, 72), "Controlled source paper. Page one.")
            quote = "Reconstruction PSNR reaches 38.4 dB on CAVE."
            document.new_page().insert_text((72, 180), quote)
            raw = document.tobytes()
            document.close()
            attachment = await parse_attachment(raw, "controlled-paper.pdf", "application/pdf")
            save_original(settings, attachment.id, raw)
            attachment.stored = True
            template = get_template("paperRead")
            query = "Browser integration original report"
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
                    markdown=(
                        "## Original report\n\nOriginal frozen body for browser integration."
                        "\n\nAlpha scored 95 [1]."
                    ),
                    citations=["https://example.invalid/controlled-source"],
                ),
            )
            import hashlib

            source = Source(
                url="https://example.invalid/controlled-source",
                content="controlled source: Alpha scored 95",
                locator="page 2, paragraph 3",
            )
            source.content_hash = hashlib.sha256(source.content.encode()).hexdigest()
            finding = verified_finding("Alpha scored 95", source.url, "Alpha scored 95")
            finding.verification.source_content_hash = source.content_hash
            await repo.save_result(run_id, ResearchResult(sub_question="方法", findings=[finding]))
            await repo.save_sources(run_id, [source])
            await repo.set_status(run_id, "done")
            conversation = await app.state.qa_store.create(
                "browser-alice", "Controlled PDF reading", run_id
            )
            chunk = next(item for item in attachment.chunks if quote in item.content)
            source_url = attachment_url(attachment.id, chunk.ordinal)
            await app.state.qa_store.append(
                conversation.id,
                QaMessage(
                    id="synthetic-answer",
                    position=0,
                    query="Which PSNR was reported?",
                    answer="The reported PSNR is 38.4 dB on CAVE [1].",
                    citations=[source_url],
                    evidence=[
                        {
                            "statement": "The reported PSNR is 38.4 dB on CAVE.",
                            "source_url": source_url,
                            "evidence_quote": quote,
                            "source_reference": "controlled-paper.pdf page 2",
                            "origin": "paper",
                        }
                    ],
                ),
            )
            bob, _ = await repo.create_run_once(
                "Bob private record", request_hash="", owner_id="browser-bob"
            )
            await repo.save_report(
                bob, Report(query="Bob private record", markdown="Bob private body", citations=[])
            )
            await repo.set_status(bob, "done")
            from deep_research.persistence import orm

            async with repo._sm() as session, session.begin():
                calls = [
                    ("operations-a", "started", 1, None),
                    ("operations-a", "failed", 1, None),
                    ("operations-a", "started", 1, None),
                    ("operations-b", "started", 2, "transport_retry"),
                    ("operations-b", "succeeded", 2, "transport_retry"),
                ]
                for sequence, (call_id, status, attempt, retry) in enumerate(calls, 1):
                    session.add(
                        orm.EventRow(
                            run_id=run_id,
                            seq=sequence,
                            attempt=1,
                            stage="LLM",
                            type="info",
                            message="fixture-only-private-event",
                            elapsed=1,
                            tokens=0,
                            tokens_estimated=False,
                            data={
                                "model_call": {
                                    "call_id": call_id,
                                    "status": status,
                                    "attempt": attempt,
                                    "retry_reason": retry,
                                    "usage_state": "unavailable",
                                    "duration_ms": 100,
                                }
                            },
                        )
                    )
                session.add(
                    orm.EventRow(
                        run_id=bob,
                        seq=1,
                        attempt=1,
                        stage="LLM",
                        type="info",
                        message="fixture-only-private-bob-event",
                        elapsed=1,
                        tokens=0,
                        tokens_estimated=False,
                        data={
                            "model_call": {
                                "call_id": "operations-bob",
                                "status": "succeeded",
                                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                            }
                        },
                    )
                )
            metadata.update(
                run_id=run_id,
                bob_run_id=bob,
                conversation_id=conversation.id,
                document_id=f"att-{attachment.id}",
                quote=quote,
            )
            metadata.update(await seed_reading_fixture(api, app))
            yield

    api.app.router.lifespan_context = fixture_lifespan

    @api.app.middleware("http")
    async def forbid_model_execution(request, call_next):
        path = request.url.path
        if request.method != "GET" and (
            path in {"/api/runs", "/api/intent/assess"}
            or "/messages" in path
            or path.endswith("/resume")
        ):
            return JSONResponse(
                {"detail": "Model-producing actions disabled in browser fixture"}, status_code=403
            )
        return await call_next(request)

    def require_fixture_admin(request):
        if api.principal_for(request).id != "admin":
            raise HTTPException(403, "Fixture control requires its synthetic administrator")

    @api.app.get("/__browser_fixture__/state", dependencies=[Depends(api.require_api_key)])
    async def state(request: Request):
        return metadata

    @api.app.post("/__browser_fixture__/next-version", dependencies=[Depends(api.require_api_key)])
    async def next_version(request: Request):
        require_fixture_admin(request)
        revision = int(metadata.get("revision", "0")) + 1
        metadata["revision"] = str(revision)
        await request.app.state.repo.save_report(
            metadata["run_id"],
            Report(
                query="Browser integration original report",
                markdown=(
                    "## Changed report\n\nNew frozen body after a second editor saves. "
                    f"Revision {revision}.\n\nAlpha scored 95 [1]."
                ),
                citations=["https://example.invalid/controlled-source"],
            ),
        )
        return {"changed": True}

    server = uvicorn.Server(
        uvicorn.Config(
            api.app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning"
        )
    )

    @api.app.post(
        "/__browser_fixture__/invalidate-reading", dependencies=[Depends(api.require_api_key)]
    )
    async def invalidate_reading(request: Request):
        require_fixture_admin(request)
        detail = await request.app.state.repo.get_run(metadata["reading_run_id"])
        await request.app.state.repo.save_report(
            metadata["reading_run_id"],
            Report(
                query=detail.report.query,
                markdown=detail.report.markdown + "\n\nAdditional synthetic paragraph.",
                citations=detail.report.citations,
            ),
        )
        return {"changed": True}

    @api.app.post("/__browser_fixture__/shutdown", dependencies=[Depends(api.require_api_key)])
    async def shutdown(request: Request):
        require_fixture_admin(request)
        server.should_exit = True
        return {"stopping": True}

    # Production's SPA fallback is intentionally last; fixture-only controls must
    # precede it, otherwise an HTTP 200 HTML shell can masquerade as readiness.
    api.app.router.routes.sort(
        key=lambda route: not getattr(route, "path", "").startswith("/__browser_fixture__/")
    )
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
