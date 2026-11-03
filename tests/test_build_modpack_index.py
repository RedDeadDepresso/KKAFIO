"""tools/build_modpack_index.py against a local fake of the BetterRepack server:
Apache/Apaxy listings, Range requests, incremental rebuilds, URL ordering."""

import importlib.util
import io
import json
import re
import threading
import zipfile
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "build_modpack_index", Path(__file__).resolve().parent.parent / "tools" / "build_modpack_index.py")
builder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(builder)

PREFIX = "/download/KKEC/"
MAIN = "Sideloader Modpack"
KK_ONLY = "Sideloader Modpack - Exclusive KK"
KKS_ONLY = "Sideloader Modpack - Exclusive KKS"
BLEEDING = "Sideloader Modpack - Bleeding Edge"


def make_zipmod(guid: str | None, version: str = "1.0", *, big_first: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if big_first:        # manifest sits far from the start of the file
            zf.writestr(zipfile.ZipInfo("abdata/big.bin"), b"\0" * 200_000, compress_type=zipfile.ZIP_STORED)
        if guid is not None:
            zf.writestr("manifest.xml",
                        f"<manifest><guid>{guid}</guid><version>{version}</version><name>n</name></manifest>")
        else:
            zf.writestr("readme.txt", "no manifest here")
    return buf.getvalue()


class FakeServer:
    """{relative path: (bytes, mtime)} served as Apaxy listings plus Range-capable downloads."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}
        self.range_hits: list[str] = []
        self.listing_hits: list[str] = []
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}{PREFIX}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, rel: str, data: bytes, mtime: str = "2023-02-03 18:39") -> None:
        self.files[rel] = (data, mtime)

    def remove(self, rel: str) -> None:
        del self.files[rel]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- listing ----------------------------------------------------------
    def _listing(self, directory: str) -> str:
        names: dict[str, tuple[bool, tuple[bytes, str] | None]] = {}
        for rel, entry in self.files.items():
            if not rel.startswith(directory):
                continue
            rest = rel[len(directory):]
            if "/" in rest:
                names[rest.split("/", 1)[0] + "/"] = (True, None)
            else:
                names[rest] = (False, entry)
        rows = ['<table id="indexlist"><tbody><tr class="indexhead"><th>Name</th></tr>',
                f'<tr class="even parent"><td class="indexcolicon"><a href="{PREFIX}"><img></a></td>'
                f'<td class="indexcolname"><a href="{PREFIX}">Parent Directory</a></td>'
                '<td class="indexcollastmod">&nbsp;</td><td class="indexcolsize">  - </td></tr>']
        for name, (is_dir, entry) in sorted(names.items()):
            href = quote(name.rstrip("/"), safe="") + ("/" if is_dir else "")
            mtime, size = ("2023-01-01 00:00", "  - ") if is_dir else (entry[1] + "  ", f"{len(entry[0]) / 1024:.1f}K")
            rows.append(
                f'<tr class="odd"><td class="indexcolicon"><a href="{href}"><img alt="[   ]"></a></td>'
                f'<td class="indexcolname"><a href="{href}">{escape(name)}</a></td>'
                f'<td class="indexcollastmod">{mtime}</td><td class="indexcolsize">{size}</td></tr>')
        return "".join(rows) + "</tbody></table>"

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = unquote(self.path.split("?")[0])
                assert path.startswith(PREFIX)
                rel = path[len(PREFIX):]
                if rel == "" or rel.endswith("/"):
                    server.listing_hits.append(rel)
                    body = server._listing(rel).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if rel not in server.files:
                    self.send_error(404)
                    return
                data = server.files[rel][0]
                header = self.headers.get("Range", "")
                m = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
                if not m:
                    self.send_error(416)
                    return
                server.range_hits.append(rel)
                first, last = m.groups()
                if first == "":
                    start, end = max(0, len(data) - int(last)), len(data) - 1
                else:
                    start, end = int(first), min(int(last or len(data) - 1), len(data) - 1)
                body = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def run(server, tmp_path, *extra, games=("kk", "kks")):
    out = {}
    cache = tmp_path / "cache.json"
    for game in games:
        path = tmp_path / f"{game}.json"
        code = builder.main(["--base-url", server.base, "--cache", str(cache), "--game-type", game,
                             "--output", str(path), *extra])
        assert code == 0
        out[game] = json.loads(path.read_text(encoding="utf-8"))["guids"]
    return out


def rel_of(url: str) -> str:
    return unquote(url[url.index(PREFIX) + len(PREFIX):])


def test_urls_are_encoded_listed_best_first_and_games_are_separate(server, tmp_path):
    server.add(f"{MAIN}/a/[x] Mod v1.0.zipmod", make_zipmod("com.mod", "1.0"))
    server.add(f"{MAIN}/a/[x] Mod v1.2.zipmod", make_zipmod("com.mod", "1.10"))      # newer than 1.9 numerically
    server.add(f"{MAIN}/a/[x] Mod v1.1.zipmod", make_zipmod("com.mod", "1.9"))
    server.add(f"{KK_ONLY}/[x] Mod v1.0.zipmod", make_zipmod("com.mod", "9.0"), mtime="2024-01-01 00:00")   # ranks after main
    server.add(f"{MAIN}/hash/Alvarna#0602/y.zipmod", make_zipmod("com.hash", big_first=True))
    server.add(f"{KK_ONLY}/only kk.zipmod", make_zipmod("com.kk"))
    server.add(f"{KKS_ONLY}/only kks.zipmod", make_zipmod("com.kks"))
    server.add(f"{BLEEDING}/CCCCP/!unsorted/be.zipmod", make_zipmod("com.be"))

    out = run(server, tmp_path)

    assert set(out["kk"]) == {"com.mod", "com.hash", "com.kk"}
    assert set(out["kks"]) == {"com.mod", "com.hash", "com.kks"}
    assert all(BLEEDING not in h for h in server.range_hits + server.listing_hits)   # never even crawled

    assert [rel_of(u) for u in out["kk"]["com.mod"]] == [
        f"{MAIN}/a/[x] Mod v1.2.zipmod",         # main folder first, highest version first
        f"{MAIN}/a/[x] Mod v1.1.zipmod",
        f"{MAIN}/a/[x] Mod v1.0.zipmod",
        f"{KK_ONLY}/[x] Mod v1.0.zipmod",
    ]
    assert len(out["kks"]["com.mod"]) == 3                                           # the KK-only copy isn't there
    (hash_url,) = out["kk"]["com.hash"]
    assert "%23" in hash_url and "#" not in hash_url
    assert rel_of(hash_url) == f"{MAIN}/hash/Alvarna#0602/y.zipmod"


def test_identical_copies_are_listed_once_and_urls_are_capped(server, tmp_path):
    data = make_zipmod("com.dup")
    server.add(f"{MAIN}/same.zipmod", data)
    server.add(f"{KK_ONLY}/same.zipmod", data)                                       # identical copy elsewhere
    for i in range(6):
        server.add(f"{MAIN}/v{i}/many{i}.zipmod", make_zipmod("com.many", f"1.{i}"))
    out = run(server, tmp_path, "--max-urls", "3", games=("kk",))
    assert [rel_of(u) for u in out["kk"]["com.dup"]] == [f"{MAIN}/same.zipmod"]
    assert [rel_of(u) for u in out["kk"]["com.many"]] == [
        f"{MAIN}/v5/many5.zipmod", f"{MAIN}/v4/many4.zipmod", f"{MAIN}/v3/many3.zipmod"]


def test_unindexable_files_are_recorded_and_not_probed_again(server, tmp_path):
    server.add(f"{MAIN}/ok.zipmod", make_zipmod("com.ok"))
    server.add(f"{MAIN}/nomanifest.zipmod", make_zipmod(None))
    server.add(f"{MAIN}/garbage.zipmod", b"not a zip at all" * 10)
    out = run(server, tmp_path, games=("kk",))
    assert set(out["kk"]) == {"com.ok"}
    server.range_hits.clear()
    run(server, tmp_path, games=("kk",))
    assert server.range_hits == []                                                    # nothing probed again
    run(server, tmp_path, "--retry-failed", games=("kk",))
    assert {h for h in server.range_hits} == {f"{MAIN}/nomanifest.zipmod", f"{MAIN}/garbage.zipmod"}


def test_incremental_rebuild_probes_only_new_changed_and_skips_moves(server, tmp_path):
    server.add(f"{MAIN}/a/one.zipmod", make_zipmod("com.one"))
    server.add(f"{MAIN}/a/two.zipmod", make_zipmod("com.two"), mtime="2022-01-01 10:00")
    server.add(f"{MAIN}/b/three.zipmod", make_zipmod("com.three"))
    run(server, tmp_path, games=("kk",))
    assert len(set(server.range_hits)) == 3

    # nothing changed -> nothing probed
    server.range_hits.clear()
    run(server, tmp_path, games=("kk",))
    assert server.range_hits == []

    # new file, changed file (new date), moved file (folder case changed), removed file
    server.add(f"{MAIN}/a/four.zipmod", make_zipmod("com.four"))
    server.files[f"{MAIN}/a/one.zipmod"] = (make_zipmod("com.one", "2.0"), "2024-05-05 05:05")
    data, mtime = server.files.pop(f"{MAIN}/a/two.zipmod")
    server.add(f"{MAIN}/A/two.zipmod", data, mtime)
    server.remove(f"{MAIN}/b/three.zipmod")
    server.range_hits.clear()
    out = run(server, tmp_path, games=("kk",))

    assert set(server.range_hits) == {f"{MAIN}/a/four.zipmod", f"{MAIN}/a/one.zipmod"}
    assert set(out["kk"]) == {"com.one", "com.two", "com.four"}                       # three is gone
    assert [rel_of(u) for u in out["kk"]["com.two"]] == [f"{MAIN}/A/two.zipmod"]      # the move is followed

    # --full ignores the cache
    server.range_hits.clear()
    run(server, tmp_path, "--full", games=("kk",))
    assert len(set(server.range_hits)) == 3


def test_a_game_index_only_holds_its_categories(server, tmp_path):
    server.add(f"{KK_ONLY}/k.zipmod", make_zipmod("com.k"))
    server.add(f"{KKS_ONLY}/s.zipmod", make_zipmod("com.s"))
    out = run(server, tmp_path)
    assert set(out["kk"]) == {"com.k"} and set(out["kks"]) == {"com.s"}
