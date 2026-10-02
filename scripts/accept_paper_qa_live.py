"""Local real-model regression of an explicitly authorized server paper conversation."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import logging
import pickle
import subprocess
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile


def fetch_sources(alias: str, run_id: str) -> list[dict]:
    run_id = str(UUID(run_id))
    script = f"""main() {{
cd /www/wwwroot/deep-research-agent || exit 1
docker compose exec -T -e PYTHONDONTWRITEBYTECODE=1 api python - <<'PY'
import asyncio, json
from deep_research.config import Settings
from deep_research.persistence.db import make_engine, make_sessionmaker
from deep_research.persistence.sql_repository import SqlRepository
from deep_research.workbench.reader import paper_sources
async def read():
    engine = make_engine(Settings().database_url)
    try:
        detail = await SqlRepository(make_sessionmaker(engine)).get_run('{run_id}')
        print(json.dumps([s.model_dump(mode='json') for s in paper_sources(detail)]))
    finally:
        await engine.dispose()
asyncio.run(read())
PY
}}
main
"""
    result = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", alias, "bash", "-s"],
        input=script.encode(),
        capture_output=True,
        timeout=90,
    )
    if result.returncode:
        raise RuntimeError("Read-only paper snapshot retrieval failed")
    return json.loads(result.stdout)


async def main() -> None:
    from deep_research.agents.base import RunContext
    from deep_research.config import Settings
    from deep_research.llm import LLM
    from deep_research.models import Source
    from deep_research.observability import Tracer
    from deep_research.prompting import load_global_rules
    from deep_research.workbench.intake import _FixedSources
    from deep_research.workbench.qa import answer_question
    from deep_research.workbench.qa_cache import PaperEvidenceCache

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--local-detail",
        type=Path,
        help="Trusted local run pickle; reuse its frozen paper and verified findings",
    )
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--context-window-tokens", type=int)
    parser.add_argument(
        "--replay-extraction",
        type=Path,
        help="Reuse real extraction/verifier responses; call the model only for the answer",
    )
    parser.add_argument("--questions", nargs="+", default=["分析论文主要内容", "论文创新点是啥"])
    parser.add_argument(
        "--history-json", type=Path, help="Resume trusted local query/answer history"
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    args.output.mkdir(parents=True, exist_ok=True)
    profile = remote_profile(args.authorized_ssh)
    if args.max_output_tokens is not None:
        profile["max_output_tokens"] = args.max_output_tokens
    if args.context_window_tokens is not None:
        profile["context_window_tokens"] = args.context_window_tokens
    cache_file = args.output / "paper-sources.json"
    evidence = []
    if args.local_detail:
        from deep_research.persistence.repository import RunDetail
        from deep_research.workbench.reader import paper_sources

        detail = pickle.loads(args.local_detail.read_bytes())
        if not isinstance(detail, RunDetail) or detail.id != args.run:
            raise ValueError("Expected a trusted local snapshot for the requested run")
        raw_sources = [source.model_dump(mode="json") for source in paper_sources(detail)]
        evidence = [finding for result in detail.results for finding in result.findings]
    else:
        raw_sources = (
            json.loads(cache_file.read_text(encoding="utf-8"))
            if cache_file.exists()
            else fetch_sources(args.authorized_ssh, args.run)
        )
    cache_file.write_text(json.dumps(raw_sources, ensure_ascii=False, indent=2), encoding="utf-8")
    sources = [Source.model_validate(item) for item in raw_sources]
    print(
        f"Paper snapshot: {len(sources)} chunks, "
        f"{sum(len(s.content) for s in sources)} characters; "
        f"model={profile['model']}",
        flush=True,
    )

    def save(name: str, data) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        if profile["api_key"] in text:
            raise RuntimeError("Refusing to persist credential")
        (args.output / name).write_text(text, encoding="utf-8")

    schemas: dict[str, int] = {}
    selection_prefixes = []

    class CaptureLLM(LLM):
        calls = 0
        streams = 0

        async def parse(self, system, user, schema, **kwargs):
            schemas[schema.__name__] = schemas.get(schema.__name__, 0) + 1
            if schema.__name__ == "PaperEvidenceSelection":
                selection_prefixes.append(
                    hashlib.sha256(user.split("\n\n", 1)[0].encode()).hexdigest()
                )
                print("Checking reusable evidence coverage", flush=True)
            if args.replay_extraction and schema.__name__ in {
                "ExtractedFindingList",
                "SemanticEvidenceDecisionList",
            }:
                from deep_research.llm import extract_json

                index = 1 if schema.__name__ == "ExtractedFindingList" else 2
                record = json.loads(
                    (args.replay_extraction / f"call-{index}.json").read_text(encoding="utf-8")
                )
                return schema.model_validate(extract_json(record["output"]))
            value = await super().parse(system, user, schema, **kwargs)
            if schema.__name__ == "PaperEvidenceSelection":
                save(f"selection-{len(selection_prefixes)}.json", value.model_dump(mode="json"))
            return value

        async def stream(self, system, user, **kwargs):
            self.streams += 1
            number = self.streams
            parts = []
            async for part in super().stream(system, user, **kwargs):
                parts.append(part)
                yield part
            save(f"stream-{number}.json", {"output": "".join(parts)})

        async def _complete_once(self, system, user, temperature):
            self.calls += 1
            number = self.calls
            result = await super()._complete_once(system, user, temperature)
            save(f"call-{number}.json", {"input_chars": len(system) + len(user), "output": result})
            return result

    settings = Settings(
        llm_api_key=profile["api_key"],
        llm_base_url=profile["base_url"],
        llm_model=profile["model"],
        quality=profile.get("quality", {}),
        request_timeout=180,
    )
    tracer = Tracer()
    tracer.cache_scope = f"local-qa-acceptance:{args.run}"
    llm = CaptureLLM.from_params(
        tracer,
        api_key=profile["api_key"],
        base_url=profile["base_url"],
        model=profile["model"],
        timeout=180,
        user_agent=settings.llm_user_agent,
        temperature=profile.get("temperature", 0.3),
        parameter_mode=profile.get("parameter_mode", "temperature"),
        reasoning_effort=profile.get("reasoning_effort", "medium"),
        context_window_tokens=profile.get("context_window_tokens"),
        max_output_tokens=profile.get("max_output_tokens"),
    )

    def progress(event):
        if (
            event.type in {"start", "error", "finding"}
            or (event.data or {}).get("category") == "llm_output_retry"
        ):
            message = event.message.replace(profile["api_key"], "[REDACTED]")
            print(f"{event.elapsed:.0f}s {event.stage}: {message[:350]}", flush=True)

    tracer.subscribe(progress)
    ctx = RunContext(
        llm=llm,
        tracer=tracer,
        settings=settings,
        search_tool=_FixedSources([]),
        global_rules=load_global_rules(),
    )
    history = json.loads(args.history_json.read_text(encoding="utf-8")) if args.history_json else []
    if not isinstance(history, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("query"), str)
        or not isinstance(item.get("answer"), str)
        for item in history
    ):
        raise ValueError("Expected a list of query/answer history records")
    cache = PaperEvidenceCache()
    results = []
    try:
        for index, question in enumerate(args.questions, 1):
            tokens_before = tracer.total_tokens
            schemas_before = dict(schemas)
            result = await answer_question(
                question,
                history=history,
                ctx=ctx,
                paper_sources=sources,
                paper_evidence=evidence,
                paper_cache=cache,
                cache_scope=args.run,
            )
            record = dataclasses.asdict(result)
            record["findings"] = [finding.model_dump(mode="json") for finding in result.findings]
            record["validation_scope"] = (
                "replayed real extraction/verifier; fresh answer"
                if args.replay_extraction
                else "fresh selection, answer and support review over frozen verified evidence"
                if args.local_detail
                else "fresh model pipeline"
            )
            save(f"answer-{index}.json", record)
            results.append(
                {
                    "question": question,
                    "fallback": result.fallback,
                    "tokens": tracer.total_tokens - tokens_before,
                    "schemas": {
                        name: count - schemas_before.get(name, 0) for name, count in schemas.items()
                    },
                    "findings": len(result.findings),
                }
            )
            history.append({"query": question, "answer": result.answer})
            print(
                f"Answer {index}: fallback={result.fallback}; evidence={len(result.findings)}; "
                f"answer_chars={len(result.answer)}; cumulative_tokens={tracer.total_tokens}",
                flush=True,
            )
    finally:
        safe_events = [
            event.model_dump(mode="json")
            for event in tracer.events
            if not (event.data or {}).get("reasoning_delta")
        ]
        save("events.json", safe_events)
        save(
            "acceptance.json",
            {
                "model": profile["model"],
                "questions": results,
                "tokens": tracer.total_tokens,
                "selection_prefixes": selection_prefixes,
                "reused_input": str(args.local_detail) if args.local_detail else None,
            },
        )
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Local QA regression stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
