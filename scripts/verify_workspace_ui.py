"""Real HTTP/browser regression with disposable data and no provider requests."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STYLE_SAMPLE = """() => [...document.body.querySelectorAll('*')]
  .filter(el => el.getBoundingClientRect().width && el.getBoundingClientRect().height)
  .map(el => {
    const css = getComputedStyle(el)
    return [el.tagName, el.className?.baseVal ?? el.className, ...[
      'display', 'position', 'width', 'height', 'color', 'backgroundColor',
      'fontSize', 'lineHeight', 'padding', 'margin', 'border', 'boxShadow',
      'gap', 'gridTemplateColumns', 'opacity', 'overflow'
    ].map(key => css[key])]
  })"""


async def verify(label: str, compare: str | None, extended: bool = False) -> None:
    import uvicorn
    from playwright.async_api import async_playwright, expect

    for name in (
        "API_KEY",
        "DR_API_KEYS",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "TAVILY_API_KEY",
        "BRAVE_API_KEY",
        "SERPER_API_KEY",
        "XAI_API_KEY",
        "CATALOG_ENCRYPTION_KEY",
        "DR_DEMO_FAKE_BACKENDS",
    ):
        os.environ[name] = ""
    os.environ.update(APP_ENV="test", DR_SEARCH_BACKENDS="openalex")

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    output = ROOT / "artifacts" / "ui-workspace" / label
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dra-workspace-ui-") as directory:
        root = Path(directory)
        os.environ.update(
            RUNTIME_CONFIG_PATH=str(root / "config.json"),
            DR_ARTIFACT_ROOT=str(root / "artifacts"),
            DATABASE_URL=f"sqlite+aiosqlite:///{root / 'ui.sqlite'}",
        )
        from deep_research.api import app
        from deep_research.catalog.dto import SearchProfileInput
        from deep_research.catalog.repository import CatalogRepository
        from deep_research.config import Settings
        from deep_research.models import Report
        from deep_research.persistence.db import create_all, make_engine, make_sessionmaker
        from deep_research.persistence.sql_repository import SqlRepository

        engine = make_engine(os.environ["DATABASE_URL"])
        await create_all(engine)
        sessions = make_sessionmaker(engine)
        repo, catalog = SqlRepository(sessions), CatalogRepository(sessions)
        app.state.settings = Settings(execution_mode="worker", intent_enabled=False)
        app.state.repo, app.state.catalog, app.state.live = repo, catalog, {}
        app.state.tasks, app.state.run_tasks = set(), {}
        pending_title = "进行中的测试研究"
        await repo.create_run(pending_title)
        run_id = await repo.create_run("报告页面测试")
        await repo.save_report(
            run_id, Report(query="测试", markdown="# 测试报告\n\n合成验收内容。")
        )
        await repo.finalize(run_id, elapsed=1.0, total_tokens=120)
        profile = await catalog.save_search_profile(
            SearchProfileInput(name="临时检索档案", provider="openalex")
        )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        ready = asyncio.Event()
        server = ReadyServer(uvicorn.Config(app, lifespan="off", log_level="error"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        observations: dict = {"layouts": [], "page_errors": [], "pool_errors": [], "styles": {}}

        class PoolErrors(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                observations["pool_errors"].append(record.getMessage())

        pool_logger = logging.getLogger("sqlalchemy.pool")
        pool_errors = PoolErrors(level=logging.ERROR)
        pool_logger.addHandler(pool_errors)
        try:
            async with asyncio.timeout(600 if extended else 90):
                await ready.wait()
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=True)
                    page = await browser.new_page(reduced_motion="reduce")
                    page.set_default_timeout(10_000)
                    await page.add_init_script("localStorage.setItem('dr_welcome_tour_seen', '1')")
                    page.on(
                        "pageerror", lambda error: observations["page_errors"].append(str(error))
                    )
                    page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))

                    async def local_only(route):
                        if route.request.url.startswith(origin):
                            await route.continue_()
                        else:
                            await route.abort()

                    await page.route("**/*", local_only)
                    for width, height in ((1440, 1000), (390, 844)):
                        await page.set_viewport_size({"width": width, "height": height})
                        for name, route in (
                            ("new", "/"),
                            ("history", "/history"),
                            ("agents", "/agents?tab=keys"),
                            ("settings", "/settings"),
                            ("workflows", "/workflows"),
                            ("report", f"/runs/{run_id}"),
                        ):
                            await page.goto(origin + route, wait_until="networkidle")
                            await page.locator(".app-container").wait_for()
                            await page.evaluate("document.fonts.ready")
                            shape = await page.evaluate("""() => ({
                                viewport: innerWidth,
                                scrollWidth: document.documentElement.scrollWidth
                            })""")
                            assert shape["scrollWidth"] <= width, (name, shape)
                            key = f"{width}-{name}"
                            observations["layouts"].append({"page": key, **shape})
                            observations["styles"][key] = await page.evaluate(STYLE_SAMPLE)
                            await page.screenshot(path=str(output / f"{key}.png"), full_page=True)

                    await page.set_viewport_size({"width": 1440, "height": 1000})
                    await page.goto(origin + "/agents?tab=keys")
                    tab = page.get_by_role("tab", selected=True)
                    await tab.focus()
                    await page.keyboard.press("Home")
                    await expect(page.get_by_role("tab", selected=True)).to_be_focused()
                    await expect(page.get_by_role("tabpanel")).to_have_attribute(
                        "aria-labelledby",
                        await page.get_by_role("tab", selected=True).get_attribute("id"),
                    )
                    await page.keyboard.press("End")
                    card = page.locator("article").filter(has_text="临时检索档案")
                    async with page.expect_response(
                        lambda r: r.request.method == "DELETE" and "/search-profiles/" in r.url
                    ) as deleting:
                        await card.get_by_role("button", name="删除", exact=True).click()
                    assert (await deleting.value).status == 204
                    await expect(card).to_have_count(0)
                    assert not any(
                        item.id == profile.id for item in await catalog.list_search_profiles()
                    )
                    await page.goto(origin + "/history")
                    await expect(
                        page.get_by_role("button", name=f"删除研究：{pending_title}")
                    ).to_be_disabled()
                    await page.goto(origin + "/workflows")
                    await page.get_by_role("button", name="新建工作流", exact=True).click()
                    dialog = page.get_by_role("dialog")
                    await expect(dialog).to_be_visible()
                    await page.keyboard.press("Shift+Tab")
                    assert await dialog.evaluate("el => el.contains(document.activeElement)")
                    await page.keyboard.press("Escape")
                    await expect(dialog).to_have_count(0)
                    await page.goto(origin + "/missing-page")
                    await expect(page.get_by_role("heading", name="页面不存在")).to_be_visible()
                    for asset, mime in (
                        ("deep-research-icon.svg", "image/svg+xml"),
                        ("research-field.png", "image/png"),
                    ):
                        response = await page.request.get(f"{origin}/{asset}")
                        assert response.ok and response.headers["content-type"].startswith(mime)
                    await page.goto(origin + "/welcome", wait_until="networkidle")
                    await expect(
                        page.get_by_role("button", name="背景动画已按系统设置暂停")
                    ).to_be_disabled()
                    assert not await page.locator(".entry-scene canvas").count()
                    if extended:
                        from verify_frontend_experience import inspect_experience

                        observations["experience"] = await inspect_experience(
                            page, origin, output, run_id, repo
                        )
                    assert not observations["page_errors"], observations["page_errors"]
                    await browser.close()
        finally:
            server.should_exit = True
            await serving
            sock.close()
            await engine.dispose()
            pool_logger.removeHandler(pool_errors)
    (output / "results.json").write_text(
        json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert not observations["pool_errors"], observations["pool_errors"]
    if compare:
        baseline = json.loads(
            (output.parent / compare / "results.json").read_text(encoding="utf-8")
        )
        changed = [
            key for key, value in observations["styles"].items() if baseline["styles"][key] != value
        ]
        assert not changed, f"Computed styles changed: {changed}"
    print(
        "Workspace browser checks passed: 12 layouts; HTTP 204, keyboard, modal, assets, 404. "
        f"{output}"
    )
    if extended:
        experience = observations["experience"]
        print(
            f"Extended checks passed: {len(experience['layouts'])} page states; "
            f"{len(experience['accessibility'])} axe scans; "
            "responsive layouts, dialogs, error recovery and motion preferences."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="current")
    parser.add_argument("--compare")
    parser.add_argument("--extended", action="store_true")
    args = parser.parse_args()
    asyncio.run(verify(args.label, args.compare, args.extended))
