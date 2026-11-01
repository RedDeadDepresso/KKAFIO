"""
folder_names.py — Turning a name into a folder name that is safe to create.

Used wherever a name that isn't ours becomes a folder: the series an LLM or the user typed (Group
Characters), a character's filename (Group Coordinates) and a scene author (Group Scenes), in the
automatic tasks and in the Run Gallery tabs.
"""

# Windows reserved device names — illegal as a folder name with or without
# an extension (CON, CON.txt, com3, etc. all fail to create on Windows).
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{d}" for d in "123456789"),
    *(f"LPT{d}" for d in "123456789"),
}
_MAX_FOLDER_NAME_LEN = 100  # generous but keeps well under Windows' 260-char path limit


def safe_folder_name(name: str) -> str:
    """Turn a name (a series, a character's filename, a scene author) into a
    folder name that is safe to join onto an existing path with a plain `/`.

    The name may come from an LLM response, which is essentially
    untrusted input: it could echo something like ".." or a reserved
    device name, whether by accident (a garbled reply) or a maliciously
    crafted one (e.g. via a prompt-injected card / cache file). Handles:
      - path separators and other Windows-illegal characters
      - "." / ".." and any name that is only dots/spaces (path traversal —
        `Path(x) / ".."` would otherwise move files OUT of the input folder)
      - trailing dots/spaces (silently stripped by Windows, so left in place
        they'd make two different-looking names collide on disk)
      - reserved device names (CON, COM1, ...), with or without a suffix
      - an empty result after cleanup, and overly long names
    Returns "" if nothing safe survives — the caller treats that as "no
    assignment for this card" instead of moving it into folder_path itself
    or its parent.
    """
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "")
    # Strip ASCII control characters too (illegal on Windows, invisible/
    # confusing elsewhere).
    name = "".join(ch for ch in name if ord(ch) >= 0x20)
    name = name.strip()
    # Windows trims trailing dots and spaces off folder names, so do it here
    # rather than let two names that only differ in that regard collide.
    name = name.rstrip(". ")

    if not name or set(name) <= {"."}:
        return ""  # "", ".", "..", "...", etc.

    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        return ""

    return name[:_MAX_FOLDER_NAME_LEN]
