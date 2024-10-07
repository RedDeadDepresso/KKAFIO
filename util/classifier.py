from enum import Enum
from pathlib import Path
from typing import Union


class CardType(Enum):
    UNKNOWN = "UNKNOWN"
    KK = "KK"
    KKSP = "KKSP"
    KKS = "KKS"
    

def get_card_type(card: Union[str, Path, bytes]):
    if isinstance(card, (str, Path)):
        card = Path(card).read_bytes()

    card_type = CardType.UNKNOWN
    if b"KoiKatuChara" in card:
        card_type = CardType.KK
        if b"KoiKatuCharaSP" in card:
            card_type = CardType.KKSP
        elif b"KoiKatuCharaSun" in card:
            card_type = CardType.KKS
    return card_type


def is_male(image_bytes: bytes):
    return b'sex\x00' in image_bytes


def is_coordinate(image_bytes: bytes):
    return b"KoiKatuClothes" in image_bytes