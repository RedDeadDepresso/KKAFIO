"""find_all_files only reports supported content/archive files."""

import zipfile
from pathlib import Path

from kkafio.core.file_manager import FileManager


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _mod_zip(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.xml", "<manifest><guid>g</guid></manifest>")
    return path


def test_find_all_files_restricted_to_supported_extensions(tmp_path):
    _touch(tmp_path / "card.png")
    _touch(tmp_path / "sub" / "UPPER.PNG")
    _touch(tmp_path / "mod.zipmod")
    _mod_zip(tmp_path / "plainmod.zip")
    _touch(tmp_path / "a.rar")
    _touch(tmp_path / "b.7z")
    _touch(tmp_path / "c.zip", b"not really a zip")
    for ignored in ("notes.txt", "pic.jpg", "lib.dll", "readme", "x.png.bak"):
        _touch(tmp_path / ignored)

    files, archives = FileManager(config=None).find_all_files(tmp_path)

    assert sorted((p.name, ext) for p, _, ext in files) == [
        ("UPPER.PNG", ".png"), ("card.png", ".png"),
        ("mod.zipmod", ".zipmod"), ("plainmod.zip", ".zip"),
    ]
    assert sorted((p.name, ext) for p, _, ext in archives) == [
        ("a.rar", ".rar"), ("b.7z", ".7z"), ("c.zip", ".zip"),
    ]
