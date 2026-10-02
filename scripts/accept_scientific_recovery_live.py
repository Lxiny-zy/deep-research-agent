"""Recheck saved numeric candidates against superscripts recovered from the same PDF."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile


def flatten_exponents(text: str) -> tuple[str, list[int]]:
    """Remove only scientific carets for matching the old extraction, keeping offsets."""
    removed = {
        match.end() - 1
        for match in re.finditer(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)\s*[×xX*·]\s*10\^", text)
    }
    positions = [i for i in range(len(text)) if i not in removed]
    return "".join(text[i] for i in positions), positions


async def main() -> None:
    from deep_research.agents.base import direct_system_prompt
    from deep_research.agents.researcher import SYSTEM, Researcher, source_context
    from deep_research.config import Settings
    from deep_research.guardrails import report_eligible
    from deep_research.llm import LLM
    from deep_research.models import ExtractedFindingList
    from deep_research.observability import Tracer
    from deep_research.prompting import PrefixPrompt
    from deep_research.quantities import has_scientific_notation, parse_measurements
    from deep_research.workbench.attachments import parse_attachment
    from deep_research.workbench.extraction import check_extraction

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--initial-response", type=Path, required=True)
    parser.add_argument("--paper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    initial = ExtractedFindingList.model_validate_json(
        json.loads(args.initial_response.read_text(encoding="utf-8"))["output"]
    )
    raw = args.paper.read_bytes()
    attachment = await parse_attachment(raw, args.paper.name)
    if attachment.truncated:
        raise ValueError("Reparsed PDF is incomplete")
    sources = attachment.sources()
    recovered, selected, provenance = [], {}, []
    for index, original in enumerate(initial.findings, 1):
        quantity = original.quantity
        if (
            quantity is None
            or quantity.value is None
            or not has_scientific_notation(quantity.rendered)
            or parse_measurements(quantity.rendered)
        ):
            continue
        if not original.source_url.startswith(
            f"https://workspace.invalid/attachments/{attachment.id}?"
        ):
            raise ValueError("PDF bytes do not match the original candidate")
        matches = []
        for source in sources:
            flat, positions = flatten_exponents(source.content)
            start = flat.find(original.evidence_quote)
            if start >= 0 and flat.count(original.evidence_quote) == 1:
                end = start + len(original.evidence_quote)
                quote = source.content[positions[start] : positions[end - 1] + 1]
                matches.append((source, quote))
        if len(matches) != 1:
            raise ValueError(f"Candidate c{index} does not have a unique reparsed quote")
        source, quote = matches[0]
        values = {
            m.raw
            for m in parse_measurements(quote)
            if m.value == quantity.value and flatten_exponents(m.raw)[0] == quantity.rendered
        }
        if len(values) != 1:
            raise ValueError(f"Candidate c{index} value is not confirmed by PDF superscripts")
        display = values.pop()
        revision = hashlib.sha256(source.content.encode()).hexdigest()[:16]
        source = source.model_copy(update={"url": source.url + "&text_revision=" + revision})
        proposal = original.model_copy(deep=True)
        proposal.source_url, proposal.evidence_quote = source.url, quote
        proposal.quantity = quantity.model_copy(update={"rendered": display})
        proposal.statement = original.statement.replace(quantity.rendered, display)
        recovered.append(proposal)
        selected[source.url] = source
        provenance.append(
            {
                "candidate_id": f"c{index}",
                "original": original.model_dump(mode="json"),
                "reparsed": proposal.model_dump(mode="json"),
            }
        )
    if not recovered:
        raise ValueError("No numeric candidates need recovery")
    profile = remote_profile(args.authorized_ssh)

    def save(name, value):
        data = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in data:
            raise RuntimeError("Credential cannot be persisted")
        (args.output / name).write_text(data, encoding="utf-8")

    calls = []

    class Capture(LLM):
        async def _complete_once(self, system, user, temperature):
            output = await super()._complete_once(system, user, temperature)
            calls.append({"input_chars": len(system) + len(user), "output": output})
            save(f"response-{len(calls)}.json", calls[-1])
            return output

    tracer = Tracer()
    tracer.cache_scope = "scientific-recovery:" + attachment.id
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
    fixed = list(selected.values())
    save(
        "input.json",
        {
            "pdf_sha256": hashlib.sha256(raw).hexdigest(),
            "original_response": str(args.initial_response),
            "provenance": provenance,
            "sources": [s.model_dump(mode="json") for s in fixed],
        },
    )
    researcher = Researcher(
        llm=llm,
        tracer=tracer,
        settings=Settings(quality={"extraction_max_revisions": 0}),
    )
    researcher.raise_extraction_errors = True
    question = "核对恢复上标后的表格数值及方法归属，数值必须与原始候选及表格对应。"
    prompt = PrefixPrompt(source_context(fixed), question)
    try:
        print(
            f"Rechecking {len(recovered)} saved numeric candidates; no extraction call", flush=True
        )
        result = await check_extraction(
            researcher,
            ExtractedFindingList(findings=recovered),
            fixed,
            question,
            direct_system_prompt(SYSTEM),
            prompt,
        )
        save("result.json", result.model_dump(mode="json"))
        passed = all(report_eligible(f) for f in result.findings) and len(result.findings) == len(
            recovered
        )
        save(
            "acceptance.json",
            {
                "passed": passed,
                "new_tokens": tracer.total_tokens,
                "model_calls": len(calls),
                "candidates": len(recovered),
                "mode": "saved candidates, reparsed PDF, new semantic review",
            },
        )
        if not passed:
            raise ValueError("Recovered candidates failed review")
        print(
            f"Numeric recovery passed; tokens={tracer.total_tokens}; calls={len(calls)}", flush=True
        )
    finally:
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Scientific recovery stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
