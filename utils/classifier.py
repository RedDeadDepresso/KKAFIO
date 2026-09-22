import json
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from scipy.spatial import cKDTree


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

        # A Studio scene embeds one or more full chara blocks (which is why
        # "KoiKatuCharaSP"/"KoiKatuCharaSun" markers can appear inside a
        # scene file), so the scene marker must be checked before those,
        # not after — checking Sun/SP first would misclassify any scene
        # containing an SP or Sun character as a bare chara/coordinate card.
        if b"sceneInfo" in card:
            card_type = CardType.SCENE
        elif b"KoiKatuCharaSP" in card:
            card_type = CardType.KKSP
        elif b"KoiKatuCharaSun" in card:
            card_type = CardType.KKS

    return card_type


def is_male(image_bytes: bytes):
    return b"sex\x01" not in image_bytes


def is_coordinate(image_bytes: bytes):
    return b"KoiKatuClothes" in image_bytes


PERSONALITIES = [
    "Sexy Flirt",
    "Ojousama Heiress",
    "Snobby Haughty",
    "Kouhai Underclassman",
    "Mysterious Enigma",
    "Weirdo Space Case",
    "Yamato Nadeshiko",
    "Boyish Tomboy",
    "Pure Heart",
    "Girl Next Door",
    "Chuunibyou Delusional",
    "Motherly Figure",
    "Big Sisterly",
    "Gyaru Airhead",
    "Bad Girl Rebel",
    "Wild Feral",
    "Honor Student",
    "Crabby Sourpuss",
    "Unlucky Girl",
    "Bookish Bookworm",
    "Nervous Timid",
    "Classic Heroine",
    "Trendy Fangirl",
    "Otaku Geek",
    "Yandere",
    "Lazy Slacker",
    "Quiet Introvert",
    "Stubborn Tough Girl",
    "Old-Fashioned Girl",
    "Docile Loner",
    "Friendly Extrovert",
    "Determined Athlete",
    "Honest Sincere",
    "Charming Seductress",
    "Returnee",
    "Dialect Girl",
    "Sadistic",
    "Emotionless",
    "Careful",
]


# ---------------------------------------------------------------------------
# Hair colour description
# ---------------------------------------------------------------------------
#
# Nearest-neighbour colour naming against a ~930-name XKCD colour survey
# (https://xkcd.com/color/rgb/), matched in perceptually-uniform CIELAB space
# via a k-d tree.
#
# numpy, scipy and scikit-image are imported lazily so importing this module
# does not require loading those relatively heavy dependencies immediately.


_COLOR_DATA_FILE = "xkcd_colors.json"


@lru_cache(maxsize=1)
def _get_color_matcher() -> tuple[list[str], "cKDTree"]:
    """Build, once and lazily, the k-d tree used for colour lookups.

    Returns:
        A tuple of (names, tree), where ``tree`` is built over the Lab
        representation of each named colour, in the same order as ``names``.
    """
    import numpy as np
    from scipy.spatial import cKDTree
    from skimage.color import rgb2lab

    if getattr(__import__("sys"), "frozen", False):
        exe_dir = Path(__import__("sys").executable).parent
    else:
        exe_dir = Path(__file__).resolve().parent.parent

    color_data_path = exe_dir / "assets" / "data" / _COLOR_DATA_FILE

    with color_data_path.open("r", encoding="utf-8") as f:
        palette: dict[str, str] = json.load(f)

    names = list(palette.keys())
    hex_values = list(palette.values())

    rgb_arr = np.array(
        [
            [
                int(h[1:3], 16),
                int(h[3:5], 16),
                int(h[5:7], 16),
            ]
            for h in hex_values
        ],
        dtype=np.float64,
    ) / 255.0

    lab_arr = rgb2lab(rgb_arr.reshape(-1, 1, 3)).reshape(-1, 3)

    tree = cKDTree(lab_arr)

    return names, tree


def _rgb_to_lab(rgb_values: "np.ndarray") -> "np.ndarray":
    """Convert an (N, 3) array of 0-255 RGB values to Lab values."""
    import numpy as np
    from skimage.color import rgb2lab

    normalized = rgb_values.astype(np.float64) / 255.0

    return rgb2lab(
        normalized.reshape(-1, 1, 3)
    ).reshape(-1, 3)


def get_simple_color_description(
    rgb: tuple[int, int, int],
) -> str:
    """Return the closest named colour for a single RGB tuple.

    For classifying many colours at once, prefer
    ``get_simple_color_descriptions`` since it batches the k-d tree query.
    """
    import numpy as np

    names, tree = _get_color_matcher()

    lab = _rgb_to_lab(np.array([rgb]))

    _, idx = tree.query(lab[0])

    return names[idx]


def get_simple_color_descriptions(
    rgb_values: list[tuple[int, int, int]],
) -> list[str]:
    """Return the closest named colours for multiple RGB tuples.

    Converts and queries all colours in one vectorized call, avoiding
    repeated per-call overhead when classifying many colours.
    """
    if not rgb_values:
        return []

    import numpy as np

    names, tree = _get_color_matcher()

    lab = _rgb_to_lab(np.array(rgb_values))

    _, idxs = tree.query(lab)

    return [names[i] for i in idxs]