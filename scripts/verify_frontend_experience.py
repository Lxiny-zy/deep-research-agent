"""Extended browser checks, called by verify_workspace_ui.py --extended.

All mutations target that script's disposable SQLite database and config.
No research worker or provider request is started. axe-core is optional; place
its axe.min.js in artifacts/frontend-refresh-20260911/ to include WCAG checks.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.async_api import expect

ROOT = Path(__file__).resolve().parents[1]
AXE = ROOT / "artifacts/frontend-refresh-20260911/axe.min.js"

LAYOUT = """() => {
  const clipped = [...document.querySelectorAll('button,input,select,textarea,[role="tab"]')]
    .filter(el => {
      if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height || (r.left >= -1 && r.right <= innerWidth + 1)) return false;
      // Canvas nodes and explicitly scrollable rails have their own viewport.
      if (el.closest('.react-flow,.runtime-pipeline-scroll')) return false;
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        if (/auto|scroll/.test(getComputedStyle(p).overflowX) && p.scrollWidth > p.clientWidth)
          return false;
      }
      return true;
    }).map(el => ({tag: el.tagName,
      name: el.getAttribute('aria-label') || el.textContent.trim().slice(0, 65)}));
  return {width: innerWidth, scrollWidth: document.documentElement.scrollWidth, clipped};
}"""


async def inspect_experience(page, origin: str, output: Path, original_run: str, repo) -> dict:
    from deep_research.models import Report
    from deep_research.observability import Event

    result: dict = {"layouts": {}, "accessibility": {}, "interactions": [], "motion": {}}
    if AXE.exists():

        async def audit_script(route):
            await route.fulfill(
                content_type="text/javascript", body=AXE.read_text(encoding="utf-8")
            )

        await page.route(origin + "/__ui-audit/axe.js", audit_script)
    report_id = await repo.create_run("多智能体研究系统：证据质量、协作方式与应用边界")
    await repo.save_report(
        report_id,
        Report(
            query="多智能体研究系统",
            markdown="""# 多智能体研究系统观察

> 以下为界面验收用合成内容，用于检查中文长文、引用、表格和代码排版。

## 一、研究结论

研究质量来自清晰的问题边界、可追溯的证据，以及对不确定性的持续复核。
在多角色协作中，规划、检索与反思需要共享上下文，同时保留每一步的来源。[1]

## 二、协作方式对照

| 协作方式 | 适用问题 | 主要优势 | 需要检查的边界 |
| --- | --- | --- | --- |
| 顺序研究 | 明确且可拆解的问题 | 步骤清晰，容易复核 | 前序结论是否充分 |
| 并行检索 | 多个相互独立的方向 | 扩大覆盖面 | 来源重叠与观点冲突 |
| 反思循环 | 证据尚不充分的问题 | 补充关键缺口 | 停止条件与研究预算 |

## 三、可复核的实践

1. 记录问题、时间范围和来源类型。
2. 对关键论断保留引用，而不是只保留最终摘要。
3. 明确列出尚未解决的问题。[2]

```python
stages = ["plan", "research", "reflect", "synthesize"]
research_plan = {"question": "如何提高研究的可复核性？", "stages": stages}
```

### 下一步

