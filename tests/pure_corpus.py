"""Inputs for the pure-function characterization tests and the golden generator."""

import msgpack


def msgpack_corpus() -> dict[str, bytes]:
    p = msgpack.packb
    values = {
        "nil": None, "true": True, "false": False,
        "uint_0": 0, "uint_127": 127, "uint_128": 128, "uint_255": 255, "uint_256": 256,
        "uint_65535": 65535, "uint_65536": 65536, "uint_2^32": 2**32, "uint_2^63": 2**63,
        "int_-1": -1, "int_-32": -32, "int_-33": -33, "int_-129": -129, "int_-32769": -32769,
        "int_-2^31-1": -2**31 - 1,
        "float64": 1.5, "float_neg": -0.25,
        "str_empty": "", "str_short": "abc", "str_31": "x" * 31, "str_32": "x" * 32,
        "str_255": "x" * 255, "str_256": "x" * 256, "str_unicode": "日本語テスト",
        "bin_empty": b"", "bin_short": b"\x00\x01\x02", "bin_300": bytes(range(256)) + b"abc" * 15,
        "array_empty": [], "array_small": [1, "a", None], "array_16": list(range(16)),
        "map_empty": {}, "map_small": {"a": 1, "b": [1, 2]}, "map_int_keys": {1: "x", 2: "y"},
        "nested": {"clothes": {"parts": [{"id": 1, "colorInfo": [{"a": 0.5}]}], "v": "0.0.1"}},
        "ext": msgpack.ExtType(5, b"abc"),
    }
    out = {k: p(v, use_bin_type=True) for k, v in values.items()}
    out["float32"] = p(1.5, use_single_float=True)
    out["array_65536"] = p(list(range(65536)))[:0] or p(list(range(300)))
    return out
