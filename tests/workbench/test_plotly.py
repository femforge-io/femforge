"""Figure conventions: the export name a figure gives itself and the titled copy it exports as."""

import plotly.graph_objects as go

from femforge_workbench.plotly import DEFAULT_NAME, TITLED_TOP_MARGIN, export_options, layout_json, title_of, with_title


class _Layoutless(go.Figure):
    """A figure whose JSON carries no layout."""

    def to_plotly_json(self) -> dict[str, object]:
        return {"data": []}


def test_a_figure_without_a_layout_has_an_empty_one_and_the_default_name() -> None:
    figure = _Layoutless()

    assert layout_json(figure) == {}
    assert export_options(figure)["filename"] == DEFAULT_NAME


def test_the_meta_title_wins_over_the_on_screen_one() -> None:
    figure = go.Figure(layout={"title": {"text": "On screen"}, "meta": {"title": "Force vs gap"}})

    assert title_of(figure) == "Force vs gap"
    assert export_options(figure)["filename"] == "Force-vs-gap"


def test_with_title_centres_the_name_at_the_top_of_a_copy() -> None:
    figure = go.Figure(layout={"meta": {"title": "Force"}})

    titled = with_title(figure)
    layout = layout_json(titled)

    assert titled is not figure and layout["title"] == {"text": "Force", "x": 0.5, "xanchor": "center"}
    assert layout["margin"] == {"t": TITLED_TOP_MARGIN}
    assert "title" not in layout_json(figure)


def test_with_title_leaves_an_untitled_figure_untitled() -> None:
    titled = with_title(go.Figure())

    assert "title" not in layout_json(titled)
