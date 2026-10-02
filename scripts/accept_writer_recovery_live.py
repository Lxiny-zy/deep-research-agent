"""Rebuild a task's deliverable from a trusted local run using the current writer policy.

Opt-in local acceptance only. Retains the original request, choices, quality and
evidence, refreshes code-owned template instructions, and preserves the old run.
Does not repeat retrieval/extraction, deploy or write server configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import pickle
import sys
import time
from contextlib import aclosing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile, render_case


async def main() -> None:
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.config import Settings
    from deep_research.llm import LLM
    from deep_research.observability import Tracer
    from deep_research.persistence.repository import RunDetail
    from deep_research.prompting import load_global_rules
    from deep_research.report.service import requires_corroboration
    from deep_research.workbench.contract import (
        CONTRACT_SCRATCH_KEY,
        build_contract,
        contract_from_scratch,
    )
    from deep_research.workbench.intake import _FixedSources
    from deep_research.workbench.templates import get_template
    from deep_research.workbench.writers import (
        MindmapWriter,
        PaperReader,
        PeerReviewer,
        ResearchWriter,
        SlideWriter,
        SurveyWriter,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument(
        "--detail", required=True, type=Path, help="Trusted locally created RunDetail pickle"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--storage-root", required=True, type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    raw = args.detail.read_bytes()
    detail = pickle.loads(raw)
    if not isinstance(detail, RunDetail) or not detail.orchestration:
        raise ValueError("Expected trusted local RunDetail with a task contract")
    scratch = copy.deepcopy(detail.orchestration.checkpoint["scratch"])
    old = contract_from_scratch(scratch)
    if old is None:
        raise ValueError("Missing original task contract")
    writers = {
        "peerReview": PeerReviewer,
        "paperRead": PaperReader,
        "litReview": SurveyWriter,
        "autoResearch": ResearchWriter,
        "slides": SlideWriter,
        "mindmap": MindmapWriter,
    }
    template = get_template(old.template)
    if template is None or old.template not in writers:
        raise ValueError("Unsupported writer recovery")
    contract = build_contract(
        template,
        old.original_request,
        answers=old.confirmed_choices,
        tier=old.tier,
        strategy=old.strategy,
        quality=old.quality,
    )
    assert contract.original_request == old.original_request
    assert contract.confirmed_choices == old.confirmed_choices
    args.output.mkdir(parents=True, exist_ok=False)
    profile = remote_profile(args.authorized_ssh)
    tracer = Tracer()
    tracer.cache_scope = "writer-recovery:" + detail.id

    def save(name: str, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in encoded:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / name).write_text(encoded, encoding="utf-8")

    class Capture(LLM):
        calls = 0

        async def _stream_once(self, system, user, *, temperature=0.4):
            self.calls += 1
            number, started, chunks = self.calls, time.monotonic(), []
            status = "interrupted"
            try:
                async with aclosing(
                    super()._stream_once(system, user, temperature=temperature)
                ) as stream:
                    async for chunk in stream:
                        chunks.append(chunk)
                        yield chunk
                status = "complete"
            finally:
                save(
                    f"response-{number}.json",
                    {
                        "status": status,
                        "seconds": time.monotonic() - started,
                        "input_chars": len(system) + len(user),
                        "output": "".join(chunks),
                    },
                )

    settings = Settings(
        quality=old.quality,
        require_corroboration=requires_corroboration(detail),
        request_timeout=max(180, profile.get("request_timeout", 180)),
    )
    llm = Capture.from_params(
        tracer,
        api_key=profile["api_key"],
        base_url=profile["base_url"],
        model=profile["model"],
        timeout=settings.request_timeout,
        user_agent=settings.llm_user_agent,
        temperature=profile.get("temperature", 0.3),
        parameter_mode=profile.get("parameter_mode", "temperature"),
        reasoning_effort=profile.get("reasoning_effort", "medium"),
        context_window_tokens=profile.get("context_window_tokens"),
        max_output_tokens=profile.get("max_output_tokens"),
    )
    scratch[CONTRACT_SCRATCH_KEY] = contract.model_dump(mode="json")
    bb = Blackboard(query=detail.query, results=copy.deepcopy(detail.results), scratch=scratch)
    evidence_before = [r.material_data() for r in bb.results]

    class NoRetrieval(_FixedSources):
        async def search(self, *args, **kwargs):
            raise AssertionError("Writer recovery must not retrieve or re-extract materials")

    metadata = {
        "mode": "writer recovery with retained evidence and refreshed template policy",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source": str(args.detail),
        "original_contract": old.model_dump(mode="json"),
        "recovery_contract": contract.model_dump(mode="json"),
        "findings": sum(len(r.findings) for r in bb.results),
        "model": profile["model"],
    }
    save("input.json", metadata)
    ctx = RunContext(
        llm=llm,
        search_tool=NoRetrieval([]),
        tracer=tracer,
        settings=settings,
        global_rules=load_global_rules(),
    )
    tracer.subscribe(
        lambda e: (
            print(
                f"{e.elapsed:.0f}s {e.stage}: {e.message[:200]}".replace(
                    profile["api_key"], "[REDACTED]"
                ),
                flush=True,
            )
            if e.stage != "LLM" and e.type in {"start", "done", "info", "error"}
            else None
        )
    )
    print(f"Reusing {metadata['findings']} findings for {old.template}; no retrieval", flush=True)

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(f"Still running; tokens={tracer.total_tokens}", flush=True)

    beat = asyncio.create_task(heartbeat())
    try:
        await writers[old.template]().step(bb, ctx)
        assert [r.material_data() for r in bb.results] == evidence_before
        detail.results, detail.report = bb.results, bb.report
        detail.orchestration.checkpoint.update(bb.model_dump(mode="json"))
        next_seq = max((e.seq for e in detail.events if e.seq is not None), default=-1) + 1
        detail.events.extend(
            e.model_copy(update={"seq": next_seq + i, "elapsed": detail.elapsed + e.elapsed})
            for i, e in enumerate(tracer.events)
        )
        detail.total_tokens += tracer.total_tokens
        detail.elapsed += tracer.elapsed
        detail.metrics = None
        frozen = pickle.dumps(detail)
        if profile["api_key"].encode() in frozen:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / "local-detail.pickle").write_bytes(frozen)
        metadata.update(new_tokens=tracer.total_tokens, calls=llm.calls)
        save("model-run.json", metadata)
        registry = render_case(
            detail, args.output / "delivery", None, metadata, storage_root=args.storage_root
        )
        save("acceptance.json", registry)
        print(f"Writer recovery: {registry['status']}; files={len(registry['items'])}", flush=True)
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Writer recovery stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
