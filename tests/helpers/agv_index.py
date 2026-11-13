"""A tiny hand-made character index (the shape kkafio.indices.agv loads), shared by the agv tests and the
tests of the tasks that look characters up in it."""

from kkafio.indices.text import norm


def make_index(chars=None):
    return {
        "version": 1,
        "langs": ["en_us", "ja_jp"],
        "series": [["Fate/stay night", "フェイト/ステイナイト"], ["Sword Art Online", ""],
                   ["", "カウボーイビバップ"]],          # no English title
        "aliases": {norm("Cowboy Bebop"): 2, norm("カウボーイビバップ"): 2, norm("Fate/stay night"): 0, norm("Fate/stay night: Unlimited Blade Works"): 0,
                    norm("Sword Art Online"): 1, norm("Sword Art Online II"): 1},
        "chars": chars if chars is not None else [
            [["Tohsaka, Rin", "遠坂, 凛"], [0], 50000],
            [["Rin", ""], [0], 10],
            [["Rin", ""], [1], 5],
        ],
        "names": {norm("Tohsaka, Rin"): [0], norm("TohsakaRin"): [0], norm("RinTohsaka"): [0],
                  norm("遠坂凛"): [0], norm("Rin"): [1, 2]},
    }
