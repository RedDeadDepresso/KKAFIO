"""Hair colour naming (hair_color.py) and how chara_key reads a card's hair colour."""

import pytest

from kkafio.cards.chara import chara_key
from kkafio.cards.chara.hair_color import hair_color_name


@pytest.mark.parametrize("rgb,name", [
    ((10, 10, 10), "black"), ((20, 25, 45), "blue-black"), ((40, 40, 45), "black"),
    ((60, 40, 30), "dark brown"), ((110, 70, 40), "brown"), ((140, 90, 60), "brown"),
    ((170, 120, 80), "light brown"), ((190, 160, 100), "dark blonde"),
    ((240, 215, 120), "blonde"), ((245, 240, 200), "platinum"),
    ((200, 90, 40), "ginger"), ((250, 160, 50), "orange"), ((190, 30, 40), "red"),
    ((250, 150, 190), "pink"), ((100, 20, 60), "burgundy"), ((190, 160, 230), "lavender"),
    ((120, 60, 160), "purple"), ((70, 120, 220), "blue"), ((150, 200, 250), "light blue"),
    ((25, 40, 90), "navy"), ((40, 170, 170), "teal"), ((80, 180, 90), "green"),
    ((200, 200, 210), "silver"), ((130, 130, 135), "grey"), ((250, 250, 250), "white"),
])
def test_names(rgb, name):
    assert hair_color_name(rgb) == name


def test_no_colour_is_unknown_not_black():
    assert hair_color_name(None) == "unknown"


def test_every_colour_gets_a_name():
    for r in range(0, 256, 17):
        for g in range(0, 256, 17):
            for b in range(0, 256, 17):
                assert hair_color_name((r, g, b))


def test_unity_to_rgb_rounds():
    assert chara_key.unity_to_rgb(0.5, 0.99999994, 0) == (128, 255, 0)


def _card(parts):
    return {"Custom": {"hair": {"parts": parts}}}


def test_hair_colour_of_a_card():
    assert chara_key.hair_color(_card([{"id": 1, "baseColor": [1.0, 0.0, 0.0, 1.0]}])) == (255, 0, 0)
    assert chara_key.hair_color(_card([{"id": 0}, {"id": 0}])) is None      # nothing to read
