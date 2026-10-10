"""
Characterization of pure helpers that live inside task modules (or are about to
leave them): the hand-rolled MessagePack codec and the Telegram link parsers.
Expected values are recorded in golden/pure_functions.json by generate_goldens.py.

Like test_card_code.py, written against the old layout; only the import block changes.
"""

import json
from pathlib import Path

import msgpack
import pytest

# ---- where the code lives -----------------------------------------------------------
from kkafio.cards import msgpack_min
from kkafio.services import telegram_links
# -------------------------------------------------------------------------------------

GOLDEN = json.loads((Path(__file__).parent / "golden" / "pure_functions.json").read_text(encoding="utf-8"))


def _corpus() -> dict[str, bytes]:
    from pure_corpus import msgpack_corpus
    return msgpack_corpus()


def roundtrip(data: bytes) -> bytes:
    value, pos = msgpack_min._mp_decode(data, 0)
    assert pos == len(data)
    out = bytearray()
    msgpack_min._mp_encode(value, out)
    return bytes(out)


@pytest.mark.parametrize("name", sorted(GOLDEN["msgpack_roundtrip"]))
def test_codec_roundtrips_byte_for_byte(name):
    data = _corpus()[name]
    assert roundtrip(data).hex() == GOLDEN["msgpack_roundtrip"][name]
    assert roundtrip(data) == data, "the codec exists to re-encode verbatim"


def test_codec_decoded_values_match_the_reference_library():
    for name, data in _corpus().items():
        value, _ = msgpack_min._mp_decode(data, 0)
        assert _plain(value) == msgpack.unpackb(data, raw=False, strict_map_key=False), name


def _plain(v):
    """Strip the codec's wire-code wrappers down to ordinary Python values."""
    if isinstance(v, msgpack_min._MpInt):
        return v.value
    if isinstance(v, msgpack_min._MpF32):
        import struct
        return struct.unpack(">f", v.raw)[0]
    if isinstance(v, msgpack_min._MpBin):
        return bytes(v.data)
    if isinstance(v, msgpack_min._MpExt):
        return msgpack.ExtType(v.code, bytes(v.data))
    if isinstance(v, msgpack_min._MpMap):
        return {_plain(k): _plain(x) for k, x in v.items}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


@pytest.mark.parametrize("link", sorted(GOLDEN["tme_links"]))
def testparse_tme_link(link):
    assert _jsonable(telegram_links.parse_tme_link(link)) == GOLDEN["tme_links"][link]


@pytest.mark.parametrize("link", sorted(GOLDEN["chat_link_for_search"]))
def test_parse_chat_link_for_search(link):
    assert _jsonable(telegram_links._parse_chat_link_for_search(link)) == GOLDEN["chat_link_for_search"][link]


@pytest.mark.parametrize("raw", sorted(GOLDEN["chat_links"]))
def testparse_chat_links(raw):
    assert _jsonable(telegram_links.parse_chat_links(raw)) == GOLDEN["chat_links"][raw]


def test_default_chat_links_and_source_labels():
    assert _jsonable(telegram_links.parse_chat_links(telegram_links.DEFAULT_TELEGRAM_CHAT_LINKS)) == GOLDEN["default_chat_links"]
    from pure_corpus import SOURCE_SELECTIONS
    assert set(GOLDEN["source_labels"]) == set(SOURCE_SELECTIONS)
    for name, selection in SOURCE_SELECTIONS.items():
        assert telegram_links.telegram_sources_label(selection) == GOLDEN["source_labels"][name]


def _jsonable(x):
    return json.loads(json.dumps(x))
