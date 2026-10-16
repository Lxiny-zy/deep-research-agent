"""Structural findings reach the writer's page editor or bounded deck rewrite."""

import json

import pytest

from deep_research.agents.base import Blackboard, RunContext
from deep_research.models import ResearchResult
from deep_research.observability import Tracer
from deep_research.workbench.prose_review import ProseReviewer
from deep_research.workbench.slide_content import Slide, SlideDeck, slide_quality
from deep_research.workbench.slide_edit import SlideTextEdits
from deep_research.workbench.writers import SlideWriter
from tests.fakes import FakeLLM, FakeSearch, verified_finding


class AcceptedProse:
    evidence = []

    async def review(self, body):
        return {
            "status": "pass", "issues": [], "can_revise": True,
            "units": [], "decisions": [],
        }


class DeckModel(FakeLLM):
    def __init__(self, initial, fixed, *, fail_edit=False):
        super().__init__()
        self.initial, self.fixed = initial, fixed
        self.fail_edit = fail_edit
        self.schemas, self.pages = [], []

    async def parse(self, system, user, schema, **kwargs):
        self.schemas.append(schema.__name__)
        if schema is SlideDeck:
            return (self.initial if len(self.schemas) == 1 else self.fixed).model_copy(deep=True)
        assert schema is SlideTextEdits
        _, end = json.JSONDecoder().raw_decode(str(user))
        pages = [item["page"] for item in json.loads(str(user)[end:])]
        self.pages.extend(pages)
        if self.fail_edit:
            raise ValueError("controlled edit failure")
        return schema(edits=[
            {"page": page, **self.fixed.slides[page - 1].model_dump(
                include={"title", "bullets", "notes", "citations"},
            )}
            for page in pages
        ])


def deck():
    return SlideDeck(title="演示", slides=[
        Slide(title="方法", bullets=["发现X [1]。"], notes="方法说明 [1]。", citations=[1]),
        Slide(title="结果", bullets=["发现X [1]。"], notes="结果说明 [1]。", citations=[1]),
        Slide(title="结论", bullets=["发现X [1]。"], notes="结论说明 [1]。", citations=[1]),
    ])


async def write(settings, monkeypatch, model, query="研究结果", max_revisions=2):
    from deep_research.workbench import figure_request

    settings.quality = {
        "max_revisions": max_revisions, "register_check": False,
        "require_limitations": False, "adaptive_review": False,
    }
    monkeypatch.setattr(figure_request, "concept_figure_enabled", lambda _: False)
    monkeypatch.setattr(ProseReviewer, "research", lambda *args, **kwargs: AcceptedProse())
    bb = Blackboard(
        query=query, results=[ResearchResult(sub_question="q", findings=[verified_finding()])],
    )
    await SlideWriter().step(
        bb, RunContext(llm=model, search_tool=FakeSearch(), tracer=Tracer(), settings=settings),
    )
    return bb


@pytest.mark.parametrize("problem", ["missing_notes", "duplicate_notes", "long_title"])
async def test_only_pages_with_structural_defects_are_edited(settings, monkeypatch, problem):
    initial, fixed = deck(), deck()
    if problem == "missing_notes":
        initial.slides[0].notes = ""
        expected = [1]
    elif problem == "duplicate_notes":
        initial.slides[1].notes = initial.slides[0].notes
        expected = [1, 2]
    else:
        initial.slides[0].title = "方法说明" * 25
        expected = [1]
    model = DeckModel(initial, fixed)
    bb = await write(settings, monkeypatch, model)
    extras = bb.scratch["workbench"]["extras"]
    assert model.schemas == ["SlideDeck", "SlideTextEdits"]
    assert sorted(model.pages) == expected
    assert extras["revision"]["revisions_used"] == 1
    assert extras["revision"]["remaining"] == []
    assert not slide_quality(extras["deck"])[0]
    delivered = SlideDeck.model_validate(extras["deck"])
    for number, original in enumerate(initial.slides, 1):
        if number not in expected:
            assert delivered.slides[number - 1] == original


async def test_page_count_requires_a_bounded_deck_rewrite(settings, monkeypatch):
    initial, fixed = deck(), deck()
    fixed.slides = fixed.slides[:1]
    model = DeckModel(initial, fixed)
    bb = await write(settings, monkeypatch, model, query="生成2页幻灯片")
    assert model.schemas == ["SlideDeck", "SlideDeck"]
    assert bb.scratch["workbench"]["extras"]["revision"]["remaining"] == []


async def test_structural_edit_failure_preserves_the_deck_without_another_full_generation(
    settings, monkeypatch,
):
    initial, fixed = deck(), deck()
    initial.slides[0].notes = ""
    model = DeckModel(initial, fixed, fail_edit=True)
    bb = await write(settings, monkeypatch, model)
    assert model.schemas == ["SlideDeck", "SlideTextEdits"]
    assert bb.scratch["workbench"]["extras"]["revision"]["stop_reason"] == "revision_failed"
    assert bb.scratch["workbench"]["extras"]["deck"]["slides"][0]["notes"] == ""


async def test_zero_revisions_does_not_edit_a_structurally_incomplete_deck(settings, monkeypatch):
    initial, fixed = deck(), deck()
    initial.slides[0].notes = ""
    model = DeckModel(initial, fixed)
    bb = await write(settings, monkeypatch, model, max_revisions=0)
    assert model.schemas == ["SlideDeck"]
    revision = bb.scratch["workbench"]["extras"]["revision"]
    assert revision["stop_reason"] == "revision_limit" and revision["revisions_used"] == 0
