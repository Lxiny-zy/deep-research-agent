"""Compare extraction schemas on the same labelled synthetic measurements with a real model."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile


async def main() -> None:
    from deep_research.agents.base import direct_system_prompt
    from deep_research.agents.researcher import SYSTEM, source_context
    from deep_research.guardrails import EvidenceVerifier, SemanticEvidenceVerifier, report_eligible
    from deep_research.llm import LLM
    from deep_research.models import ExtractedFindingList, FindingList, ScholarlyMetadata, Source
    from deep_research.observability import Tracer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.CRITICAL)
    profile = remote_profile(args.authorized_ssh)

    def save(name, value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / name).write_text(text, encoding="utf-8")

    calls = []

    class Capture(LLM):
        async def _complete_once(self, system, user, temperature):
            answer = await super()._complete_once(system, user, temperature)
            call = {
                "input_chars": len(system) + len(user),
                "output_chars": len(answer),
                "output": answer,
            }
            calls.append(call)
            save(f"response-{len(calls)}.json", call)
            return answer

    tracer = Tracer()
    tracer.cache_scope = "local-extraction-schema-comparison"
    llm = Capture.from_params(
        tracer,
        api_key=profile["api_key"],
        base_url=profile["base_url"],
        model=profile["model"],
        timeout=180,
        user_agent="deep-research-local-acceptance",
        temperature=profile.get("temperature", 0.3),
        parameter_mode=profile.get("parameter_mode", "temperature"),
        reasoning_effort=profile.get("reasoning_effort", "medium"),
        context_window_tokens=profile.get("context_window_tokens"),
        max_output_tokens=profile.get("max_output_tokens"),
    )
    sources = [
        Source(
            title="Synthetic Alpha measurement",
            url="https://example.org/alpha",
            content=(
                "In this labelled synthetic test, Alpha reaches PSNR 38.4 dB on CAVE "
                "with 31 bands and 256x256 crops."
            ),
            scholarly=ScholarlyMetadata(section="results"),
        ),
        Source(
            title="Synthetic Beta measurement",
            url="https://example.org/beta",
            content=(
                "In this labelled synthetic test, Beta reaches SSIM 0.948 on CAVE "
                "with 31 bands and 256x256 crops."
            ),
            scholarly=ScholarlyMetadata(section="results"),
        ),
    ]
    user = (
        source_context(sources) + "\n子问题：仅抽取 Alpha 的 PSNR 与 Beta 的 SSIM 两条测量事实，"
        "保留数据集、波段数和裁剪尺寸。"
        "明确它们是合成验收数据，不是实际科研测量。"
    )
    results = []
    try:
        for label, schema in (
            ("persisted-schema", FindingList),
            ("extraction-schema", ExtractedFindingList),
        ):
            before, first_call, started = tracer.total_tokens, len(calls), time.monotonic()
            print(f"Extraction comparison: {label}", flush=True)
            extracted = await llm.parse(direct_system_prompt(SYSTEM), user, schema)
            extraction_tokens = tracer.total_tokens - before
            extraction_seconds = time.monotonic() - started
            raw_calls = calls[first_call:]
            candidates = [item.as_unverified() for item in extracted.findings]
            checked = []
            for candidate in candidates:
                source = next((s for s in sources if s.url == candidate.source_url), None)
                if source:
                    result = EvidenceVerifier().verify(candidate, source)
                    if result.accepted:
                        checked.append(result.finding)
            verified = await SemanticEvidenceVerifier().verify_batch(
                checked, llm, raise_errors=True
            )
            admitted = [f for f in verified if report_eligible(f)]
            record = {
                "schema": label,
                "extraction_tokens": extraction_tokens,
                "extraction_seconds": extraction_seconds,
                "extraction_calls": len(raw_calls),
                "input_chars": [c["input_chars"] for c in raw_calls],
                "output_chars": [c["output_chars"] for c in raw_calls],
                "schema_chars": len(json.dumps(schema.model_json_schema(), ensure_ascii=False)),
                "findings": [f.model_dump(mode="json") for f in verified],
                "admitted": len(admitted),
            }
            save(label + ".json", record)
            assert len(admitted) == 2
            by_entity = {f.entity.casefold(): f for f in admitted}
            assert by_entity["alpha"].quantity.value == 38.4
            assert by_entity["beta"].quantity.value == 0.948
            assert all(
                f.conditions.dataset == "CAVE" and f.conditions.bands == 31 for f in admitted
            )
            assert all(
                f.conditions.spatial_size.replace("×", "x").replace(" ", "") == "256x256"
                for f in admitted
            )
            results.append(record)
            print(
                f"{label}: accepted=2; tokens={extraction_tokens}; time={extraction_seconds:.1f}s",
                flush=True,
            )
        save(
            "acceptance.json",
            {
                "model": profile["model"],
                "scope": "two labelled synthetic measurements; no live search",
                "total_tokens": tracer.total_tokens,
                "results": results,
            },
        )
    finally:
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Extraction acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
