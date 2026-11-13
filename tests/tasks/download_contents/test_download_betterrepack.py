"""Downloading one modpack zipmod from its list of URLs (kkafio/services/mod_sources/betterrepack.py),
against a fake server (httpx.MockTransport)."""

import asyncio
import io
import zipfile
from urllib.parse import quote, unquote

import httpx

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
from kkafio.services.mod_sources.betterrepack import download_betterrepack

BASE = "https://sideload.betterrepack.com/download/KKEC/"


def url(rel: str) -> str:
    return BASE + quote(rel)


def zipmod_bytes(guid: str | None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.xml" if guid else "readme.txt", f"<manifest><guid>{guid}</guid></manifest>")
    return buf.getvalue()


def download(tmp_path, urls, served, guid="com.mod"):
    """Run the download against `served`: {relative path: bytes | status code}. Returns
    (result, guid_str_map, [relative paths requested])."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        rel = unquote(request.url.path[len("/download/KKEC/"):])
        requested.append(rel)
        reply = served.get(rel, 404)
        return httpx.Response(reply) if isinstance(reply, int) else httpx.Response(200, content=reply)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await download_betterrepack(client, guid, urls, tmp_path, mapping)

    mapping: dict[str, str] = {}
    return asyncio.run(go()), mapping, requested


def test_downloads_into_the_path_of_the_url(tmp_path):
    rel = "Sideloader Modpack/Alvarna#0602/[a] Mod v1.0.zipmod"
    result, mapping, _ = download(tmp_path, [url(rel)], {rel: zipmod_bytes("com.mod")})
    assert result is True
    assert (tmp_path / rel).is_file() and mapping["com.mod"] == str(tmp_path / rel)
    assert not list(tmp_path.rglob("*.part"))


def test_a_missing_file_falls_back_to_the_next_url(tmp_path):
    first, second = "Sideloader Modpack/old.zipmod", "Sideloader Modpack - Maps/new.zipmod"
    result, mapping, requested = download(tmp_path, [url(first), url(second)], {second: zipmod_bytes("com.mod")})
    assert result is True and requested == [first, second]
    assert mapping["com.mod"] == str(tmp_path / second) and not (tmp_path / first).exists()


def test_a_download_of_another_mod_or_not_a_zipmod_is_rejected_and_the_next_url_tried(tmp_path):
    wrong, junk, good = "A/wrong.zipmod", "A/junk.zipmod", "A/good.zipmod"
    served = {wrong: zipmod_bytes("com.other"), junk: b"<html>not a zip</html>", good: zipmod_bytes("com.mod")}
    result, mapping, requested = download(tmp_path, [url(wrong), url(junk), url(good)], served)
    assert result is True and requested == [wrong, junk, good]
    assert not (tmp_path / wrong).exists() and not (tmp_path / junk).exists()
    assert not list(tmp_path.rglob("*.part"))


def test_a_server_error_gives_up_without_trying_the_other_urls(tmp_path):
    result, mapping, requested = download(
        tmp_path, [url("A/one.zipmod"), url("A/two.zipmod")], {"A/one.zipmod": 503, "A/two.zipmod": zipmod_bytes("com.mod")})
    assert result is False and requested == ["A/one.zipmod"] and mapping == {}


def test_all_urls_failing_is_a_failure(tmp_path):
    result, _, requested = download(tmp_path, [url("A/one.zipmod"), url("A/two.zipmod")], {})
    assert result is False and requested == ["A/one.zipmod", "A/two.zipmod"]


def test_an_intact_existing_file_is_skipped_and_a_corrupt_one_is_downloaded_again(tmp_path):
    rel = "A/x.zipmod"
    (tmp_path / "A").mkdir()
    (tmp_path / rel).write_bytes(zipmod_bytes("com.mod"))
    result, _, requested = download(tmp_path, [url(rel)], {})
    assert result == "skipped" and requested == []

    (tmp_path / rel).write_bytes(b"truncated")
    result, _, requested = download(tmp_path, [url(rel)], {rel: zipmod_bytes("com.mod")})
    assert result is True and requested == [rel]


def test_urls_that_could_leave_the_mods_folder_or_the_server_are_ignored(tmp_path):
    bad = [BASE + "A/../../escape.zipmod", "https://evil.example/download/KKEC/A/x.zipmod"]
    result, _, requested = download(tmp_path, bad, {})
    assert result is False and requested == []
    assert not list(tmp_path.parent.glob("escape.zipmod"))