结合真实任务检查证据覆盖率，并在研究历史中继续追问。
""",
            citations=["https://example.org/evidence-one", "https://example.org/evidence-two"],
        ),
    )
    await repo.finalize(report_id, elapsed=123.4, total_tokens=6384)
    await repo.set_tags(report_id, ["多智能体", "证据质量"])
    live_id = await repo.create_run("正在研究：多角色如何共同验证一个结论？")
    await repo.set_status(live_id, "running")
    await repo.save_events(
        live_id,
        [
            Event(
                stage="ORCHESTRATOR",
                type="info",
                message="研究计划已确认",
                elapsed=0,
                data={"event_name": "workflow.plan", "total_steps": 4},
            ),
            Event(
                stage="RESEARCHER",
                type="info",
                message="正在检索多智能体协作相关来源",
                elapsed=8,
                data={
                    "event_name": "step.running",
                    "step_run_id": "ui-search",
                    "agent": "researcher",
                    "label": "并行检索",
                },
            ),
        ],
    )

    async def visit(path: str, ready: str = ".app-container") -> None:
        await page.goto(origin + path, wait_until="domcontentloaded")
        await page.locator(ready).first.wait_for()
        # Let route data and layout settle without waiting on a live SSE stream.
        await page.wait_for_timeout(450)
        await page.evaluate("document.fonts.ready")

    async def capture(name: str, *, axe: bool = True, full: bool = True) -> None:
        layout = await page.evaluate(LAYOUT)
        result["layouts"][name] = layout
        await page.screenshot(path=str(output / f"{name}.png"), full_page=full)
        if axe and AXE.exists():
            if not await page.evaluate("Boolean(window.axe)"):
                await page.add_script_tag(url=origin + "/__ui-audit/axe.js")
            result["accessibility"][name] = await page.evaluate("""async () => {
              const result = await axe.run(document, {
                runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa']}
              });
              return result.violations.map(v => ({id: v.id, impact: v.impact,
                nodes: v.nodes.map(n => ({target: n.target, summary: n.failureSummary}))}));
            }""")
        (output / "experience.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def check_dialog(name: str) -> None:
        dialog = page.get_by_role("dialog")
        await expect(dialog).to_be_visible()
        await capture(name, full=False)
        result["layouts"][name]["dialog"] = await dialog.evaluate("""el => {
          const r = el.getBoundingClientRect();
          return {x: r.x, y: r.y, width: r.width, height: r.height, viewport: innerHeight};
        }""")
        bounds = result["layouts"][name]["dialog"]
        assert bounds["x"] >= -1 and bounds["y"] >= -1, (name, bounds)
        assert bounds["x"] + bounds["width"] <= result["layouts"][name]["width"] + 1, (
            name,
            bounds,
        )
        assert bounds["y"] + bounds["height"] <= bounds["viewport"] + 1, (name, bounds)
        if name.endswith("-model-editor"):
            save = dialog.get_by_role("button", name="保存", exact=True)
            await save.scroll_into_view_if_needed()
            await expect(save).to_be_in_viewport()
            result["interactions"].append(f"{name}: save action reachable by scrolling")
        tabs = dialog.locator(".workflow-mobile-tabs")
        if await tabs.is_visible():
            bounds = await tabs.bounding_box()
            body_bounds = await dialog.locator(".workflow-studio-body").bounding_box()
            assert bounds and bounds["height"] <= 64, (name, bounds)
            assert body_bounds and body_bounds["height"] >= 160, (name, body_bounds)
            await tabs.get_by_role("button", name="角色库", exact=True).click()
            await expect(dialog.locator(".workflow-library")).to_be_visible()
            await tabs.get_by_role("button", name="检查器", exact=True).click()
            await expect(dialog.locator(".workflow-inspector")).to_be_visible()
            await tabs.get_by_role("button", name="画布", exact=True).click()
        await page.keyboard.press("Shift+Tab")
        assert await dialog.evaluate("el => el.contains(document.activeElement)"), name
        await page.keyboard.press("Escape")
        await expect(dialog).to_have_count(0)
        result["interactions"].append(f"{name}: focus stays inside; Escape closes")

    for width, height in ((1440, 1000), (768, 1024), (390, 844), (320, 740)):
        await page.set_viewport_size({"width": width, "height": height})
        for name, path, ready in (
            ("welcome", "/welcome", ".research-welcome"),
            ("new", "/", ".research-composer"),
            ("history", "/history", ".history-run-list"),
            ("roles", "/agents", ".builtin-card"),
            ("models-empty", "/agents?tab=models", "[role=tabpanel]"),
            ("search", "/agents?tab=keys", ".search-profile-manager"),
            ("settings", "/settings", ".settings-fields"),
            ("workflows-empty", "/workflows", ".workflow-custom-rail"),
            ("report-long", f"/runs/{report_id}", ".report-view-body"),
            ("run-live", f"/runs/{live_id}", ".research-live-overview"),
            ("not-found", "/missing-page", ".route-state"),
        ):
            await visit(path, ready)
            await capture(f"{width}-{name}", axe=width in (1440, 390))

        await visit("/history", ".history-run-list")
        await page.get_by_label("搜索关键词").fill("不存在的验收关键词")
        await expect(page.get_by_text("没有符合条件的记录")).to_be_visible()
        await capture(f"{width}-history-filter-empty", axe=width == 390)
        await page.get_by_role("button", name="清除全部筛选").click()
        await expect(page.locator(".history-run-row").first).to_be_visible()

        await visit("/workflows", ".workflow-custom-rail")
        await page.get_by_role("button", name="浏览内置模板").click()
        await expect(page.locator(".workflow-templates")).to_have_attribute("open", "")
        template_position = await page.locator(".workflow-templates > summary").evaluate(
            """el => ({
                top: el.getBoundingClientRect().top,
                headerBottom: document.querySelector('.global-header')
                    .getBoundingClientRect().bottom
            })"""
        )
        assert template_position["top"] >= template_position["headerBottom"], template_position
        await capture(f"{width}-workflow-templates", axe=width == 390, full=False)
        await page.get_by_role("button", name="新建工作流", exact=True).click()
        await check_dialog(f"{width}-workflow-editor")

        await visit("/agents", ".builtin-card")
        await page.get_by_role("button", name="新建角色", exact=True).click()
        await check_dialog(f"{width}-role-editor")
        await page.get_by_role("tab", name="模型档案").click()
        await expect(page).to_have_url(origin + "/agents?tab=models")
        await page.reload(wait_until="domcontentloaded")
        await expect(page.get_by_role("tab", name="模型档案")).to_have_attribute(
            "aria-selected", "true"
        )
        await page.get_by_role("button", name="新建档案").click()
        await check_dialog(f"{width}-model-editor")
        if width < 1000:
            toggle = page.get_by_role("button", name="打开导航")
            if await toggle.is_visible():
                await toggle.click()
                await capture(f"{width}-navigation", axe=width == 390, full=False)
                await page.keyboard.press("Escape")
                await expect(toggle).to_be_focused()
                await expect(toggle).to_have_attribute("aria-expanded", "false")

    result["interactions"].append(
        "catalog tabs survive reload; empty history filters reset; "
        "template scroll clears navigation"
    )
    # Additional stateful checks are kept separate from the viewport sweep.
    await inspect_states(
        page, origin, output, repo, original_run, report_id, result, visit, capture, check_dialog
    )
    (output / "experience.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    failures = {
        key: value
        for key, value in result["layouts"].items()
        if value["scrollWidth"] > value["width"] or value["clipped"]
    }
    assert not failures, f"Clipped controls: {failures}"
    violations = {key: value for key, value in result["accessibility"].items() if value}
    assert not violations, f"Accessibility violations: {violations}"
    return result


async def inspect_states(
    page, origin, output, repo, original_run, report_id, result, visit, capture, check_dialog
):
    """Exercise persisted state, error recovery and animation controls."""
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await visit(f"/runs/{original_run}", ".research-live-overview")
    await expect(page.get_by_text("正在确认工作流阶段")).to_have_count(0)
    await visit(f"/runs/{report_id}", ".report-view-body")
    await page.get_by_role("button", name="打印预览", exact=True).click()
    await capture("1440-report-print-preview")
    await page.emulate_media(media="print")
    await page.pdf(path=str(output / "report-print.pdf"), print_background=True)
    await page.emulate_media(media="screen")

    async def create(path, data):
        response = await page.request.post(origin + path, data=data)
        assert response.ok, (path, await response.text())
        return await response.json()

    model = await create(
        "/api/models",
        {
            "name": "研究用模型",
            "model": "research-model",
            "base_url": "https://example.org/v1",
            "api_key": "synthetic-ui-key",
            "is_default": True,
        },
    )
    await create(
        "/api/agents",
        {
            "name": "evidence-reviewer",
            "display_name": "循证研究员",
            "behavior": "research",
            "description": "关注原始来源与证据之间的联系，对重要论断进行交叉验证。",
            "model_profile_id": model["id"],
        },
    )
    await create(
        "/api/workflows/custom",
        {
            "name": "evidence-workflow",
            "display_name": "循证研究流程",
            "description": "从问题规划开始，经并行检索与反思，再形成带引用的研究报告。",
            "steps": [
                {"kind": "agent", "agent": name}
                for name in ("planner", "researcher", "synthesizer")
            ],
        },
    )
    for width, height in ((1440, 1000), (390, 844)):
        await page.set_viewport_size({"width": width, "height": height})
        for name, path in (
            ("models", "/agents?tab=models"),
            ("roles-populated", "/agents"),
            ("workflows", "/workflows"),
        ):
            await visit(path)
            await capture(f"{width}-{name}-populated")

        await visit("/welcome", ".research-welcome")
        await page.get_by_role("button", name="入门引导", exact=True).click()
        await check_dialog(f"{width}-onboarding")

    async def config_error(route):
        await route.fulfill(status=503, json={"detail": "服务暂时不可用，请稍后重试。"})

    await page.route("**/api/config", config_error)
    await visit("/", ".boot-screen-error")
    await capture("390-connection-error")
    await page.unroute("**/api/config", config_error)
    await page.get_by_role("button", name="重试", exact=True).click()
    await expect(page.locator(".research-composer")).to_be_visible()
    result["interactions"].append("connection error can be retried without losing credentials")

    async def config_guest(route):
        await route.fulfill(status=401, json={"detail": "访问密钥无效"})

    await page.route("**/api/config", config_guest)
    for width, height in ((1440, 1000), (390, 844), (320, 740)):
        await page.set_viewport_size({"width": width, "height": height})
        await visit("/welcome", ".research-welcome")
        await page.get_by_role("button", name="开启研究", exact=True).click()
        await check_dialog(f"{width}-login")
    await page.unroute("**/api/config", config_guest)

    await page.set_viewport_size({"width": 1440, "height": 1000})
    await page.emulate_media(reduced_motion="no-preference")
    await visit("/", ".research-composer")
    canvas = page.locator(".ambient-particles")
    before = await canvas.evaluate("el => el.toDataURL()")
    await page.wait_for_timeout(250)
    assert before != await canvas.evaluate("el => el.toDataURL()"), (
        "Ambient animation does not advance"
    )
    await page.get_by_role("button", name="暂停背景动效", exact=True).click()
    await expect(page.locator(".app-container")).to_have_attribute("data-atmosphere-paused", "true")
    await page.wait_for_timeout(80)
    frozen = await canvas.evaluate("el => el.toDataURL()")
    trace = await page.locator(".page-motif .motif-signal").evaluate(
        "el => getComputedStyle(el).strokeDashoffset"
    )
    await page.wait_for_timeout(300)
    assert frozen == await canvas.evaluate("el => el.toDataURL()"), "Paused canvas still moves"
    assert trace == await page.locator(".page-motif .motif-signal").evaluate(
        "el => getComputedStyle(el).strokeDashoffset"
    )
    await page.reload(wait_until="domcontentloaded")
    await expect(page.get_by_role("button", name="播放背景动效", exact=True)).to_be_visible()
    await page.get_by_role("button", name="播放背景动效", exact=True).click()
    await page.emulate_media(reduced_motion="reduce")
    await expect(page.get_by_role("button", name="背景动效已按系统设置暂停")).to_be_disabled()
    result["motion"] = {
        "canvas_advances": True,
        "pause_freezes_canvas_and_svg": True,
        "pause_survives_reload": True,
        "system_preference_updates_live": True,
    }

    await page.emulate_media(reduced_motion="no-preference")
    await visit("/welcome", ".research-welcome")
    await capture("1440-welcome-motion", axe=False, full=False)
    result["motion"]["welcome_field_state"] = await page.locator(".research-field").get_attribute(
        "data-state"
    )
    if await page.get_by_role("button", name="暂停背景动画").count():
        await page.get_by_role("button", name="暂停背景动画").click()
        await expect(page.get_by_role("button", name="播放背景动画")).to_be_visible()
    await page.emulate_media(reduced_motion="reduce")
