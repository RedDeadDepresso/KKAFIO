# Mod index

Published by [kk-mod-scraper](https://github.com/RedDeadDepresso/kk-mod-scraper) and cached as `indices/mod.json` (refreshed like every [downloaded index](index.md#downloaded-indices)). Read by **Download Missing Mods**.

`mod.json` maps mod GUIDs to the links the mod can be downloaded from, per host, e.g.
`{ "com.AgiShark.Casual_Moves_Part2": { "mega": [], "drive": ["https://drive.google.com/file/d/…/view"], "telegram": ["https://t.me/KK_archive_modlibrary/25"], "gofile": [], "catbox": [], "mediafire": [], "pixeldrain": [] } }`.
Each list may hold several links or none. A Gofile, Catbox, MediaFire or Pixeldrain link is always used as such, even if the index put it in another list. The links of a mod are tried in the order of the **Mod Sources**
option (`ModSources`: an `ordered_checkbox` of `Drive`, `Mega`, `Telegram`, `Gofile`, `Catbox`, `MediaFire`, `Pixeldrain`, in the user's order; unchecked
sources are not used) until one downloads the mod; a download is only kept if its `manifest.xml` has the GUID
that was asked for. `SOURCE_ORDER` in `src/kkafio/indices/mod.py` is only the default for `ordered_links()`.
The index is only read when at least one mod needs a download from outside the modpack and at least one source
is checked. Telegram is only connected to (the account is signed in once, before the first link is tried) when
`Telegram` is checked; if that fails, the Drive, Mega, Gofile, Catbox, MediaFire and Pixeldrain links still work. With `Telegram` checked and
`UseTelegramChatLinks` on, a search of the configured chats (`TelegramChatLinks`) follows the Telegram link in
that order, and is also what is left for a mod the index has no link for.
