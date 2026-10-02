from __future__ import annotations

import pytest

from deep_research.workbench.analysis import analyse


@pytest.mark.parametrize("grouped", [True, False])
def test_every_measurement_has_a_distribution_panel_with_pagination(monkeypatch, grouped):
    from deep_research.workbench import analysis

    rendered = []
    original = analysis._png

    def capture(figure):
        rendered.append(
            [
                (ax.get_title(), [t.get_text() for t in ax.get_xticklabels()])
                for ax in figure.axes
                if ax.get_visible()
            ]
        )
        return original(figure)

    monkeypatch.setattr(analysis, "_png", capture)
    header = ("group," if grouped else "") + "x,y,z,u,v\n"
    body = "\n".join(
        (f"{'A' if i < 3 else 'B'}," if grouped else "")
        + f"{i},{'' if i == 0 else i + 1},{i * 2},{i + 3},{i * 3}"
        for i in range(6)
    )
    result = analyse(header + body)
    panels = [item for figure in rendered[:2] for item in figure]
    assert [title for title, _ in panels] == ["x", "y", "z", "u", "v"]
    assert len(rendered[0]) == 4 and len(rendered[1]) == 1
    if grouped:
        assert panels[0][1] == ["A\nn=3", "B\nn=3"]
        assert panels[1][1] == ["A\nn=2", "B\nn=3"]
    assert all(figure.png.startswith(b"\x89PNG") for figure in result.figures)


def test_frozen_new_analysis_reproduces_identical_plot_names_captions_and_bytes():
    csv = "group,a,b,c,d\nA,1,2,3,4\nA,2,3,4,5\nB,3,4,5,6\nB,4,5,6,7\n"
    initial = analyse(csv)
    restored = analyse(csv, frozen=initial.snapshot())
    assert initial.figure_policy == restored.figure_policy == 2
    assert initial.figures == restored.figures
    assert initial.facts() == restored.facts()


def test_legacy_frozen_analysis_keeps_old_three_plots_and_correlation_number():
    csv = "group,a,b,c,d\nA,1,2,3,4\nA,2,3,4,5\nB,3,4,5,6\nB,4,5,6,7\n"
    frozen = analyse(csv).snapshot()
    frozen.pop("figure_policy")
    restored = analyse(csv, frozen=frozen)
    assert restored.figure_policy == 1
    assert [figure.name for figure in restored.figures] == [
        "fig_01_box_a_group.png",
        "fig_02_box_b_group.png",
        "fig_03_box_c_group.png",
        "fig_04_correlation.png",
    ]


def test_insufficient_group_samples_still_have_descriptive_plots():
    result = analyse("group,x,y\nA,1,2\nA,,3\nB,4,5\nB,6,7\n")
    assert any("x 按 group" in issue for issue in result.issues)
    assert "x：A n=1、B n=2" in result.figures[0].caption
    assert "y：A n=2、B n=2" in result.figures[0].caption
