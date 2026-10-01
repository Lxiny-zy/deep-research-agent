"""Real browser regression for evidence drawers on completed paper-reading tasks."""

from __future__ import annotations

import argparse
import asyncio
import json
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect
from verify_welcome_login import preview

URLS = ["https://example.org/source-one", "https://example.org/source-two"]
FINDINGS = [
    {
        "statement": f"结论 {index}",
        "source_url": url,
        "evidence_quote": f"原文证据片段 {index}",
        "confidence": 0.9,
        "verification": {
            "status": "verified",
            "method": "normalized_quote",
            "reason": "",
            "source_title": f"参考材料 {index}",
            "source_content_hash": "a" * 64,
            "semantic_status": "supported",
            "semantic_confidence": 0.9,
            "semantic_reason": "",
            "claim_id": f"claim-{index}",
            "consistency_status": "clear",
            "contradicts_claim_ids": [],
            "contradiction_reason": "",
            "corroboration_status": "single_source",
            "independent_source_count": 1,
            "corroborates_claim_ids": [],
            "corroboration_reason": "",
        },
    }
    for index, url in enumerate(URLS, 1)
]


async def verify(origin: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for width, height in ((1440, 900), (390, 844)):
                for theme in ("light", "dark"):
                    context = await browser.new_context(viewport={"width": width, "height": height})
                    await context.add_init_script("""
                        sessionStorage.setItem('sr_intro_seen', '1');
                        localStorage.setItem('dr_welcome_tour_seen', '1');
                    """)

                    async def mock_api(route):  # type: ignore[no-untyped-def]
                        path = urlsplit(route.request.url).path
                        if path == "/api/config":
                            payload = {"access": {"role": "researcher"}}
                        elif path == "/api/runs/evidence-smoke/reader":
                            payload = {
                                "run_id": "evidence-smoke",
                                "status": "done",
                                "documents": [],
                                "has_report": True,
                                "can_ask": True,
                            }
                        elif path == "/api/runs/evidence-smoke":
                            payload = {
                                "id": "evidence-smoke",
                                "query": "精读证据展示验收",
                                "status": "done",
                                "results": [{"sub_question": "结论依据", "findings": FINDINGS}],
                                "report": {
                                    "markdown": "# 精读结论\n\n第一项结论 [1]。第二项结论 [2]。",
                                    "citations": URLS,
                                },
                                "events": [],
                                "created_at": None,
                                "total_tokens": 123456,
                            }
                        else:
                            payload = []
                        await route.fulfill(json=payload)

                    await context.route("**/api/**", mock_api)
                    page = await context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda error, target=errors: target.append(str(error)))
                    page.set_default_timeout(5000)
                    await page.goto(origin.rstrip("/") + "/runs/evidence-smoke/read")
                    await page.evaluate(
                        "theme => document.documentElement.dataset.theme = theme", theme
                    )
                    await page.get_by_role("tab", name="精读报告", exact=True).click()
                    citation = page.get_by_role("button", name="查看引用 1 的证据").first
                    await citation.click()
                    dialog = page.get_by_role("dialog")
                    await expect(dialog.get_by_text("原文证据片段 1", exact=True)).to_be_visible()
                    # Hit testing detects an opaque/blurred backdrop covering the panel.
                    await dialog.get_by_role("button", name="下一个来源").click()
                    await expect(dialog.get_by_text("原文证据片段 2", exact=True)).to_be_visible()
                    await dialog.get_by_role("button", name="关闭证据侧栏").click()
                    await expect(dialog).to_have_count(0)
                    await citation.click()
                    await page.keyboard.press("Escape")
                    await expect(dialog).to_have_count(0)
                    assert not errors, errors
                    print(json.dumps({"width": width, "theme": theme, "evidence": "passed"}))
                    await context.close()
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin")
    args = parser.parse_args()
    if args.origin:
        asyncio.run(verify(args.origin))
    else:
        with preview() as origin:
            asyncio.run(verify(origin))
