"""Tests for the body color sequence."""

import re

from femforge.viz.glyphs import body_color


def test_body_colors_are_hex_stable_and_keep_neighbours_apart() -> None:
    colors = [body_color(index) for index in range(8)]

    assert all(re.fullmatch(r"#[0-9a-f]{6}", color) for color in colors)
    assert colors == [body_color(index) for index in range(8)]
    assert len(set(colors)) == len(colors)
