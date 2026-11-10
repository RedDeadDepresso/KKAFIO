"""
chara_key.py — Build the "lastname | firstname | nickname | personality | hair_color"
identification
key used by both GroupChara and RenameChara to describe a chara card to an
LLM and to match the LLM's JSON response back to the right file.

This was previously copy-pasted (with tiny formatting drift) into both
src/kkafio/tasks/group_chara.py and src/kkafio/tasks/rename_chara.py; kept in one place now so a
future change to how a character is identified doesn't need to be made
twice, and can't quietly drift into producing different keys for the same
card depending on which task built it.

Reading a card is the expensive part, so it is split in two: `chara_info(kc)` reads the raw
identity fields off a loaded card (CharaInfo — this is what the chara info cache stores), and
`key_of(info)` turns those fields into the key string. Changing how the key is built (the hair
colour buckets, the personality names, ...) therefore never needs a card to be read again.
"""

from __future__ import annotations

from dataclasses import dataclass

from kkloader import KoikatuCharaData

from kkafio.cards.chara.hair_color import hair_color_name
from kkafio.cards.classifier import PERSONALITIES


def unity_to_rgb(r: float, g: float, b: float) -> tuple[int, int, int]:
    """Convert Unity's 0.0-1.0 float color channels to 0-255 ints (rounded, so 0.5 is 128 and a
    float32 0.99999994 is 255, not 254)."""
    return (round(r * 255), round(g * 255), round(b * 255))


def hair_color(kc: KoikatuCharaData) -> tuple[int, int, int] | None:
    """Best-effort base hair color for a loaded chara card, as 0-255 RGB; None when the card has
    no hair colour to read (it is not reported as black)."""
    parts = kc["Custom"]["hair"]["parts"]
    for i, part in enumerate(parts):
        if part.get("id", 0) == 0 and i != 1:
            continue
        if i == 3:
            continue
        base = part.get("baseColor")
        if not base:
            continue
        # baseColor is a list [r, g, b, a] of 0.0-1.0 floats
        if isinstance(base, (list, tuple)) and len(base) >= 3:
            return unity_to_rgb(base[0], base[1], base[2])
        # Fallback: dict with r/g/b keys
        if isinstance(base, dict):
            vals = list(base.values())
            if len(vals) >= 3:
                return unity_to_rgb(vals[0], vals[1], vals[2])
    return None


def _name_part(value) -> str:
    """One name field as plain text, with any ' | ' separator look-alike removed
    so a name can't be mistaken for a field boundary in the key."""
    return str(value or "").replace("|", "/").strip()


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class CharaInfo:
    """What is read off a chara card to identify it — raw values, nothing derived.

    Names are as stored on the card (the key's `|` handling is `key_of`'s job), the hair colour
    is 0-255 RGB (its name comes from `hair_color_name`), the personality is the card's index
    (its name comes from PERSONALITIES), `birth` is (birthMonth, birthDay).

    A new field is added here, in `chara_info`, and in `to_json` / `from_json`. Entries cached
    without it fail `from_json`, so they count as a miss and the card is read once more.
    """
    last: str
    first: str
    nick: str
    personality: int
    hair: tuple[int, int, int] | None
    birth: tuple[int | None, int | None]

    def to_json(self) -> dict:
        return {"last": self.last, "first": self.first, "nick": self.nick,
                "pers": self.personality,
                "hair": list(self.hair) if self.hair is not None else None,
                "birth": list(self.birth)}

    @classmethod
    def from_json(cls, data) -> CharaInfo | None:
        """The info stored by `to_json`, or None if `data` isn't a complete, well-formed entry."""
        try:
            last, first, nick = data["last"], data["first"], data["nick"]
            pers, hair, birth = data["pers"], data["hair"], data["birth"]
            if not (isinstance(last, str) and isinstance(first, str) and isinstance(nick, str)
                    and _is_int(pers)):
                return None
            if hair is not None:
                if len(hair) != 3 or not all(_is_int(c) for c in hair):
                    return None
                hair = tuple(hair)
            if len(birth) != 2 or not all(v is None or _is_int(v) for v in birth):
                return None
            return cls(last, first, nick, pers, hair, tuple(birth))
        except (KeyError, TypeError):
            return None


def _birthday(kc: KoikatuCharaData) -> tuple[int | None, int | None]:
    """(birthMonth, birthDay) of a loaded chara card; None for a field the card lacks."""
    def _int(name: str) -> int | None:
        try:
            value = kc["Parameter"][name]
        except (KeyError, IndexError, TypeError):
            return None
        return value if _is_int(value) else None
    return _int("birthMonth"), _int("birthDay")


def chara_info(kc: KoikatuCharaData) -> CharaInfo:
    """The identity fields of a loaded chara card. Raises if the card lacks a name or
    personality field, which the tasks report as an unreadable card."""
    param = kc["Parameter"]
    return CharaInfo(
        last=str(param["lastname"] or ""),
        first=str(param["firstname"] or ""),
        nick=str(param["nickname"] or ""),
        personality=int(param["personality"]),
        hair=hair_color(kc),
        birth=_birthday(kc),
    )


def key_of(info: CharaInfo) -> str:
    """Stable, deterministic "lastname | firstname | nickname | personality | hair_color" key.

    Used both when exporting cards to an LLM prompt and when matching the
    LLM's JSON response back to the file(s) it describes — the two sides
    must build byte-for-byte the same key for a given card, which is the
    whole reason this lives in one place instead of two.
    """
    idx         = info.personality
    personality = PERSONALITIES[idx] if idx < len(PERSONALITIES) else str(idx)
    color       = hair_color_name(info.hair)
    return (f"{_name_part(info.last)} | {_name_part(info.first)} | {_name_part(info.nick)}"
            f" | {personality} | {color} hair")
