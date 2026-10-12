"""
build_mal_index.py — Turn the two Kaggle MyAnimeList CSVs into the compact
index KKAFIO uses to suggest series / names to the LLM.

  characters: mal_characters_detailed.csv  (character_id, full_name, alternate_name, name, ...)
  anime:      mal_anime.csv                (myanimelist_id, title, Members, characters, ...)

The character -> anime link is NOT in the characters CSV: it lives in the anime CSV's
`characters` column (a JSON list of {"id": <character_id>, "name": ..., ...}), so the
build inverts it. The characters CSV has no favorites column, so a character's
popularity score is the Members count of the most popular anime it appears in.

Column names are looked up by ALIASES below, the detected mapping is printed, and any
field can be forced with --map FIELD=COLUMN (use --show-columns to see the headers).

Output (assets/data/kkafio_mal_index.json, version 2):
  {
    "version": 2,
    "series":  ["Fate/stay night", ...],                 # franchise titles
    "aliases": {"<norm title>": <series idx>, ...},      # every anime title -> franchise
    "chars":   [["Tohsaka, Rin", [series idx, ...], score], ...],
    "names":   {"<norm name form>": [<chars idx>, ...]}  # romaji both orders, full name, kanji
  }

Usage:
  python tools/build_mal_index.py --characters chars.csv --anime anime.csv --show-columns
  python tools/build_mal_index.py --characters chars.csv --anime anime.csv
  python tools/build_mal_index.py ... --min-score 50000 --min-members 5000
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

# Make `import kkafio` work when run as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kkafio.cards.mal_text import norm, name_parts  # noqa: E402

csv.field_size_limit(10_000_000)  # description columns can be huge

OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "data" / "kkafio_mal_index.json"

# field -> candidate header names, compared case-insensitively
ALIASES: dict[str, list[str]] = {
    # characters CSV
    "char_id":        ["character_id", "id", "mal_id"],
    "char_name":      ["name", "character_name"],                    # MAL form: "Tohsaka, Rin"
    "char_full":      ["full_name", "name_en"],                      # "Rin Tohsaka"
    "char_kanji":     ["alternate_name", "name_kanji", "kanji", "japanese_name"],
    # anime CSV
    "anime_id":       ["myanimelist_id", "anime_id", "mal_id", "id"],
    "anime_title":    ["title", "anime_title", "name"],
    "anime_title2":   ["title_english", "title_en", "english_title"],   # optional
    "anime_synonyms": ["synonyms", "title_synonyms", "other_titles"],   # optional
    "anime_members":  ["members", "num_members"],
    "anime_chars":    ["characters", "character_list"],
}

# Season / part / format markers stripped to get a franchise title.
_SUFFIXES = re.compile(
    r"""(?ix)
    (?:\s*[:\-–]\s*)?
    (?: \(?\b\d+(?:st|nd|rd|th)\s+season\b\)?
      | \bseason\s+\d+\b | \bpart\s+\d+\b | \bfinal\s+season\b | \bthe\s+final\b
      | \b(?:movie|ova|oad|ona|special|specials|tv)\b(?:\s*\d+)?
      | \b(?:ii|iii|iv|v|vi)\b | \s\d$ )
    \s*$""")


def franchise_title(title: str) -> str:
    """Heuristic: collapse "X: Subtitle" / "X 2nd Season" / "X Movie" into "X".

    Deliberately conservative: only splits on ': ' (colon + space) so titles like
    "Re:Zero" survive, and never returns something shorter than 4 characters.
    """
    t = title.strip()
    if ": " in t:
        head = t.split(": ", 1)[0].strip()
        if len(head) >= 4:
            t = head
    prev = None
    while prev != t:                       # strip stacked markers ("X 2nd Season Part 2")
        prev = t
        stripped = _SUFFIXES.sub("", t).strip()
        if len(stripped) >= 4:
            t = stripped
    return t


# ---------------------------------------------------------------------------

def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _detect(headers: list[str], fields: list[str], forced: dict[str, str]) -> dict[str, str | None]:
    lower = {h.lower().strip(): h for h in headers}
    out: dict[str, str | None] = {}
    for field in fields:
        if field in forced:
            if forced[field] not in headers:
                sys.exit(f"--map {field}={forced[field]}: no such column. Columns: {headers}")
            out[field] = forced[field]
            continue
        out[field] = next((lower[a] for a in ALIASES[field] if a in lower), None)
    return out


def _num(s: str | None) -> int:
    try:
        return int(float(str(s).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _split_list(s: str | None) -> list[str]:
    """'[\'A\', \'B\']' / 'A|B' / 'A;B' -> list of items (used for synonyms)."""
    s = (s or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(s)]
        except Exception:
            try:
                return [str(x).strip() for x in json.loads(s.replace("'", '"'))]
            except Exception:
                s = s.strip("[]")
    return [p.strip().strip("'\"") for p in re.split(r"[|;]", s) if p.strip()]


_ID_RE = re.compile(r'"id"\s*:\s*(\d+)')


def _character_ids(cell: str | None) -> list[str]:
    """The character ids in an anime row's `characters` cell."""
    cell = (cell or "").strip()
    if not cell:
        return []
    try:
        data = json.loads(cell)
        ids = [str(c["id"]) for c in data if isinstance(c, dict) and "id" in c]
        if ids or data == []:
            return ids
    except Exception:
        pass
    return _ID_RE.findall(cell)          # malformed JSON: still salvage the ids


