"""Optional browser smoke test, using a temporary DB and synthetic credentials.

Requires Playwright and its Chromium browser. Writes screenshots under artifacts/ui-search.
Run from the repository root: python scripts/verify_search_resources_ui.py
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main() -> None:
    import uvicorn
    from playwright.async_api import async_playwright, expect
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    class ReadyServer(uvicorn.Server):
        def __init__(self, config):
            super().__init__(config)
            self.ready = asyncio.Event()

        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            self.ready.set()

    with tempfile.TemporaryDirectory(prefix="dra-search-ui-") as directory:
        os.environ["RUNTIME_CONFIG_PATH"] = str(Path(directory) / "config.json")
        for field in (
            "API_KEY",
            "LLM_API_KEY",
            "TAVILY_API_KEY",
            "SERPER_API_KEY",
            "XAI_API_KEY",
            "CATALOG_ENCRYPTION_KEY",
        ):
            os.environ[field] = ""
        from deep_research.api import app
        from deep_research.catalog.repository import CatalogRepository
        from deep_research.config import Settings
        from deep_research.persistence.db import create_all

        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(directory) / 'test.db'}")
        await create_all(engine)
        app.state.catalog = CatalogRepository(async_sessionmaker(engine, expire_on_commit=False))
        app.state.settings = Settings(llm_base_url=None, api_key="")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        server = ReadyServer(uvicorn.Config(app, lifespan="off", log_level="error"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        screenshots = ROOT / "artifacts" / "ui-search"
        screenshots.mkdir(parents=True, exist_ok=True)
        try:
            async with asyncio.timeout(60):
                await server.ready.wait()
                assert server.started
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True)
                    page = await browser.new_page(
                        viewport={"width": 1440, "height": 1080}, reduced_motion="reduce"
                    )
                    await page.add_init_script("localStorage.setItem('dr_welcome_tour_seen', '1')")
                    failures = []
                    page.on("pageerror", lambda error: failures.append(str(error)))

                    async def local_only(route):
                        if route.request.url.startswith(origin):
                            await route.continue_()
                        else:
                            await route.abort()

                    await page.route("**/*", local_only)
                    await page.goto(f"{origin}/agents?tab=keys")
                    await expect(page.get_by_role("button", name="新建检索档案")).to_be_visible()
                    for index, label in enumerate(("新闻主账号", "新闻备用账号")):
                        await page.locator(".key-create-grid select").select_option("responses")
                        await page.get_by_label("备注", exact=True).fill(label)
                        await page.get_by_label("RESPONSES API Key", exact=True).fill(
                            f"synthetic-only-{index}"
                        )
                        await page.get_by_label("优先级", exact=True).fill(str(index))
                        async with page.expect_response(
                            lambda response: (
                                response.url.endswith("/api/search-keys")
                                and response.request.method == "POST"
                            )
                        ) as saved:
                            await page.get_by_role("button", name="添加 Key", exact=True).click()
                        assert (await saved.value).status == 201
                    await expect(page.get_by_text("新闻备用账号", exact=True)).to_be_visible()
                    await page.get_by_role("button", name="新建检索档案", exact=True).click()
                    await page.get_by_label("档案名称", exact=True).fill("新闻专属搜索")
                    await page.get_by_role("combobox", name="检索协议", exact=True).select_option(
                        "responses"
                    )
                    await page.get_by_label("请求端点", exact=True).fill(
                        "https://search.example/v1/responses"
                    )
                    await page.get_by_label("搜索模型", exact=True).fill("news-search-model")
                    await page.get_by_role("checkbox", name="新闻主账号", exact=False).check()
                    await page.get_by_role("checkbox", name="新闻备用账号", exact=False).check()
                    async with page.expect_response(
                        lambda response: (
                            response.url.endswith("/api/search-profiles")
                            and response.request.method == "POST"
                        )
                    ) as saved:
                        await page.get_by_role("button", name="保存检索档案", exact=True).click()
                    profile = await (await saved.value).json()
                    assert len(profile["key_ids"]) == 2
                    await expect(page.get_by_text("新闻专属搜索", exact=True)).to_be_visible()
                    await page.screenshot(
                        path=str(screenshots / "search-resources.png"), full_page=True
                    )

                    await page.goto(f"{origin}/settings")
                    await page.get_by_role("checkbox", name="新闻专属搜索", exact=True).check()
                    await page.get_by_role("checkbox", name="Tavily", exact=True).uncheck()
                    async with page.expect_response(
                        lambda response: (
                            response.url.endswith("/api/config")
                            and response.request.method == "PUT"
                        )
                    ) as saved:
                        await page.get_by_role("button", name="保存设置", exact=False).click()
                    assert (await saved.value).status == 200
                    assert (await (await saved.value).json())["search_profile_ids"] == [
                        profile["id"]
                    ]

                    await page.goto(f"{origin}/agents")
                    await page.get_by_role("button", name="新建角色", exact=True).click()
                    await page.get_by_placeholder("如 my-critic", exact=True).fill(
                        "news-researcher"
                    )
                    await page.get_by_placeholder("如 严苛评审员", exact=True).fill("新闻研究员")
                    await page.get_by_role(
                        "checkbox", name="继承全局默认检索档案", exact=True
                    ).uncheck()
                    await page.get_by_role("checkbox", name="新闻专属搜索", exact=True).check()
                    await page.get_by_label("角色指令（留空使用内置默认）", exact=True).fill(
                        "关注发布时间，区分事实与观点。"
                    )
                    await page.get_by_role("button", name="预览最终提示词", exact=True).click()
                    await expect(
                        page.get_by_text("最终 System Prompt（含输出格式）", exact=True)
                    ).to_be_visible()
                    dialog = page.get_by_role("dialog", name="新建角色", exact=True)
                    assert await dialog.evaluate(
                        """element => {
                            const rect = element.getBoundingClientRect();
                            return rect.top >= 0 && rect.bottom <= window.innerHeight
                                && ['auto', 'scroll'].includes(getComputedStyle(element).overflowY);
                        }"""
                    )
                    await page.screenshot(path=str(screenshots / "role-binding.png"))
                    async with page.expect_response(
                        lambda response: (
                            response.url.endswith("/api/agents")
                            and response.request.method == "POST"
                        )
                    ) as saved:
                        await page.get_by_role("button", name="保存角色", exact=True).click()
                    card = await (await saved.value).json()
                    assert card["search_profile_ids"] == [profile["id"]]
                    assert card["prompt_mode"] == "append"

                    await page.set_viewport_size({"width": 390, "height": 844})
                    await page.goto(f"{origin}/agents?tab=keys")
                    await expect(
                        page.get_by_role("button", name="新建检索档案", exact=True)
                    ).to_be_visible()
                    await page.screenshot(
                        path=str(screenshots / "search-mobile.png"), full_page=True
                    )
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth + 1"
                    )
                    assert not failures, failures
                    await browser.close()
                    print(
                        "Browser flow passed: 2 keys, search profile, default selection, "
                        "role binding, prompt preview, mobile layout."
                    )
        finally:
            server.should_exit = True
            await serving
            sock.close()
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
