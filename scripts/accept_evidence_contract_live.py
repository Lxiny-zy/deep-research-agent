"""Opt-in real-model calibration of complete quotes and structured evidence fields."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile


async def main() -> None:
    from deep_research.guardrails import EvidenceVerifier, SemanticEvidenceVerifier, report_eligible
    from deep_research.llm import LLM
    from deep_research.models import Finding, Source
    from deep_research.observability import Tracer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    profile = remote_profile(args.authorized_ssh)
    tracer = Tracer()
    args.output.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in payload:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / name).write_text(payload, encoding="utf-8")

    class Capture(LLM):
        calls = 0

        async def _complete_once(self, system, user, temperature):
            answer = await super()._complete_once(system, user, temperature)
            self.calls += 1
            save(f"response-{self.calls}.json", {"answer": answer})
            return answer

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
    quote = (
        "Labelled synthetic evidence for software testing, not actual research. "
        "Alpha achieves PSNR 38.4 dB on the CAVE held-out split, evaluated on CPU. "
        "No other dataset or hardware is reported."
    )
    source = Source(url="https://example.org/synthetic-evidence", content=quote)
    good = Finding(
        statement="In this synthetic test Alpha achieves PSNR 38.4 dB on CAVE.",
        source_url=source.url,
        evidence_quote=quote,
        entity="Alpha",
        quantity={"metric": "PSNR", "value": 38.4, "unit": "dB", "rendered": "38.4"},
        conditions={"dataset": "CAVE", "hardware": "CPU", "split": "held-out"},
    )
    variants = [("supported-fields", good, True)]
    for name, changes in (
        ("invented-dataset", {"conditions": {"dataset": "KAIST"}}),
        ("invented-hardware", {"conditions": {"hardware": "RTX 5090"}}),
        ("wrong-method", {"entity": "Beta"}),
        ("wrong-unit", {"quantity": {"metric": "PSNR", "value": 38.4, "unit": "ms"}}),
    ):
        variants.append((name, Finding.model_validate({**good.model_dump(), **changes}), False))
    long_quote = (
        "Synthetic table A. All listed methods use the CAVE held-out split.\n"
        "Method | PSNR in dB\n"
        + "\n".join(f"Baseline-{i:02} | {27 + i / 10:.1f}" for i in range(85))
        + "\nAlpha | 38.4\nEnd of synthetic table A."
    )
    long_source = Source(url="https://example.org/long-synthetic-table", content=long_quote)
    variants.append(
        (
            "complete-table-context",
            Finding(
                statement="The synthetic table reports Alpha on the CAVE held-out split.",
                entity="Alpha",
                conditions={"dataset": "CAVE", "split": "held-out"},
                source_url=long_source.url,
                evidence_quote=long_quote,
            ),
            True,
        )
    )
    sources = {s.url: s for s in (source, long_source)}
    findings = []
    for _, finding, _ in variants:
        check = EvidenceVerifier().verify(finding, sources[finding.source_url])
        assert check.accepted and check.finding
        findings.append(check.finding)
    prompt = SemanticEvidenceVerifier._prompt(list(enumerate(findings)))
    save(
        "input.json", {"findings": [f.model_dump(mode="json") for f in findings], "prompt": prompt}
    )
    try:
        print("Structured-field and long-evidence calibration started", flush=True)
        reviewed = await SemanticEvidenceVerifier().verify_batch(findings, llm, raise_errors=True)
        cases = [
            {
                "name": name,
                "expected_admission": expected,
                "admitted": report_eligible(finding),
                "finding": finding.model_dump(mode="json"),
                "pass": (finding.verification.semantic_status == "supported") == expected
                and report_eligible(finding) == expected,
            }
            for (name, _, expected), finding in zip(variants, reviewed, strict=True)
        ]
        passed = all(case["pass"] for case in cases)
        save(
            "acceptance.json",
            {
                "model": profile["model"],
                "scope": "labelled synthetic calibration; not a complete paper run",
                "status": "pass" if passed else "fail",
                "tokens": tracer.total_tokens,
                "cases": cases,
            },
        )
        print(
            f"Calibration: {sum(c['pass'] for c in cases)}/{len(cases)}; "
            f"{tracer.total_tokens} tokens"
        )
        if not passed:
            raise RuntimeError("Calibration did not pass; original decisions retained")
    finally:
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Calibration stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
