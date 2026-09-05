import json
from enum import Enum
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from skimage.color import rgb2lab


class CardType(Enum):
    UNKNOWN = "UNKNOWN"
    KK = "KK"
    KKSP = "KKSP"
    KKS = "KKS"
    SCENE = "SCENE"
    

def get_card_type(card: str | Path | bytes):
    if isinstance(card, (str, Path)):
        card = Path(card).read_bytes()

    card_type = CardType.UNKNOWN
    if b"KoiKatuChara" in card:
        card_type = CardType.KK
        if b"KoiKatuCharaSP" in card:
            card_type = CardType.KKSP
        elif b"KoiKatuCharaSun" in card:
            card_type = CardType.KKS
        elif b"sceneInfo" in card:
            card_type = CardType.SCENE

    return card_type


def is_male(image_bytes: bytes):
    return b'sex\x00' in image_bytes


def is_coordinate(image_bytes: bytes):
    return b"KoiKatuClothes" in image_bytes


PERSONALITIES = [
    "Sexy Flirt","Ojousama Heiress","Snobby Haughty","Kouhai Underclassman",
    "Mysterious Enigma","Weirdo Space Case","Yamato Nadeshiko","Boyish Tomboy",
    "Pure Heart","Girl Next Door","Chuunibyou Delusional","Motherly Figure",
    "Big Sisterly","Gyaru Airhead","Bad Girl Rebel","Wild Feral",
    "Honor Student","Crabby Sourpuss","Unlucky Girl","Bookish Bookworm",
    "Nervous Timid","Classic Heroine","Trendy Fangirl","Otaku Geek",
    "Yandere","Lazy Slacker","Quiet Introvert","Stubborn Tough Girl",
    "Old-Fashioned Girl","Docile Loner","Friendly Extrovert","Determined Athlete",
    "Honest Sincere","Charming Seductress","Returnee","Dialect Girl",
    "Sadistic","Emotionless","Careful",
]

# ---------------------------------------------------------------------------
# Hair colour description
# ---------------------------------------------------------------------------
#
# Nearest-neighbour colour naming against a ~930-name XKCD colour survey
# (https://xkcd.com/color/rgb/), matched in perceptually-uniform CIELAB space
# via a k-d tree. This gives far more accurate results than a small hand
# picked RGB palette matched with raw Euclidean RGB distance, and stays fast
# even when called thousands of times (tree built once, queries are O(log n),
# and can be batched).


_COLOR_DATA_FILE = "xkcd_colors.json"


@lru_cache(maxsize=1)
def _get_color_matcher() -> tuple[list[str], cKDTree]:
    """Build (once, lazily) the k-d tree used for nearest-colour lookups.

    Returns a tuple of (names, tree) where `tree` is built over the Lab
    representation of each named colour, in the same order as `names`.
    """
    import sys
    
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).resolve().parent.parent  # repo root

    color_data_path = exe_dir / _COLOR_DATA_FILE

    with open(color_data_path, "r", encoding="utf-8") as f:
        palette: dict[str, str] = json.load(f)

    names = list(palette.keys())
    hex_values = list(palette.values())

    rgb_arr = np.array(
        [[int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)] for h in hex_values],
        dtype=np.float64,
    ) / 255.0
    lab_arr = rgb2lab(rgb_arr.reshape(-1, 1, 3)).reshape(-1, 3)

    tree = cKDTree(lab_arr)
    return names, tree


def _rgb_to_lab(rgb_values: np.ndarray) -> np.ndarray:
    """Convert an (N, 3) array of 0-255 RGB values to (N, 3) Lab values."""
    normalized = rgb_values.astype(np.float64) / 255.0
    return rgb2lab(normalized.reshape(-1, 1, 3)).reshape(-1, 3)


def get_simple_color_description(rgb: tuple[int, int, int]) -> str:
    """Return the closest named colour for a single RGB tuple.

    For classifying many colours at once (e.g. an entire character card
    folder), prefer `get_simple_color_descriptions` instead, since it batches
    the k-d tree query and avoids repeated per-call overhead.
    """
    names, tree = _get_color_matcher()
    lab = _rgb_to_lab(np.array([rgb]))
    _, idx = tree.query(lab[0])
    return names[idx]


def get_simple_color_descriptions(rgb_values: list[tuple[int, int, int]]) -> list[str]:
    """Batched version of `get_simple_color_description`.

    Converts and queries all colours in one vectorized call, which is
    significantly faster than calling `get_simple_color_description` in a
    loop when classifying hundreds or thousands of hair colours.
    """
    if not rgb_values:
        return []

    names, tree = _get_color_matcher()
    lab = _rgb_to_lab(np.array(rgb_values))
    _, idxs = tree.query(lab)
    return [names[i] for i in idxs]
