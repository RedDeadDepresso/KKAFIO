"""transfer.download_file: an interrupted download is continued with a Range request."""

import asyncio
from pathlib import Path

import httpx
import pytest

from kkafio.services.content_sources.shared import transfer
CONTENT = bytes((i * 7) % 251 for i in range(50000))
URL = "https://files.example.com/dl/card.png"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    async def no_sleep(_t):
        pass
    monkeypatch.setattr(transfer.asyncio, "sleep", no_sleep)


class _Dropping(httpx.AsyncByteStream):
    """A body that sends `sent` bytes, then the connection breaks."""

    def __init__(self, data: bytes, sent: int):
        self.data, self.sent = data, sent

    async def __aiter__(self):
        yield self.data[:self.sent]
        raise httpx.ReadError("connection reset")


def _server(honour_range=True, drops=(), status=None):
    """Serves CONTENT. `drops`: how many bytes the 1st, 2nd... response sends before breaking."""
    seen: list[str | None] = []
    drops = list(drops)

    def handler(req: httpx.Request) -> httpx.Response:
        rng = req.headers.get("range")
        seen.append(rng)
        if status:
            return httpx.Response(status)
        start = int(rng.removeprefix("bytes=").rstrip("-")) if rng and honour_range else 0
        if start >= len(CONTENT):
            return httpx.Response(416, headers={"content-range": f"bytes */{len(CONTENT)}"})
        body = CONTENT[start:]
        headers = {"content-length": str(len(body))}
        code = 200
        if rng and honour_range:
            code, headers["content-range"] = 206, f"bytes {start}-{len(CONTENT) - 1}/{len(CONTENT)}"
        if drops:
            sent = drops.pop(0)
            return httpx.Response(code, headers=headers, stream=_Dropping(body, sent))
        return httpx.Response(code, headers=headers, content=body)

    return handler, seen


def _run(handler, tmp_path, url=URL, **kwargs):
    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        try:
            history: dict = {}
            result = await transfer.download_file(client, url, Path(tmp_path), "card.png", history, False, **kwargs)
            return result, history
        finally:
            await client.aclose()
    return asyncio.run(go())


def _part(tmp_path, url=URL, **kwargs):
    return transfer.part_path(Path(tmp_path), kwargs.get("filename") or "card.png", kwargs.get("resume_key") or url)


def test_a_kept_part_is_continued_with_a_range_request(tmp_path):
    _part(tmp_path).write_bytes(CONTENT[:12345])
    handler, seen = _server()
    (url, err, skipped), history = _run(handler, tmp_path)
    assert err is None and not skipped
    assert seen == ["bytes=12345-"]
    assert (tmp_path / "card.png").read_bytes() == CONTENT
    assert not list(tmp_path.glob("*.part")) and history[URL].endswith("card.png")


def test_a_server_that_ignores_range_sends_the_whole_file(tmp_path):
    _part(tmp_path).write_bytes(CONTENT[:12345])
    handler, seen = _server(honour_range=False)
    (url, err, _), _ = _run(handler, tmp_path)
    assert err is None
    assert (tmp_path / "card.png").read_bytes() == CONTENT        # the body replaced the part
    assert not list(tmp_path.glob("*.part"))


def test_a_part_that_is_not_a_prefix_is_dropped_and_the_file_fetched_again(tmp_path):
    _part(tmp_path).write_bytes(b"x" * (len(CONTENT) + 10))       # longer than the file: 416
    handler, seen = _server()
    (url, err, _), _ = _run(handler, tmp_path)
    assert err is None and (tmp_path / "card.png").read_bytes() == CONTENT
    assert seen == [f"bytes={len(CONTENT) + 10}-", None]


def test_a_part_that_already_is_the_whole_file_is_just_renamed(tmp_path):
    _part(tmp_path).write_bytes(CONTENT)
    handler, seen = _server()
    (url, err, _), _ = _run(handler, tmp_path)
    assert err is None and (tmp_path / "card.png").read_bytes() == CONTENT
    assert seen == [f"bytes={len(CONTENT)}-"]


def test_a_dropped_connection_resumes_where_it_stopped(tmp_path):
    handler, seen = _server(drops=[20000])
    (url, err, _), _ = _run(handler, tmp_path)
    assert err is None and (tmp_path / "card.png").read_bytes() == CONTENT
    assert seen == [None, "bytes=20000-"]


def test_progress_does_not_use_up_a_retry(tmp_path, monkeypatch):
    # three drops, each after gaining bytes, with a budget of ONE try: it still finishes
    monkeypatch.setattr(transfer, "RETRY_ATTEMPTS", 1)
    handler, seen = _server(drops=[10000, 10000, 10000])
    (url, err, _), _ = _run(handler, tmp_path)
    assert err is None and (tmp_path / "card.png").read_bytes() == CONTENT
    assert seen == [None, "bytes=10000-", "bytes=20000-", "bytes=30000-"]


def test_a_failure_without_progress_still_gives_up_and_keeps_the_part(tmp_path):
    _part(tmp_path).write_bytes(CONTENT[:100])
    handler, seen = _server(status=503)
    (url, err, _), _ = _run(handler, tmp_path)
    assert err is not None and len(seen) == transfer.RETRY_ATTEMPTS
    assert _part(tmp_path).read_bytes() == CONTENT[:100]          # kept for the next run


def test_a_missing_file_removes_its_part(tmp_path):
    _part(tmp_path).write_bytes(CONTENT[:100])
    handler, seen = _server(status=404)
    (url, err, _), _ = _run(handler, tmp_path)
    assert err is not None and not list(tmp_path.glob("*.part"))


def test_parts_are_tied_to_the_file_not_just_its_name(tmp_path):
    a = transfer.part_path(tmp_path, "x.zip", "share-a/1")
    b = transfer.part_path(tmp_path, "x.zip", "share-b/1")
    assert a != b and a.name.startswith("x.zip.") and a.name.endswith(".part")
    # a stale part of another file is never continued
    a.write_bytes(b"OTHER FILE")
    handler, seen = _server()
    (url, err, _), _ = _run(handler, tmp_path, filename="x.zip", resume_key="share-b/1")
    assert err is None and (tmp_path / "x.zip").read_bytes() == CONTENT and seen == [None]
    assert a.exists()