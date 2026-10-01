"""Render a real PDF through the deployed PDF.js viewer using isolated API fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf
from playwright.async_api import async_playwright, expect
from verify_welcome_login import preview


async def verify(origin: str, pdf_file: Path | None = None) -> None:
    with pymupdf.open() as pdf:
        for number in (1, 2):
            pdf.new_page().insert_text((72, 72), f"Paper reader verification - page {number}")
        data = pdf.tobytes()
    if pdf_file is not None:
        data = await asyncio.to_thread(pdf_file.read_bytes)
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        page_count = len(pdf)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.add_init_script(
                "sessionStorage.setItem('sr_intro_seen', '1');"
                "localStorage.setItem('dr_welcome_tour_seen', '1');"
            )
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: errors.append(message.text) if message.type == "error" else None,
            )

            async def api(route):
                path = urlsplit(route.request.url).path
                if path == "/api/config":
                    await route.fulfill(json={"access": {"role": "researcher"}})
                elif path.endswith("/reader/doc/pdf"):
                    await route.fulfill(body=data, content_type="application/pdf")
                elif path.endswith("/reader"):
                    await route.fulfill(
                        json={
                            "run_id": "pdf-smoke",
                            "status": "done",
                            "documents": [
                                {
                                    "id": "doc",
                                    "kind": "attachment",
                                    "title": "example.pdf",
                                    "pdf": True,
                                    "note": "",
                                }
                            ],
                            "has_report": False,
                            "can_ask": True,
                        }
                    )
                elif path == "/api/runs/pdf-smoke":
                    await route.fulfill(
                        json={
                            "id": "pdf-smoke",
                            "status": "done",
                            "query": "PDF verification",
                            "results": [],
                            "report": None,
                        }
                    )
                else:
                    await route.fulfill(json=[])

            await page.route("**/api/**", api)
            await page.goto(origin.rstrip("/") + "/runs/pdf-smoke/read")
            try:
                await expect(page.get_by_text(f"共 {page_count} 页", exact=True)).to_be_visible(
                    timeout=20000
                )
                await page.wait_for_function("""() =>
                [...document.querySelectorAll('.pdf-page canvas')].some(canvas => {
                  if (!canvas.width || !canvas.height) return false;
                  const pixels = canvas.getContext('2d')
                    .getImageData(0, 0, canvas.width, canvas.height).data;
                  for (let i = 0; i < pixels.length; i += 16) {
                    if (pixels[i + 3] > 0 && pixels[i] < 200 &&
                        pixels[i + 1] < 200 && pixels[i + 2] < 200) return true;
                  }
                  return false;
                })""")
                assert not errors, errors
            finally:
                print(
                    json.dumps(
                        {
                            "errors": errors,
                            "status": await page.locator(".pdf-viewer").inner_text(),
                        },
                        ensure_ascii=False,
                    )
                )
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin")
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args()
    if args.origin:
        asyncio.run(verify(args.origin, args.pdf))
    else:
        with preview() as origin:
            asyncio.run(verify(origin, args.pdf))
