from enum import Enum
from pathlib import Path


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

_COLOR_PALETTE = {
    "black":      (0,   0,   0),
    "white":      (255, 255, 255),
    "gray":       (128, 128, 128),
    "light gray": (211, 211, 211),
    "dark gray":  (169, 169, 169),
    "red":        (255, 0,   0),
    "dark red":   (139, 0,   0),
    "maroon":     (128, 0,   0),
    "pink":       (255, 192, 203),
    "light pink": (255, 182, 193),
    "magenta":    (255, 0,   255),
    "orange":     (255, 165, 0),
    "yellow":     (255, 255, 0),
    "gold":       (255, 215, 0),
    "beige":      (245, 245, 220),
    "brown":      (165, 42,  42),
    "dark brown": (101, 67,  33),
    "green":      (0,   128, 0),
    "light green":(144, 238, 144),
    "dark green": (0,   100, 0),
    "teal":       (0,   128, 128),
    "cyan":       (0,   255, 255),
    "blue":       (0,   0,   255),
    "light blue": (173, 216, 230),
    "dark blue":  (0,   0,   139),
    "purple":     (128, 0,   128),
}


def get_simple_color_description(rgb: tuple[int, int, int]) -> str:
    """Return the closest named colour for an RGB tuple."""
    r, g, b = rgb
    best_name = "unknown"
    best_dist = float("inf")
    for name, (pr, pg, pb) in _COLOR_PALETTE.items():
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name
