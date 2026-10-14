"""
build_mal_index.py — Turn the two Kaggle MyAnimeList CSVs into the compact
index KKAFIO uses to suggest series / names to the LLM.

  characters: mal_characters_detailed.csv  (character_id, full_name, alternate_name, name, ...)
  anime:      mal_anime.csv                (myanimelist_id, title, characters, ...)
  favorites:  a CSV of (character_id, favorites)

The character -> anime link is NOT in the characters CSV: it lives in the anime CSV's
`characters` column (a JSON list of {"id": <character_id>, "name": ..., ...}), so the
build inverts it. A character's popularity score is its `favorites` from the favorites
CSV (0 if it has no row there). A character's series are ordered by the total favorites
of the characters in them, so the most prominent series comes first.

Column names are looked up by ALIASES below, the detected mapping is printed, and any
field can be forced with --map FIELD=COLUMN (use --show-columns to see the headers).

Output (assets/data/kkafio_mal_index.json, version 1):
  {
    "version": 1,
    "langs":   ["en_us", "ja_jp"],                       # language of each position below
    "series":  [["Fate/stay night", "フェイト/ステイナイト"], ...],   # franchise titles per language
    "aliases": {"<norm title>": <series idx>, ...},      # every anime title -> franchise
    "chars":   [[["Tohsaka, Rin", "遠坂, 凛"], [series idx, ...], score], ...],
    "names":   {"<norm name form>": [<chars idx>, ...]}  # every language, both orders, full name
  }

Names are "Last, First". English is MAL's romaji. Japanese ("ja_jp") is only written when
the CSVs have the columns for it (anime `title_japanese`, or the id + `title_japanese` CSV
given as --japanese-titles; characters `alternate_name`). That CSV's `title_english`, where
not empty, is the English series title; otherwise the anime CSV's title is used; a
text missing in a language is "" and the lookup falls back to English.

Usage:
  python tools/build_mal_index.py --characters chars.csv --anime anime.csv --favorites fav.csv --show-columns
  python tools/build_mal_index.py --characters chars.csv --anime anime.csv --favorites fav.csv
  python tools/build_mal_index.py ... --japanese-titles japanese.csv   # mal_id + title_japanese (+ title_english)
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
    # favorites CSV (--favorites)
    "fav_id":         ["character_id", "id", "mal_id"],
    "fav_count":      ["favorites", "favourites", "num_favorites"],
    # Japanese titles CSV (--japanese-titles), joined to the anime CSV on the MAL id
    "ja_id":          ["mal_id", "myanimelist_id", "anime_id", "id"],
    "ja_title":       ["title_japanese", "title_ja", "japanese_title"],
    "ja_title_en":    ["title_english", "title_en", "english_title"],   # optional, may be empty
    # anime CSV
    "anime_id":       ["myanimelist_id", "anime_id", "mal_id", "id"],
    "anime_title":    ["title", "anime_title", "name"],
    "anime_title2":   ["title_english", "title_en", "english_title"],   # optional
    "anime_title_ja": ["title_japanese", "title_ja", "japanese_title"],   # optional (-> ja_jp)
    "anime_synonyms": ["synonyms", "title_synonyms", "other_titles"],   # optional
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


def japanese_name(raw: str) -> str:
    """MAL's kanji name ("遠坂 凛": family name first) -> "遠坂, 凛"; anything else as is."""
    parts = (raw or "").replace("\u3000", " ").split()
    return f"{parts[0]}, {parts[1]}" if len(parts) == 2 else " ".join(parts)


def franchise_title(title: str) -> str:
    """Heuristic: collapse "X: Subtitle" / "X 2nd Season" / "X Movie" into "X".

    Deliberately conservative: only splits on ': ' (colon + space) so titles like
    "Re:Zero" survive, and never returns something shorter than 4 characters.
    """
    t = title.strip()
    t = re.sub(r"\s*：\s*", ": ", t)             # full-width colon (Japanese titles)
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


def _id(value: str | None) -> str:
    """A MAL id as a comparable string ("52" == "52.0")."""
    v = (value or "").strip()
    return v[:-2] if v.endswith(".0") else v


