# Mod index

Published by [kk-mod-scraper](https://github.com/RedDeadDepresso/kk-mod-scraper) and cached as `indices/mod.json` (refreshed like every [downloaded index](index.md#downloaded-indices)). Read by **Download Missing Mods**.

`mod.json` maps mod GUIDs to a list with one entry per known version of the mod, newest version first and the
entry of an unknown version (`"version": "unknown"`) last. Each entry has the links the version can be downloaded from, per host, e.g.
`{ "com.AgiShark.Casual_Moves_Part2": [ { "version": "1.1", "mega": [], "drive": ["https://drive.google.com/file/d/…/view"], "pixeldrain": [], "dropbox": [], "mediafire": [], "catbox": [], "gofile": [], "telegram": ["https://t.me/KK_archive_modlibrary/25"] }, { "version": "1.0", … } ] }`.
The scraper normalizes versions (`1`, `v1.0`, `1.00` and `1.0.0` are all `1.0`). The version is a string and is
only used for logging: KKAFIO never compares versions, it relies on the order of the file. Entries and lists in any
other format (such as the older `{guid: {source: [links]}}`) are ignored, so a cached copy in an older format is downloaded again.
Each list may hold several links or none. A Gofile, Catbox, MediaFire, Pixeldrain or Dropbox link is always used as such, even if the index put it in another list. The links of a mod are tried newest version first and, within a version, in the order of the **Mod Sources**
option (`ModSources`: an `ordered_checkbox` of `Drive`, `Mega`, `Telegram`, `Gofile`, `Catbox`, `MediaFire`, `Pixeldrain`, `Dropbox`, in the user's order; unchecked
sources are not used) until one downloads the mod (newest version first; within a version, in that order); a download is only kept if its `manifest.xml` has the GUID
that was asked for. `SOURCE_ORDER` in `src/kkafio/indices/mod.py` is only the default for `ordered_links()`.
The index is only read when at least one mod needs a download from outside the modpack and at least one source
is checked. Telegram is only connected to (the account is signed in once, before the first link is tried) when
`Telegram` is checked; if that fails, the Drive, Mega, Gofile, Catbox, MediaFire, Pixeldrain and Dropbox links still work. With `Telegram` checked and
`UseTelegramChatLinks` on, a search of the configured chats (`TelegramChatLinks`) follows the Telegram link in
that order, and is also what is left for a mod the index has no link for.
