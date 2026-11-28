# Modpack index

The Sideloader Modpack index is published by
[kk-mod-scraper](https://github.com/RedDeadDepresso/kk-mod-scraper), one file covering both Koikatsu and
Koikatsu Sunshine, and downloaded like the other [indices](index.md#downloaded-indices). A copy is kept in `%APPDATA%/KKAFIO/indices/`; the server is
asked at most once an hour, and the copy is only re-downloaded when its ETag changes
(`<name>.meta.json` holds the ETag and the time of the last check). If the download fails, the
cached copy is used.

| File | Games | Source |
|---|---|---|
| `modpack.json` | Koikatsu / Koikatsu Party / Koikatsu Sunshine | `https://reddeaddepresso.github.io/kk-mod-scraper/indices/modpack.json` |

Each maps a mod GUID to one entry per known version, newest first and an unknown version (`"unknown"`) last, with the URLs
that version can be downloaded from on the BetterRepack server:

```json
{
 "com.example.mod": [
  {"version": "1.1", "betterrepack": ["https://sideload.betterrepack.com/download/KKEC/Sideloader%20Modpack/a/mod%20v1.1.zipmod"]},
  {"version": "1.0", "betterrepack": ["https://sideload.betterrepack.com/download/KKEC/Sideloader%20Modpack/a/mod%20v1.0.zipmod"]}
 ]
}
```

The versions are normalized by the scraper (`1`, `v1.0` and `1.0.0` are all `1.0`). KKAFIO flattens them into one list of URLs per
GUID, the newest version's first (`kkafio.indices.modpack.ModpackIndex`), so the older versions are only tried when
the newer fails; `ModpackIndex.versions` maps each URL to its version, which is only used for logging. The older
`{"guids": {guid: [url, ...]}}` format is not read: a cached copy in it is downloaded again.

The path of a mod inside the mods folder is not stored: it is the URL's path below
`/download/KKEC/`, percent-decoded (`Sideloader Modpack/a/mod.zipmod`).
`kkafio.indices.modpack.url_to_rel_path()` does this and refuses URLs that are not on the
BetterRepack server or that could leave the mods folder.

Archive Cards, Delete Cards, and Download Missing Mods use the index to instantly identify which required mods are
covered by the Sideloader Modpack. If a GUID is not in the index, KKAFIO
falls back to scanning the local mods folder automatically. If the index can be neither
downloaded nor read from the cache, it is treated as empty, with a message in the log.
