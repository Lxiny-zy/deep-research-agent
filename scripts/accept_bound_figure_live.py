"""Validate scoped diagram evidence against a frozen real report; never rewrite its prose."""

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> None:
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.config import Settings
    from deep_research.llm import LLM
    from deep_research.observability import Tracer
    from deep_research.prompting import load_global_rules, structured_system_prompt
    from deep_research.registry import create
    from deep_research.report.service import requires_corroboration
    from deep_research.workbench.content_revision import REVISION_KEY, WRITERS
    from deep_research.workbench.contract import contract_from_scratch
    from deep_research.workbench.figure_review import (
        SCOPED_FIGURE_RULES,
        figure_units,
        review_figure,
    )
    from deep_research.workbench.figures import ConceptFigure
    from deep_research.workbench.intake import _FixedSources
    from deep_research.workbench.support import SupportDecisions, SupportReviewer, evidence_records
    from scripts.accept_delivery_live import remote_profile, render_case

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument(
        "--resume-figure",
        action="store_true",
        help="Resume the frozen scoped figure without repeating generation/calibration",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    raw = args.detail.read_bytes()
    detail = pickle.loads(raw)  # Trusted locally generated RunDetail only.
    scratch = copy.deepcopy(detail.orchestration.checkpoint["scratch"])
    contract = contract_from_scratch(scratch)
    if contract is None or contract.template not in {
        "autoResearch",
        "litReview",
        "paperRead",
        "slides",
    }:
        raise ValueError("Expected a research report with an optional concept figure")
    profile = remote_profile(args.authorized_ssh)
    tracer = Tracer()
    calls = []

    def save(name: str, value: object) -> None:
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / name).write_text(text, encoding="utf-8")

    class Capture(LLM):
        active_schema = ""

        async def parse(self, system, user, schema, **kwargs):
            self.active_schema = schema.__name__
            return await super().parse(system, user, schema, **kwargs)

        async def _stream_once(self, system, user, *, temperature=0.4):
            record = {"schema": self.active_schema, "input_chars": len(system) + len(user)}
            number = len(calls) + 1
            calls.append(record)
            chunks, started = [], time.monotonic()
            try:
                async for chunk in super()._stream_once(system, user, temperature=temperature):
                    chunks.append(chunk)
                    yield chunk
                record["status"] = "complete"
            finally:
                record.update(output="".join(chunks), seconds=time.monotonic() - started)
                save(f"response-{number}.json", record)

    settings = Settings(
        llm_api_key=profile["api_key"],
        llm_base_url=profile["base_url"],
        llm_model=profile["model"],
        quality=contract.quality,
        require_corroboration=requires_corroboration(detail),
        request_timeout=180,
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
    tracer.cache_scope = "bound-figure:" + detail.id
    evidence = evidence_records(
        detail.results,
        {u: i for i, u in enumerate(detail.report.citations, 1)},
        corroboration=requires_corroboration(detail),
    )

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(
                f"Figure acceptance running; calls={len(calls)}; tokens={tracer.total_tokens}",
                flush=True,
            )

    beat = asyncio.create_task(heartbeat())
    try:
        calibration_tokens, calibration_calls = 0, 0
        if not args.resume_figure:
            # The exact source behind the observed sc61 subtype overgeneralization.
            match = next(
                e
                for e in evidence
                if "10--21" in e["quote"] and "deep learning methods" in e["quote"]
            )
            cite = match["citation"]
            calibration = ConceptFigure(
                title="文中实验范围校准",
                evidence_mode="scoped",
                nodes=[
                    {"id": "m", "label": "测量模型失配"},
                    {"id": "u", "label": "迭代算法深度展开"},
                    {"id": "d", "label": "文中评估的深度学习方法"},
                ],
                edges=[
                    {"source": "m", "target": "u", "label": "退化10–21dB", "citations": [cite]},
                    {"source": "m", "target": "d", "label": "退化10–21dB", "citations": [cite]},
                ],
            )
            reviewer = SupportReviewer(
                llm,
                evidence,
                settings.llm_max_input_chars,
                context=detail.query,
                system_rules=SCOPED_FIGURE_RULES,
            )
            calibrated = await review_figure(calibration, reviewer)
            decisions = {d["unit_id"]: d for d in calibrated["decisions"]}
            units = figure_units(calibration, [])
            checks = {
                "subtype_rejected": decisions[units[-2].id]["verdict"]
                in {"unsupported", "uncertain"},
                "reported_scope_supported": decisions[units[-1].id]["verdict"] == "supported",
            }
            save(
                "calibration.json",
                {
                    "figure": calibration.model_dump(mode="json"),
                    "review": calibrated,
                    "checks": checks,
                    "tokens": tracer.total_tokens,
                },
            )
            if not all(checks.values()):
                raise ValueError("Scoped diagram review failed the positive/negative calibration")
            print(
                "Scoped evidence rejects the unsupported subtype and accepts the reported class",
                flush=True,
            )
            calibration_tokens, calibration_calls = tracer.total_tokens, len(calls)
            prior = ConceptFigure.model_validate(scratch["workbench"]["extras"]["concept_figure"])
            legacy = SupportReviewer(llm, evidence, settings.llm_max_input_chars)
            old_units = figure_units(prior, sorted({e["citation"] for e in evidence}))
            old_system_size = len(structured_system_prompt(legacy.system, SupportDecisions))
            old_plan = legacy._batches(old_units, old_system_size)
            save(
                "legacy-plan.json",
                {
                    "units": len(old_units),
                    "batches": len(old_plan),
                    "input_chars": sum(
                        old_system_size + len(legacy._prompt(batch)) for batch in old_plan
                    ),
                },
            )
        if args.resume_figure:
            from deep_research.workbench.figure_edit import prime_figure

            prior = ConceptFigure.model_validate(scratch["workbench"]["extras"]["concept_figure"])
            bound = SupportReviewer(llm, evidence, settings.llm_max_input_chars)
            if prior.evidence_mode != "scoped" or not prime_figure(
                bound, prior, scratch["workbench"]["extras"].get("figure_review")
            ):
                raise ValueError("Resume requires a matching scoped figure review")
        scratch[REVISION_KEY] = {"parent_run_id": detail.id}
        bb = Blackboard(
            query=detail.report.query,
            results=copy.deepcopy(detail.results),
            report=detail.report.model_copy(deep=True),
            scratch=scratch,
        )
        before = [r.material_data() for r in bb.results]

        class NoSearch(_FixedSources):
            async def search(self, *args, **kwargs):
                raise AssertionError("Diagram acceptance must not retrieve materials")

        writer = create(WRITERS[contract.template])

        async def no_prose(*args, **kwargs):
            raise AssertionError("Diagram acceptance must not rewrite approved prose")

        writer.write = no_prose
        ctx = RunContext(
            llm=llm,
            search_tool=NoSearch([]),
            tracer=tracer,
            settings=settings,
            global_rules=load_global_rules(),
        )
        await writer.step(bb, ctx)
        assert before == [r.material_data() for r in bb.results]
        assert bb.report.markdown == detail.report.markdown
        detail.report, detail.results = bb.report, bb.results
        detail.orchestration.checkpoint["scratch"] = bb.scratch
        frozen = pickle.dumps(detail)
        if profile["api_key"].encode() in frozen:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / "local-detail.pickle").write_bytes(frozen)
        metadata = {
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "mode": "scoped figure only; approved prose and findings unchanged",
            "model": profile["model"],
            "new_tokens": tracer.total_tokens - calibration_tokens,
            "calls": len(calls) - calibration_calls,
            "calibration_tokens": calibration_tokens,
        }
        save("model-run.json", metadata)
        registry = render_case(
            detail, args.output / "delivery", None, metadata, storage_root=args.storage_root
        )
        save("acceptance.json", registry)
        print(
            f"Bound figure: {registry['status']}; files={len(registry['items'])}; "
            f"figure calls={metadata['calls']}",
            flush=True,
        )
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Bound figure acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
