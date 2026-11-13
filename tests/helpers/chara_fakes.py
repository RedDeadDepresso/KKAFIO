"""Stand-ins that let the Group / Rename Characters tests run end to end without a real kkloader: a fake card
file is ``b"CHARA|<name>"``, ``FakeCard`` parses it (and counts how many were parsed), and ``fake_cards()``
patches both tasks to use it."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from kkafio.cards.chara.chara_key import CharaInfo
from kkafio.cards.classifier import CHARA_CARD_TYPES
from kkafio.tasks import group_chara, rename_chara

KK = CHARA_CARD_TYPES[0]


def write_file(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_info(last: str = "Rin", **fields) -> CharaInfo:
    values = dict(last=last, first="", nick="", personality=0, hair=None, birth=(None, None))
    return CharaInfo(**{**values, **fields})


class FakeCard:
    """Stands in for KoikatuCharaData. A card file is  b"CHARA|<name>"."""
    parsed = 0

    def __init__(self, path):
        FakeCard.parsed += 1
        self.name = Path(path).read_bytes().split(b"|", 1)[1].decode()
        self.parameter = {"lastname": "", "firstname": "", "nickname": ""}

    @classmethod
    def load(cls, path):
        return cls(path)

    def __getitem__(self, k):
        return self.parameter

    def display_name(self):
        last, first = self.parameter["lastname"], self.parameter["firstname"]
        return f"{last} {first}" if last or first else self.name

    def save(self, path):
        Path(path).write_bytes(b"CHARA|" + self.display_name().encode())


def _fake_info(card):
    return make_info(card.display_name())


def _fake_key(info):
    return f"{info.last} | calm | black hair"


def _fake_card_type(raw):
    return KK if raw.startswith(b"CHARA|") else None


@contextmanager
def fake_cards():
    FakeCard.parsed = 0
    with ExitStack() as st:
        for module in (group_chara, rename_chara):
            st.enter_context(mock.patch.object(module, "KoikatuCharaData", FakeCard))
            st.enter_context(mock.patch.object(module, "chara_info", _fake_info))
            st.enter_context(mock.patch.object(module, "key_of", _fake_key))
            st.enter_context(mock.patch.object(module, "get_card_type", _fake_card_type))
        yield


def write_cards(folder: Path, **names) -> dict[str, Path]:
    out = {}
    for stem, name in names.items():
        out[stem] = write_file(folder / f"{stem}.png", b"CHARA|" + name.encode())
    return out
