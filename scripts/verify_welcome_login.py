"""Exercise the real welcome/login UI with synthetic auth and no provider calls.

Run against a Vite preview or deployed frontend with --origin. API responses
are intercepted in the isolated browser, so no real key or user data is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect


@contextmanager
def preview() -> Iterator[str]:
    """Serve the built SPA locally without starting a database or application API."""
    directory = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if not (directory / "index.html").is_file():
        raise RuntimeError("Build the frontend before running the browser regression")

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            if not Path(urlsplit(self.path).path).suffix:
                self.path = "/index.html"
            super().do_GET()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def verify(origin: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for width, height in ((1440, 900), (390, 844)):
                for motion in ("no-preference", "reduce"):
                    context = await browser.new_context(
                        viewport={"width": width, "height": height}, reduced_motion=motion
                    )
                    await context.add_init_script(
                        "localStorage.setItem('dr_welcome_tour_seen', '1')"
                    )

                    async def mock_api(route):  # type: ignore[no-untyped-def]
                        path = urlsplit(route.request.url).path
                        if path == "/api/config":
                            valid = (
                                route.request.headers.get("authorization")
                                == "Bearer welcome-smoke-valid"
                            )
                            await route.fulfill(
                                status=200 if valid else 401,
                                json={"access": {"role": "researcher"}} if valid else {},
                            )
                        else:
                            await route.fulfill(json=[])

                    await context.route("**/api/**", mock_api)
                    page = await context.new_page()
                    page.set_default_timeout(5000)
                    errors: list[str] = []
                    page.on("pageerror", lambda error, target=errors: target.append(str(error)))
                    await page.goto(origin.rstrip("/") + "/history")
                    enter = page.get_by_role("button", name="进入 Enter", exact=True)
                    await enter.click()
                    dialog = page.get_by_role("dialog", name="连接你的研究工作台")
                    key = dialog.get_by_label("访问密钥", exact=True)
                    # Visibility alone misses a higher opaque welcome overlay.
                    # A real click must reach the input without force=True.
                    await key.click()
                    await expect(key).to_be_focused()
                    await expect(dialog.get_by_role("button", name="验证并进入")).to_be_disabled()
                    await page.keyboard.press("Escape")
                    await expect(dialog).to_have_count(0)
                    await enter.click()
                    await key.click()
                    await key.fill("welcome-smoke-invalid")
                    await dialog.get_by_role("button", name="验证并进入").click()
                    await expect(dialog.get_by_role("alert")).to_contain_text("访问密钥无效")
                    await key.fill("welcome-smoke-valid")
                    await dialog.get_by_role("button", name="验证并进入").click()
                    await expect(page.locator("#workspace-main")).to_be_visible()
                    await expect(dialog).to_have_count(0)
                    await expect(page.get_by_test_id("welcome")).to_have_count(0)
                    assert not errors, errors
                    print(json.dumps({"width": width, "motion": motion, "login": "passed"}))
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
