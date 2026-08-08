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