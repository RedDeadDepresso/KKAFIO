import zipfile
from enum import Enum
from pathlib import Path


# Koikatsu mods are conventionally shipped with a ".zipmod" extension, but a
# mod is really just a plain zip archive containing a manifest.xml — some
# mods (and re-uploads) are distributed as plain ".zip" instead. Anywhere
# KKAFIO needs to decide "is this a mod or a generic archive", it should
# check content (manifest.xml), not just trust the ".zipmod" extension.
MOD_ARCHIVE_EXTENSIONS = (".zipmod", ".zip")


def has_manifest(path: str | Path) -> bool:
    """Return True if the zip archive at `path` contains a manifest.xml at
    any depth (case-insensitive match on the filename, matching the same
    convention used elsewhere to locate it inside a zipmod)."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            return any(n.lower().endswith("manifest.xml") for n in zf.namelist())
    except Exception:
        return False


def is_mod_archive(path: str | Path) -> bool:
    """Return True if `path` should be treated as a Koikatsu mod: a
    ".zipmod" file, or a plain ".zip" file that actually contains a
    manifest.xml (as opposed to a generic archive of loose files, e.g. a
    zipped batch of PNG cards)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".zipmod":
        return True
    if suffix == ".zip":
        return has_manifest(path)
    return False


class CardType(Enum):
    UNKNOWN = "UNKNOWN"
    KK = "KK"
    KKSP = "KKSP"
    KKS = "KKS"
    SCENE = "SCENE"


# Every card type that is a Koikatsu-family *character* card (as opposed to a
# scene, coordinate, or unknown file). kkloader reads all three the same way —
# it never checks the "KoiKatuChara[Sun|SP]" marker or the per-block version
# strings, it just keeps them — so KKS cards can be loaded, edited and saved
# through KoikatuCharaData without being converted, and keep their KKS
# marker/versions on the way back out.
CHARA_CARD_TYPES = (CardType.KK, CardType.KKSP, CardType.KKS)


def get_card_type(card: str | Path | bytes):
    if isinstance(card, (str, Path)):
        card = Path(card).read_bytes()

    card_type = CardType.UNKNOWN

    # A Studio scene embeds one or more full chara blocks (which is why
    # "KoiKatuChara" / "KoiKatuCharaSP" / "KoiKatuCharaSun" markers can
    # appear inside a scene file), so the scene marker must be checked
    # first — checking the chara markers first would misclassify any scene
    # containing a character as a bare chara card. Every scene ends with
    # the "【KStudio】" tail mark, and no chara/coordinate card has it.
    if b"KStudio" in card:
        card_type = CardType.SCENE
    elif b"KoiKatuChara" in card:
        card_type = CardType.KK
        if b"KoiKatuCharaSP" in card:
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
