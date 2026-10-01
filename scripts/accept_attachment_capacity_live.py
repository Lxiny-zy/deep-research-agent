"""Real-model first/last-chunk check for capacity-based attachment reading."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.accept_delivery_live import remote_profile


async def main() -> None:
    from deep_research.agents.base import Blackboard, RunContext
    from deep_research.config import Settings
    from deep_research.guardrails import report_eligible
    from deep_research.llm import LLM
    from deep_research.observability import Tracer
    from deep_research.workbench.attachment_reader import AttachmentReader
    from deep_research.workbench.attachments import ATTACHMENTS_SCRATCH_KEY, parse_attachment
    from deep_research.workbench.intake import _FixedSources

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-ssh", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    profile = remote_profile(args.authorized_ssh)
    args.output.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        if profile["api_key"] in payload:
            raise RuntimeError("Refusing to persist a credential")
        (args.output / name).write_text(payload, encoding="utf-8")

    tracer = Tracer()
    llm = LLM.from_params(
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
    first, last = "BEGIN-" + uuid4().hex, "END-" + uuid4().hex
    text = (
        f"合成验收文档。文首起点标记为 {first}。\n\n"
        + "\n\n".join(
            f"合成填充章节 {i}\n" + "本段仅用于检查长文件输入组装，不是科研结论。" * 140
            for i in range(16)
        )
        + f"\n\n合成验收文档的文末终点标记为 {last}。"
    )
    attachment = await parse_attachment(text.encode(), "synthetic-capacity.txt")
    save(
        "fixture.json",
        {"first": first, "last": last, "attachment": attachment.model_dump(mode="json")},
    )
    calls: dict[str, int] = {}
    original = llm.parse

    async def counted(system, user, schema, **kwargs):
        calls[schema.__name__] = calls.get(schema.__name__, 0) + 1
        save(
            f"request-{schema.__name__}-{calls[schema.__name__]}.json",
            {
                "chars": len(system) + len(user),
                "first_in_request": first in user,
                "last_in_request": last in user,
            },
        )
        result = await original(system, user, schema, **kwargs)
        save(
            f"result-{schema.__name__}-{calls[schema.__name__]}.json",
            result.model_dump(mode="json"),
        )
        return result

    llm.parse = counted
    bb = Blackboard(query="仅提取文首起点标记和文末终点标记两个事实，完整保留原文标记。")
    bb.scratch[ATTACHMENTS_SCRATCH_KEY] = [attachment.model_dump(mode="json")]
    ctx = RunContext(llm=llm, search_tool=_FixedSources([]), tracer=tracer, settings=Settings())
    try:
        print(f"Attachment capacity check: {len(attachment.chunks)} chunks", flush=True)
        await AttachmentReader().step(bb, ctx)
        findings = [f for result in bb.results for f in result.findings if report_eligible(f)]
        recovered = "\n".join(f.statement + "\n" + f.evidence_quote for f in findings)
        record = {
            "scope": "synthetic boundary-marker test; not paper interpretation quality",
            "model": profile["model"],
            "chunks": len(attachment.chunks),
            "calls": calls,
            "tokens": tracer.total_tokens,
            "first_found": first in recovered,
            "last_found": last in recovered,
            "findings": [f.model_dump(mode="json") for f in findings],
            "events": [
                {"stage": e.stage, "type": e.type, "message": e.message, "data": e.data}
                for e in tracer.events
                if e.type in {"finding", "error"}
            ],
        }
        save("acceptance.json", record)
        assert len(attachment.chunks) > 4 and calls.get("FindingList") == 1
        assert record["first_found"] and record["last_found"]
        print(
            f"Passed: one extraction includes both markers; tokens={tracer.total_tokens}",
            flush=True,
        )
    finally:
        await llm.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Attachment acceptance stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
