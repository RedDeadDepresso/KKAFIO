"""Download progress reporting (core/progress.py) and the progress window's no-op path."""

import asyncio

import conftest  # noqa: F401  (sandbox env must be set before kkafio is imported)
import httpx

from kkafio.core import progress


def test_calls_do_nothing_without_a_window():
    handle = progress.begin("x.zipmod", 10)
    handle.update(5)
    handle.close(ok=True)
    progress.count("failed")
    progress.item_done()
    progress.set_current("x")                       # none of these may raise


def test_tracker_counts_rows_and_bytes():
    tracker = progress.ProgressTracker(items_total=3)
    handle = tracker.begin("a.zipmod", 100)
    handle.update(40)
    handle.update(100)
    snap = tracker.snapshot()
    assert [(r.name, r.received, r.total) for r in snap.rows] == [("a.zipmod", 100, 100)]
    assert snap.total_bytes == 100

    handle.close()                                  # drops the row, counts nothing
    handle.close(ok=True)                           # idempotent: the first close wins
    snap = tracker.snapshot()
    assert snap.rows == [] and snap.ok == 0

    tracker.begin("b", None).close(ok=True)
    tracker.count("skipped")
    tracker.count("failed")
    tracker.item_done()
    snap = tracker.snapshot()
    assert (snap.ok, snap.skipped, snap.failed, snap.items_done, snap.items_total) == (1, 1, 1, 1, 3)


def test_cancel_runs_the_callback_once():
    calls = []
    tracker = progress.ProgressTracker(on_cancel=lambda: calls.append(1))
    tracker.request_cancel()
    tracker.request_cancel()
    assert calls == [1] and tracker.snapshot().cancelled


def test_download_file_reports_a_row_and_the_outcome(tmp_path):
    from kkafio.services.content_sources import transfer

    body = b"x" * 200_000

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bad":
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-length": str(len(body))})

    async def run():
        tracker = progress.ProgressTracker()
        progress.install(tracker)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                _, err, skipped = await transfer.download_file(
                    client, "http://h/ok.png", tmp_path, "ok.png", {}, False)
                assert err is None and not skipped
                _, err, _ = await transfer.download_file(
                    client, "http://h/bad", tmp_path, "bad.png", {}, False)
                assert err is not None
                _, _, skipped = await transfer.download_file(
                    client, "http://h/ok.png", tmp_path, "ok.png", {"http://h/ok.png": str(tmp_path / "ok.png")}, True)
                assert skipped
        finally:
            progress.uninstall()
        return tracker.snapshot()

    snap = asyncio.run(run())
    assert (snap.ok, snap.failed, snap.skipped) == (1, 1, 1)
    assert snap.total_bytes == len(body) and snap.rows == []


def test_progress_window_is_a_noop_when_disabled():
    from kkafio.dialogs.progress_dialog import download_progress

    async def run():
        async with download_progress("T", "mods", 1):
            progress.item_done()            # no window and no tracker: still harmless
        return "done"

    assert asyncio.run(run()) == "done"     # PYTEST_CURRENT_TEST disables the window
