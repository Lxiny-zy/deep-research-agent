"""Opt-in real-model delivery acceptance; SSH credentials remain in process memory.

Run only with explicit authorization to use the named server's model configuration.
No deploy, server config write, migration or remote research task is performed.
The default suite uses frozen public paper inputs and a labelled synthetic dataset;
it measures the local engine and real model, not live search recall or human accuracy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pickle
import re
import subprocess
import sys
import time
from contextlib import aclosing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def remote_profile(alias: str) -> dict:
    # Only SELECTs; do not import the server application (startup can migrate/write).
    remote = """set -eu
cd /www/wwwroot/deep-research-agent
docker compose exec -T -e PYTHONDONTWRITEBYTECODE=1 api python - <<'PY'
import asyncio, json
from deep_research.config import Settings
from deep_research.catalog.repository import CatalogRepository
from deep_research.persistence.db import make_engine, make_sessionmaker
async def read():
    base = Settings()
    engine = make_engine(base.database_url)
    try:
        repo = CatalogRepository(make_sessionmaker(engine))
        current = await repo.config_store.load(base)
        profile = await repo.get_default_profile()
        fields = ['api_key', 'base_url', 'model', 'temperature',
                  'parameter_mode', 'reasoning_effort']
        data = {k: getattr(profile, k) for k in fields} if profile else {
            'api_key': current.llm_api_key, 'base_url': current.llm_base_url,
            'model': current.llm_model}
        data['context_window_tokens'] = getattr(profile, 'context_window_tokens', None)
        data['max_output_tokens'] = getattr(profile, 'max_output_tokens', None)
        data['quality'] = current.quality
        data['request_timeout'] = current.request_timeout
        print(json.dumps(data))
    finally:
        await engine.dispose()
