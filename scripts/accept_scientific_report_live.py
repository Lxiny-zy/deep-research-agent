"""Rebind numeric rows to recovered PDF evidence and review the saved report, without rewriting."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile, render_case


async def main() -> None:
    from deep_research.agents.researcher import Researcher
    from deep_research.config import Settings
    from deep_research.guardrails import report_eligible
    from deep_research.llm import LLM
    from deep_research.models import ResearchResult
    from deep_research.observability import Tracer
    from deep_research.quantities import parse_measurements
    from deep_research.report.validation import validate_body
    from deep_research.workbench.figure_review import FIGURE_REVIEW_KEY, FIGURE_RULES, review_figure
    from deep_research.workbench.figures import ConceptFigure
    from deep_research.workbench.intake import PAPER_SOURCES_KEY
    from deep_research.workbench.paper_abstract import abstract_section_support
    from deep_research.workbench.paper_evidence import current_findings
    from deep_research.workbench.prose_review import PROSE_REVIEW_KEY, reviewer_for_report
    from deep_research.workbench.reader import paper_sources
    from deep_research.workbench.scholarly import uncited_sections_for
    from deep_research.workbench.support import SupportReviewer, evidence_records

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--detail", required=True, type=Path, help="Trusted local task pickle")
    parser.add_argument(
        "--recovery", required=True, type=Path, help="Scientific recovery directory"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--resume-prose", type=Path, help="Bound failed review; repair only rejected paragraphs"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    logging.disable(logging.CRITICAL)
    detail = pickle.loads(args.detail.read_bytes())
    recovered = ResearchResult.model_validate_json(
        (args.recovery / "result.json").read_text("utf-8")
    )
    provenance = json.loads((args.recovery / "input.json").read_text("utf-8"))["provenance"]
    assert recovered.extraction_audit and all(report_eligible(f) for f in recovered.findings)
    scratch = detail.orchestration.checkpoint["scratch"]
    sources = paper_sources(detail)
    researcher = Researcher(settings=Settings())
    rejected = []
    for result in detail.results:
        current = await current_findings(result.findings, sources, researcher)
        kept = {f.model_dump_json() for f in current}
        rejected.extend(
            f.model_dump(mode="json") for f in result.findings if f.model_dump_json() not in kept
        )
        result.findings = current
    detail.results.append(recovered)
    scratch[PAPER_SOURCES_KEY] = [
        *scratch.get(PAPER_SOURCES_KEY, []),
        *[s.model_dump(mode="json") for s in recovered.extraction_audit.sources],
    ]
    citations = list(detail.report.citations)
    lines = detail.report.markdown.splitlines()
    changed = []
    for entry in provenance:
        old, new = entry["original"], entry["reparsed"]
        assert any(s.url == old["source_url"] for s in sources), "Recovery is for another document"
        if new["source_url"] not in citations:
            citations.append(new["source_url"])
        old_cite = f"[{citations.index(old['source_url']) + 1}]"
        new_cite = f"[{citations.index(new['source_url']) + 1}]"
        found = False
        for index, line in enumerate(lines):
            if not line.lstrip().startswith("|"):
                continue
            if any(m.value == new["quantity"]["value"] for m in parse_measurements(line)):
                assert old_cite in line or new_cite in line
                updated = line.replace(old_cite, new_cite)
                if updated != line:
                    changed.append({"line": index + 1, "before": line, "after": updated})
                lines[index], found = updated, True
        assert found, "Expected a saved table row; no automatic scientific-text rewrite"
    for source in recovered.extraction_audit.sources:
        lines.append(f"[{citations.index(source.url) + 1}] {source.title}")
    detail.report = detail.report.model_copy(
        update={"citations": citations, "markdown": "\n".join(lines)}
    )
    mechanical = validate_body(
        detail.report.markdown,
        detail.results,
        {u: i for i, u in enumerate(citations, 1)},
        fallback=False,
        uncited_sections=uncited_sections_for(scratch),
        section_support=abstract_section_support(scratch),
    )
    assert not mechanical.issues, mechanical.issues
    profile = remote_profile(args.authorized_ssh)

    def save(name, value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in text:
            raise RuntimeError("Credential cannot be persisted")
        (args.output / name).write_text(text, encoding="utf-8")

    save("revalidation.json", {"excluded_findings": rejected, "citation_edits": changed})
    calls = []

    class Capture(LLM):
        async def _complete_once(self, system, user, temperature):
            response = await super()._complete_once(system, user, temperature)
            calls.append({"input_chars": len(system) + len(user), "output": response})
            save(f"response-{len(calls)}.json", calls[-1])
            return response

    tracer = Tracer()
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
    tracer.cache_scope = "scientific-report-recheck:" + detail.id

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(f"Still checking report; tokens={tracer.total_tokens}", flush=True)

    beat = asyncio.create_task(heartbeat())
    try:
        print(f"Rechecking saved prose; {len(rejected)} old findings excluded", flush=True)
        reviewer = reviewer_for_report(
            llm, detail.query, detail.results, citations, scratch, llm.input_capacity_chars
        )
        assert reviewer is not None
        if args.resume_prose:
            from deep_research.workbench.prose_edit import repair_paragraphs

            prior = json.loads(args.resume_prose.read_text(encoding="utf-8"))
            previous_edits = args.resume_prose.parent / "paragraph-repair.json"
            if previous_edits.is_file():
                prior_body = json.loads(previous_edits.read_text(encoding="utf-8"))["after"]
                detail.report = detail.report.model_copy(update={"markdown": prior_body})
            if not reviewer.prime(detail.report.markdown, prior):
                raise ValueError("Prior review does not bind to this report and evidence")
            patched = await repair_paragraphs(llm, reviewer, detail.report.markdown, prior)
            if patched is None:
                raise ValueError("This failure cannot be repaired with local paragraph edits")
            check = validate_body(
                patched,
                detail.results,
                {u: i for i, u in enumerate(citations, 1)},
                fallback=False,
                uncited_sections=uncited_sections_for(scratch),
                section_support=abstract_section_support(scratch),
            )
            if check.issues:
                raise ValueError("Paragraph edits failed mechanical validation")
            save("paragraph-repair.json", {"before": detail.report.markdown, "after": patched})
            detail.report = detail.report.model_copy(update={"markdown": patched})
        audit = await reviewer.review(detail.report.markdown)
        save("prose-review.json", audit)
        if audit["status"] != "pass":
            raise ValueError("Saved report did not pass current evidence review")
        audit.update(mechanically_finalized=True, body_replaced=False)
        scratch[PROSE_REVIEW_KEY] = audit
        extras = scratch["workbench"]["extras"]
        extras[PROSE_REVIEW_KEY] = audit
        if extras.get("concept_figure"):
            evidence = evidence_records(detail.results, {u: i for i, u in enumerate(citations, 1)})
            figure_review = await review_figure(
                ConceptFigure.model_validate(extras["concept_figure"]),
                SupportReviewer(
                    llm,
                    evidence,
                    llm.input_capacity_chars,
                    context=detail.query,
                    system_rules=FIGURE_RULES,
                ),
            )
            save("figure-review.json", figure_review)
            extras[FIGURE_REVIEW_KEY] = figure_review
        frozen = pickle.dumps(detail)
        if profile["api_key"].encode() in frozen:
            raise RuntimeError("Credential cannot be persisted")
        (args.output / "local-detail.pickle").write_bytes(frozen)
        registry = render_case(
            detail,
            args.output / "delivery",
            None,
            {
                "mode": "local paragraph repair and review of saved report; no extraction"
                if args.resume_prose
                else "saved report with corrected numeric evidence; no extraction or writing",
                "new_tokens": tracer.total_tokens,
                "model_calls": len(calls),
            },
        )
        save("acceptance.json", registry)
        print(
            f"Report recheck: {registry['status']}; tokens={tracer.total_tokens}; "
            f"files={len(registry['items'])}",
            flush=True,
        )
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        save("usage.json", {"tokens": tracer.total_tokens, "completed_calls": len(calls)})
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Scientific report recheck stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