def build(chars_csv: Path, anime_csv: Path, favorites_csv: Path,
          forced: dict[str, str], show_only: bool = False,
          japanese_csv: Path | None = None) -> dict | None:
    c_head, c_rows = _read(chars_csv)
    a_head, a_rows = _read(anime_csv)
    f_head, f_rows = _read(favorites_csv)
    j_head, j_rows = _read(japanese_csv) if japanese_csv else ([], [])

    if show_only:
        files = [("characters", c_head, c_rows), ("anime", a_head, a_rows),
                 ("favorites", f_head, f_rows)]
        if japanese_csv:
            files.append(("japanese titles", j_head, j_rows))
        for label, head, rows in files:
            print(f"\n[{label}] {len(rows)} rows\n  columns: {head}")
            if rows:
                print("  first row:", {k: (v[:60] if isinstance(v, str) else v) for k, v in rows[0].items()})
        return None

    cm = _detect(c_head, ["char_id", "char_name", "char_full", "char_kanji"], forced)
    am = _detect(a_head, ["anime_id", "anime_title", "anime_title2", "anime_title_ja",
                          "anime_synonyms", "anime_chars"], forced)
    fm = _detect(f_head, ["fav_id", "fav_count"], forced)
    print("characters CSV mapping:", cm)
    print("anime CSV mapping:     ", am)
    print("favorites CSV mapping: ", fm)
    missing = [f for f, c in {**cm, **am, **fm}.items()
               if c is None and f in ("char_id", "char_name", "anime_title", "anime_chars",
                                      "fav_id", "fav_count")]
    if missing:
        sys.exit(f"Could not detect: {missing}. Run with --show-columns and pass --map FIELD=COLUMN.")

    # Japanese titles from the extra CSV, by MAL id (they fill in where the anime CSV has none).
    ja_titles: dict[str, str] = {}
    en_titles: dict[str, str] = {}                # English titles, where the extra CSV has one
    if japanese_csv:
        jm = _detect(j_head, ["ja_id", "ja_title", "ja_title_en"], forced)
        print("japanese titles CSV mapping:", jm)
        if not (jm["ja_id"] and jm["ja_title"]):
            sys.exit("Could not detect the id / title_japanese columns of --japanese-titles. "
                     "Run with --show-columns and pass --map ja_id=COLUMN --map ja_title=COLUMN.")
        if not am["anime_id"]:
            sys.exit("--japanese-titles needs an id column in the anime CSV (--map anime_id=COLUMN).")
        for row in j_rows:
            mal_id = _id(row.get(jm["ja_id"]))
            t = (row.get(jm["ja_title"]) or "").strip()
            if t:
                ja_titles[mal_id] = t
            e = (row.get(jm["ja_title_en"]) or "").strip() if jm["ja_title_en"] else ""
            if e:
                en_titles[mal_id] = e
        print(f"japanese titles: {len(ja_titles)}  english titles: {len(en_titles)}")

    # Languages: English always; Japanese when the CSVs have the columns for it.
    langs = ["en_us"] + (["ja_jp"] if am["anime_title_ja"] or ja_titles or cm["char_kanji"] else [])
    print("languages:", langs)

    # --- anime -> franchise, aliases, and the inverted character -> anime link ---------
    series: list[list[str]] = []                  # per franchise: title in each of `langs`
    series_idx: dict[str, int] = {}

    def sidx(title: str, title_ja: str = "") -> int:
        k = norm(title)
        if k not in series_idx:
            series_idx[k] = len(series)
            series.append([title] + [""] * (len(langs) - 1))
        if title_ja and len(langs) > 1 and not series[series_idx[k]][1]:
            series[series_idx[k]][1] = title_ja
        return series_idx[k]

    favorites: dict[str, int] = {}           # char id -> favorites
    for row in f_rows:
        cid = _id(row.get(fm["fav_id"]))
        favorites[cid] = max(favorites.get(cid, 0), _num(row.get(fm["fav_count"])))
    print(f"favorites: {len(favorites)}")

    aliases: dict[str, int] = {}
    char_series: dict[str, set[int]] = {}    # char id -> series idx it appears in

    for row in a_rows:
        mal_id = _id(row.get(am["anime_id"])) if am["anime_id"] else ""
        titles = [row.get(am["anime_title"]) or ""]
        if am["anime_title2"]:
            titles.append(row.get(am["anime_title2"]) or "")
        if mal_id in en_titles:                   # the extra CSV's English title is the
            titles.insert(0, en_titles[mal_id])   # series title; the first CSV's stays an alias
        titles = [t.strip() for t in titles if t and t.strip()]
        if not titles:
            continue
        syn = _split_list(row.get(am["anime_synonyms"])) if am["anime_synonyms"] else []
        title_ja = (row.get(am["anime_title_ja"]) or "").strip() if am["anime_title_ja"] else ""
        title_ja = title_ja or ja_titles.get(mal_id, "")
        i = sidx(franchise_title(titles[0]), franchise_title(title_ja) if title_ja else "")
        for t in titles + syn + ([title_ja] if title_ja else []):
            aliases.setdefault(norm(t), i)
            aliases.setdefault(norm(franchise_title(t)), i)
        for cid in _character_ids(row.get(am["anime_chars"])):
            char_series.setdefault(_id(cid), set()).add(i)
    for i, titles_ in enumerate(series):
        for s_ in titles_:
            if s_:
                aliases.setdefault(norm(s_), i)

    # A series' prominence = the favorites of its characters; it orders each character's series.
    series_pop = [0] * len(series)
    for cid, in_series in char_series.items():
        for i in in_series:
            series_pop[i] += favorites.get(cid, 0)

    # --- characters ---------------------------------------------------------------------
    chars: list[list] = []
    names: dict[str, list[int]] = {}
    for row in c_rows:
        cid = _id(row.get(cm["char_id"]))
        appearances = char_series.get(cid)
        if not appearances:
            continue                          # not in any anime
        score = favorites.get(cid, 0)
        raw = (row.get(cm["char_name"]) or "").strip()
        if not raw:
            continue
        ordered = sorted(appearances, key=lambda i: -series_pop[i])[:3]
        ci = len(chars)
        kanji = japanese_name(row.get(cm["char_kanji"]) or "") if cm["char_kanji"] else ""
        chars.append([[raw] + ([kanji] if len(langs) > 1 else []), ordered, score])

        last, first = name_parts(raw)
        forms = {norm(raw), norm(last + first), norm(first + last)}
        if cm["char_full"]:
            forms.add(norm(row.get(cm["char_full"]) or ""))
        if kanji:
            kl, kf = name_parts(kanji)
            forms |= {norm(kanji), norm(kl + kf), norm(kf + kl)}
        forms.discard("")
        for f in forms:
            names.setdefault(f, []).append(ci)

    print(f"series: {len(series)}  aliases: {len(aliases)}  characters kept: {len(chars)}"
          f"  name keys: {len(names)}")
    return {"version": 1, "langs": langs, "series": series, "aliases": aliases,
            "chars": chars, "names": names}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--characters", type=Path, required=True)
    ap.add_argument("--anime", type=Path, required=True)
    ap.add_argument("--favorites", type=Path, required=True, metavar="CSV",
                    help="CSV with a character id column (character_id) and favorites; a character's "
                         "popularity score is its favorites")
    ap.add_argument("--japanese-titles", type=Path, default=None, metavar="CSV",
                    help="extra CSV with a MAL id column (mal_id) and title_japanese, joined to the "
                         "anime CSV by id; use it when the anime CSV has no Japanese titles")
    ap.add_argument("--output", type=Path, default=OUTPUT)
    ap.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN",
                    help="force a column, e.g. --map fav_count=Favorites")
    ap.add_argument("--show-columns", action="store_true", help="print headers + first row and exit")
    args = ap.parse_args()

    forced = dict(m.split("=", 1) for m in args.map)
    index = build(args.characters, args.anime, args.favorites, forced, show_only=args.show_columns, japanese_csv=args.japanese_titles)
    if index is None:
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.output} ({args.output.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