def build(chars_csv: Path, anime_csv: Path, min_score: int, min_members: int,
          forced: dict[str, str], show_only: bool = False) -> dict | None:
    c_head, c_rows = _read(chars_csv)
    a_head, a_rows = _read(anime_csv)

    if show_only:
        for label, head, rows in (("characters", c_head, c_rows), ("anime", a_head, a_rows)):
            print(f"\n[{label}] {len(rows)} rows\n  columns: {head}")
            if rows:
                print("  first row:", {k: (v[:60] if isinstance(v, str) else v) for k, v in rows[0].items()})
        return None

    cm = _detect(c_head, ["char_id", "char_name", "char_full", "char_kanji"], forced)
    am = _detect(a_head, ["anime_id", "anime_title", "anime_title2", "anime_synonyms",
                          "anime_members", "anime_chars"], forced)
    print("characters CSV mapping:", cm)
    print("anime CSV mapping:     ", am)
    missing = [f for f, c in {**cm, **am}.items()
               if c is None and f in ("char_id", "char_name", "anime_title", "anime_chars")]
    if missing:
        sys.exit(f"Could not detect: {missing}. Run with --show-columns and pass --map FIELD=COLUMN.")

    # --- anime -> franchise, aliases, and the inverted character -> anime link ---------
    series: list[str] = []
    series_idx: dict[str, int] = {}

    def sidx(title: str) -> int:
        k = norm(title)
        if k not in series_idx:
            series_idx[k] = len(series)
            series.append(title)
        return series_idx[k]

    members_of: dict[int, int] = {}          # series idx -> best Members seen
    aliases: dict[str, int] = {}
    char_series: dict[str, dict[int, int]] = {}   # char id -> {series idx: members}

    for row in a_rows:
        members = _num(row.get(am["anime_members"])) if am["anime_members"] else 0
        if am["anime_members"] and members < min_members:
            continue
        titles = [row.get(am["anime_title"]) or ""]
        if am["anime_title2"]:
            titles.append(row.get(am["anime_title2"]) or "")
        titles = [t.strip() for t in titles if t and t.strip()]
        if not titles:
            continue
        syn = _split_list(row.get(am["anime_synonyms"])) if am["anime_synonyms"] else []
        i = sidx(franchise_title(titles[0]))
        members_of[i] = max(members_of.get(i, 0), members)
        for t in titles + syn:
            aliases.setdefault(norm(t), i)
            aliases.setdefault(norm(franchise_title(t)), i)
        for cid in _character_ids(row.get(am["anime_chars"])):
            seen = char_series.setdefault(cid, {})
            seen[i] = max(seen.get(i, 0), members)
    for i, s_ in enumerate(series):
        aliases.setdefault(norm(s_), i)

    # --- characters ---------------------------------------------------------------------
    chars: list[list] = []
    names: dict[str, list[int]] = {}
    for row in c_rows:
        cid = str(row.get(cm["char_id"]) or "").strip()
        appearances = char_series.get(cid)
        if not appearances:
            continue                          # not in any kept anime
        score = max(appearances.values())
        if score < min_score:
            continue
        raw = (row.get(cm["char_name"]) or "").strip()
        if not raw:
            continue
        ordered = sorted(appearances, key=lambda i: -appearances[i])[:3]
        ci = len(chars)
        chars.append([raw, ordered, score])

        last, first = name_parts(raw)
        forms = {norm(raw), norm(last + first), norm(first + last)}
        if cm["char_full"]:
            forms.add(norm(row.get(cm["char_full"]) or ""))
        if cm["char_kanji"]:
            forms.add(norm(row.get(cm["char_kanji"]) or ""))
        forms.discard("")
        for f in forms:
            names.setdefault(f, []).append(ci)

    print(f"series: {len(series)}  aliases: {len(aliases)}  characters kept: {len(chars)}"
          f"  name keys: {len(names)}")
    return {"version": 2, "series": series, "aliases": aliases, "chars": chars, "names": names}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--characters", type=Path, required=True)
    ap.add_argument("--anime", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=OUTPUT)
    ap.add_argument("--min-score", type=int, default=10000,
                    help="drop characters whose most popular anime has fewer MAL members than this "
                         "(the characters CSV has no favorites, so this is the popularity proxy; "
                         "raise it to shrink the file)")
    ap.add_argument("--min-members", type=int, default=2000,
                    help="drop anime with fewer MAL members before anything else")
    ap.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN",
                    help="force a column, e.g. --map anime_members=Members")
    ap.add_argument("--show-columns", action="store_true", help="print headers + first row and exit")
    args = ap.parse_args()

    forced = dict(m.split("=", 1) for m in args.map)
    index = build(args.characters, args.anime, args.min_score, args.min_members,
                  forced, show_only=args.show_columns)
    if index is None:
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.output} ({args.output.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