asyncio.run(read())
PY
"""
    result = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", alias, "bash", "-s"],
        input=remote.encode(),
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        classes = re.findall(r"\b\w+(?:Error|Exception)\b", result.stderr.decode(errors="replace"))
        print(
            f"SSH read status={result.returncode}; exception types={sorted(set(classes))}",
            flush=True,
        )
        raise RuntimeError("Read-only model configuration retrieval failed")
    payload = json.loads(result.stdout)
    if not payload.get("api_key"):
        raise RuntimeError("No configured model credential")
    return payload


async def run_case(
    key: str,
    profile: dict,
    output: Path,
    paper: Path | None,
    *,
    papers: list[Path] | None = None,
    strategy: str | None = None,
    query_override: str | None = None,
    live_search: bool = False,
) -> dict:
    from deep_research.config import Settings
    from deep_research.llm import LLM
    from deep_research.orchestrator import DeepResearchAgent, create_initial_execution
    from deep_research.persistence.memory_repository import InMemoryRepository
    from deep_research.workbench.attachments import ATTACHMENTS_SCRATCH_KEY, parse_attachment
    from deep_research.workbench.contract import CONTRACT_SCRATCH_KEY, build_contract
    from deep_research.workbench.intake import _FixedSources
    from deep_research.workbench.templates import get_template

    target = output / key
    target.mkdir(parents=True, exist_ok=True)
    if live_search and (key not in {"autoResearch", "litReview"} or strategy == "none"):
        raise ValueError("Live retrieval requires an open research or literature review task")
    settings = Settings(
        llm_api_key=profile["api_key"],
        llm_base_url=profile["base_url"],
        llm_model=profile["model"],
        request_timeout=max(120, profile.get("request_timeout", 120)),
        artifact_root=str(target / "work"),
        max_concurrency=1,
        provider_max_concurrency=1,
        quality=profile.get("quality", {}),
    )
    template = get_template(key)
    if template is None:
        raise ValueError("Unknown acceptance template")
    query = {
        "dataAnalysis": (
            "以下为明确标注的合成验收数据。比较两种高光谱重建方法在同一组场景的 PSNR，"
            "解释离散程度和配对差异，列出单位、样本量、缺失值及局限，不外推为真实科研结果。"
        ),
        "paperRead": (
            "精读所附论文，用中文说明研究问题、作者明确提出的贡献、方法流程、数学表达、"
            "实验条件、局限和结论；准确区分既有方法与原创贡献。"
        ),
        "peerReview": (
            "评审所附论文，给出有原文依据的优点、不足、可执行修改建议及总体推荐。不要编造实验数据。"
        ),
        "litReview": (
            "基于本次提供的公开论文材料整理方法综述，比较研究思路、方法条件和局限；"
            "明确材料覆盖范围，不把单篇材料称为领域全景。"
        ),
        "autoResearch": (
            "根据提供的公开论文材料分析高光谱成像拼接的研究问题、方法选择、证据和局限，"
            "给出有依据的结论。"
        ),
        "slides": (
            "根据提供的公开论文做中文组会汇报，包含问题、贡献、方法、实验依据、局限、"
            "结论和讨论；每页保留演讲备注，避免文字堆积。"
        ),
        "mindmap": (
            "根据提供的公开论文整理中文思维导图，按研究问题、方法、实验、贡献、局限和"
            "后续问题组织，不编造事实。"
        ),
    }[key]
    if query_override:
        query = query_override
    attachments = []
    if key != "dataAnalysis":
        inputs = ([paper] if paper else []) + (papers or [])
        if not inputs and not live_search:
            raise ValueError("Paper input required")
        for input_path in inputs:
            item = await parse_attachment(
                await asyncio.to_thread(input_path.read_bytes), input_path.name
            )
            if item.truncated:
                raise ValueError("Acceptance input is truncated")
            attachments.append(item)
    dataset = "scene,method_a_psnr_db,method_b_psnr_db\n" + "\n".join(
        f"s{i},{30 + i * 0.2:.1f},{31 + i * 0.2 + (i % 3) * 0.1:.1f}" for i in range(1, 13)
    )
    contract = build_contract(
        template,
        query,
        attachments_csv=dataset if key == "dataAnalysis" else "",
        dataset_source={"filename": "synthetic-paired-acceptance.csv"}
        if key == "dataAnalysis"
        else None,
        quality=settings.quality,
        strategy=strategy,
    )
    workflow = template.workflow_for(strategy)
    execution = create_initial_execution(query, workflow, settings, requested_workflow=workflow)
    scratch = execution.checkpoint.setdefault("scratch", {})
    scratch[CONTRACT_SCRATCH_KEY] = contract.model_dump(mode="json")
    if attachments:
        scratch[ATTACHMENTS_SCRATCH_KEY] = [item.model_dump(mode="json") for item in attachments]

    class ProvidedOnly(_FixedSources):
        calls = 0

        async def search(self, query, *, max_results=5):
            self.calls += 1
            raise AssertionError("A provided-material review must not perform open retrieval")

    from deep_research.tools.base import SearchTool

    class LiveSources(SearchTool):
        """Public production backends with full retrieval records; no paid search keys."""

        def __init__(self):
            from deep_research.tools.arxiv_search import ArxivSearch
            from deep_research.tools.composite import MultiBackendSearch
            from deep_research.tools.openalex import OpenAlexSearch

            options = {
                "timeout": settings.request_timeout,
                "fulltext": settings.fulltext_enabled,
                "fulltext_max_chars": settings.fulltext_max_chars,
            }
            self.delegate = MultiBackendSearch([ArxivSearch(**options), OpenAlexSearch(**options)])
            self.calls = 0

        @property
        def backend_name(self):
            return self.delegate.backend_name

        def set_tracer(self, tracer):
            super().set_tracer(tracer)
            self.delegate.set_tracer(tracer)

        async def search(self, query, *, max_results=5):
            self.calls += 1
            number, started = self.calls, time.monotonic()
            record = {"query": query, "requested_results": max_results, "status": "interrupted"}
            try:
                sources = await self.delegate.search(query, max_results=max_results)
                record.update(
                    status="complete", sources=[s.model_dump(mode="json") for s in sources]
                )
                return sources
            except Exception as exc:
                record.update(status="error", error_type=type(exc).__name__)
                raise
            finally:
                record["seconds"] = time.monotonic() - started
                encoded = json.dumps(record, ensure_ascii=False, indent=2)
                if profile["api_key"] in encoded:
                    raise RuntimeError("Refusing to persist a credential")
                (target / f"retrieval-{number}.json").write_text(encoded, encoding="utf-8")

        async def aclose(self):
            await self.delegate.aclose()

    search = (
        LiveSources()
        if live_search
        else ProvidedOnly([])
        if strategy == "none"
        else _FixedSources([s for a in attachments for s in a.sources()])
    )
    repo = InMemoryRepository()
    run_id = await repo.create_run(query, execution=execution)
    agent = DeepResearchAgent(
        settings,
        search_tool=search,
        workflow=workflow,
        requested_workflow=workflow,
        repo=repo,
        run_id=run_id,
        initial_execution=execution,
    )

    class CaptureLLM(LLM):
        calls = 0

        async def _stream_once(self, system, user, *, temperature=0.4):
            # Retain each response, including invalid JSON and interrupted output,
            # so a failed cold run can be diagnosed without paid full re-extraction.
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
                record = json.dumps(
                    {
                        "call": number,
                        "status": status,
                        "seconds": time.monotonic() - started,
                        "input_chars": len(system) + len(user),
                        "output": "".join(chunks),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                if profile["api_key"] in record:
                    raise RuntimeError("Refusing to persist a credential")
                await asyncio.to_thread(
                    (target / f"response-{number}.json").write_text, record, encoding="utf-8"
                )

    llm = CaptureLLM.from_params(
        agent.tracer,
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
    # Use the same configured model options for every engine role in this local run.
    agent.llm = llm
    for role in (agent.planner, agent.researcher, agent.reflector, agent.synthesizer):
        role.llm = llm
    agent.researcher.verification_llm = llm

    def progress(event):
        if event.type in {"start", "done", "error"} or (
            event.stage != "LLM" and event.type in {"info", "finding"}
        ):
            print(f"{key}: {event.stage} {event.type} ({event.elapsed:.0f}s)", flush=True)

    agent.tracer.subscribe(progress)

    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print(
                f"{key}: still running; recorded model tokens={agent.tracer.total_tokens}",
                flush=True,
            )

    beat = asyncio.create_task(heartbeat())
    try:
        await agent.run(query)
        if isinstance(search, ProvidedOnly) and search.calls:
            raise AssertionError("Unexpected retrieval in provided-only workflow")
        detail = await repo.get_run(run_id)
        if detail is None:
            raise RuntimeError("Missing persisted local result")
        # Freeze before rendering: a format/storage failure must not require
        # another paid model run. Never load pickles from users or remote sources.
        frozen = pickle.dumps(detail)
        if profile["api_key"].encode() in frozen:
            raise RuntimeError("Secret detected in local result; refusing to persist")
        (target / "local-detail.pickle").write_bytes(frozen)
        metadata = {
            "model": profile["model"],
            "input": [item.filename for item in attachments]
            or ("open research question" if live_search else "labelled synthetic paired data"),
            "open_retrieval_calls": search.calls
            if isinstance(search, (ProvidedOnly, LiveSources))
            else None,
            "search": "live arXiv and OpenAlex with configured full-text expansion"
            if live_search
            else "frozen supplied paper sources; not a search recall test",
            "tokens": agent.tracer.total_tokens,
            "run_status": detail.status,
        }
        (target / "model-run.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2).replace(
                profile["api_key"], "[REDACTED]"
            ),
            encoding="utf-8",
        )
        record = render_case(detail, target, settings.artifact_total_bytes, metadata)
        safe = json.dumps(record, ensure_ascii=False, indent=2).replace(
            profile["api_key"], "[REDACTED]"
        )
        (target / "acceptance.json").write_text(safe, encoding="utf-8")
        print(
            f"{key}: status={record['status']}; tokens={agent.tracer.total_tokens}; "
            f"files={len(record['items'])}",
            flush=True,
        )
        return record
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
        await agent.aclose()
        await search.aclose()


def render_case(
    detail, target: Path, quota: int | None, metadata: dict, *, storage_root: Path | None = None
) -> dict:
    from deep_research.workbench.delivery_store import build_or_load, load_version
    from deep_research.workbench.publish import build_bundle

    target.mkdir(parents=True, exist_ok=True)
    root = str(storage_root if storage_root is not None else target / "work")
    bundle = build_or_load(detail, root, quota, build_bundle)
    restored = load_version(detail, root, bundle.content_version)
    assert [file.data for file in bundle.files] == [file.data for file in restored.files]
    for file in bundle.files:
        path = target / file.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.data)
    record = bundle.registry()
    record["acceptance"] = {**metadata, "persisted_version_verified": True}
    return record


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--authorized-ssh", help="Explicitly authorized SSH alias")
    mode.add_argument(
        "--rerender-local",
        type=Path,
        help="Trusted local-detail.pickle created by this script; no SSH or model call",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--paper", type=Path)
    parser.add_argument("--papers", type=Path, nargs="+")
    parser.add_argument("--strategy", choices=["none", "quick", "deep"])
    parser.add_argument("--query-file", type=Path)
    parser.add_argument(
        "--live-search", action="store_true", help="Use live arXiv/OpenAlex for open research tasks"
    )
    parser.add_argument("--templates", nargs="+", default=["dataAnalysis"])
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.rerender_local:
        from deep_research.persistence.repository import RunDetail

        detail = pickle.loads(args.rerender_local.read_bytes())
        if not isinstance(detail, RunDetail):
            raise ValueError("Expected this script's trusted local RunDetail")
        record = render_case(
            detail,
            args.output,
            None,
            {
                "mode": "rerender saved model output; no model call",
                "source": str(args.rerender_local),
                "run_status": detail.status,
            },
        )
        (args.output / "acceptance.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Rerender: status={record['status']}; files={len(record['items'])}", flush=True)
        return
    profile = remote_profile(args.authorized_ssh)
    print(
        f"Read-only configuration ready: model={profile['model']}; credential kept in memory",
        flush=True,
    )
    for key in args.templates:
        await run_case(
            key,
            profile,
            args.output,
            args.paper,
            papers=args.papers,
            strategy=args.strategy,
            query_override=args.query_file.read_text(encoding="utf-8") if args.query_file else None,
            live_search=args.live_search,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # A provider exception can echo a request; never dump it or local variables.
        print(f"Acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
