"""
download_missing_mods.py — Find and download mods that characters reference
but are not present in the input / output mods directories.

Strategy
--------
1. Build / load the mods cache of the input mods directory and of the output
   mods directory (both default to the game's mods directory when left
   blank; if they resolve to the same folder it is only scanned once).
2. Build / load the chara GUID cache (all GUIDs referenced by chara cards).
3. missing_local = referenced_guids - input_mods_guids - output_mods_guids
4. Sideloader Modpack mode:
     Skip     — ignore modpack GUIDs entirely (only download local-only mods)
     OnlyUsed — download missing GUIDs that are in the modpack index or on
                the KKC mod index / Telegram Chat Links
     All      — also download every GUID in the modpack index not installed
5. For each GUID to download:
     a) In modpack index → BetterRepack (httpx, no auth)
     b) Not in index, Telegram Source is KoikatsuCards/Both → look up the
        GUID in kkc_mod_index.json (kkc-mod-scraper; cached in CONFIG_DIR and
        refreshed when the repo's latest commit changes), then download the
        linked t.me message directly from Telegram
     c) Not in index (or the KKC mod index had no link / the download
        failed), Telegram Source is ChatLinks/Both → search each
        configured Telegram Chat Links entry (channel, group, or forum
        topic) via Telegram's server-side document search, and download
        the first result whose filename ends in .zipmod or .zip
     d) Otherwise → log as unresolved
6. Everything is downloaded into the OUTPUT mods directory (which also
   receives the report file).
"""

import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import kkafio.services.telegram_config as tg_cfg

from kkafio.cards.mods import build_mods_cache, load_modpack_index
from kkafio.cards.png_guids import collect_chara_guids, collect_coord_guids, collect_scene_guids
from kkafio.core.config import GameType
from kkafio.core.logger import logger
from kkafio.services.http_mod_sources import (
    download_betterrepack,
    load_kkc_mod_index,
    make_http_client,
)
from kkafio.services.telegram_links import (
    parse_chat_links,
    telegram_source_label,
    DEFAULT_TELEGRAM_CHAT_LINKS,
)
from kkafio.services.telegram_mods import (
    download_via_teleget,
    ensure_session,
    search_chat_links_and_download,
)
from kkafio.tasks.base_task import BaseTask


class DownloadMissingMods(BaseTask):
    def __init__(self, config, file_manager):
        super().__init__(config, file_manager)
        cfg = self.config.download_missing_mods
        self.input_mods_dir_str  : str  = cfg.get("InputModsDir",        "")
        self.output_mods_dir_str : str  = cfg.get("OutputModsDir",       "")
        self.chara_dir_str       : str  = cfg.get("CharaDir",             "")
        self.scene_dir_str       : str  = cfg.get("SceneDir",             "")
        self.coord_dir_str       : str  = cfg.get("CoordDir",             "")
        self.content_types       : list[str] = cfg.get("ContentTypes",    ["Chara", "Scene", "Coord"])
        self.use_cache           : bool = cfg.get("UseCache",             True)
        self.open_report         : bool = cfg.get("OpenReport",           True)
        self.modpack_mode        : str  = cfg.get("SideloaderModpack",    "OnlyUsed")
        self.telegram_source     : str  = cfg.get("TelegramSource",       "No")  # No | KoikatsuCards | ChatLinks | Both
        self.telegram_chat_links_raw : str = cfg.get("TelegramChatLinks", DEFAULT_TELEGRAM_CHAT_LINKS)

    @staticmethod
    def _write_readme(
        input_mods_dir: Path,
        output_mods_dir: Path,
        chara_guids: set[str],
        scene_guids: set[str],
        coord_guids: set[str],
        local_guids: set[str],
        modpack_index: dict[str, str],
        to_download: set[str],
        from_betterrepack: dict[str, str],
        downloaded_br: set[str],
        from_telegram: list[str],
        downloaded_tg: set[str],
        unresolved: list[str],
        failed_guids: set[str],
        ok: int,
        fail: int,
        modpack_mode: str,
        telegram_source: str,
        generated: str,
    ) -> Path | None:
        """Write a report to output_mods_dir summarising the download run.

        Returns the report's path, or None if it couldn't be written."""
        referenced_guids = chara_guids | scene_guids | coord_guids
        missing_all = referenced_guids - local_guids

        modpack_covered = referenced_guids & set(modpack_index.keys())
        if modpack_mode == "Skip":
            covered_count = len(local_guids & referenced_guids)
        else:
            covered_count = len((local_guids | modpack_covered) & referenced_guids)

        lines: list[str] = [
            "KKAFIO — Download Missing Mods Report",
            f"Generated        : {generated}",
            f"Input mods dir   : {input_mods_dir}",
            f"Output mods dir  : {output_mods_dir}",
            f"Sideloader mode  : {modpack_mode}",
            f"Telegram source  : {telegram_source}",
            "",
            "=" * 60,
            "",
            f"Character card mod references : {len(chara_guids)}",
            f"Scene mod references          : {len(scene_guids)}",
            f"Coordinate mod references     : {len(coord_guids)}",
            f"Already installed / covered   : {covered_count}",
            f"Missing total                 : {len(missing_all)}",
            f"Queued for download           : {len(to_download)}",
            f"  — from BetterRepack         : {len(from_betterrepack)}",
            f"  — from Telegram             : {len(from_telegram)}",
            f"  — unresolvable              : {len(unresolved)}",
            "",
            f"Downloaded successfully : {ok}",
            f"Failed                  : {fail}",
            "",
        ]

        if unresolved:
            lines += [
                "=" * 60,
                "Unresolvable mods (not in modpack index, no Telegram source found):",
                "These mods could not be downloaded automatically.",
                "Search for them manually on the KKC mod index or game modding communities.",
                "",
            ]
            for guid in sorted(unresolved):
                lines.append(f"  ! {guid}")
            lines.append("")

        if failed_guids:
            lines += [
                "=" * 60,
                f"Failed downloads ({len(failed_guids)}):",
                "These mods were found but could not be downloaded.",
                "Check your internet connection and try again.",
                "",
            ]
            for guid in sorted(failed_guids):
                lines.append(f"  ✗ {guid}")
            lines.append("")

        # Only list mods that were actually downloaded (not skipped/already present)
        actually_downloaded_br = {guid: rel for guid, rel in from_betterrepack.items()
                                   if guid in downloaded_br}
        actually_downloaded_tg = [guid for guid in from_telegram if guid in downloaded_tg]

        if actually_downloaded_br:
            lines.append("Downloaded from BetterRepack:")
            for guid, rel in sorted(actually_downloaded_br.items()):
                lines.append(f"  + {guid}")
                lines.append(f"    {rel}")
            lines.append("")

        if actually_downloaded_tg:
            lines.append("Downloaded via Telegram:")
            for guid in sorted(actually_downloaded_tg):
                lines.append(f"  + {guid}")
            lines.append("")

        if modpack_mode == "Skip":
            modpack_missing = referenced_guids & set(modpack_index.keys()) - local_guids
            if modpack_missing:
                lines += [
                    "=" * 60,
                    f"Sideloader Modpack mods skipped ({len(modpack_missing)}) — mode is 'Skip':",
                    "These mods are part of the Sideloader Modpack and were not downloaded.",
                    "Install the Sideloader Modpack from: https://dl.betterrepack.com/",
                    "",
                ]
                for guid in sorted(modpack_missing):
                    lines.append(f"  ~ {guid} ({modpack_index[guid]})")
                lines.append("")

        readme_path = output_mods_dir / "kkafio_missing_mods_report.txt"
        try:
            readme_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("DLMOD", f"Report saved: {readme_path}")
            return readme_path
        except Exception as e:
            logger.warning("DLMOD", f"Could not write report: {e}")
            return None

    @staticmethod
    def _open_file(path: Path) -> None:
        """Open `path` with the system's default program (best effort)."""
        try:
            if sys.platform == "win32":
                os.startfile(str(path))                      # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            logger.warning("DLMOD", f"Could not open report: {e}")

    def run(self) -> None:
        game_path = self.config.game_path

        # ── Resolve directories ───────────────────────────────────────────
        # Input and output mods dirs each fall back to the game's mods dir
        # when left blank.
        default_mods_dir = game_path["mods"] if "mods" in game_path else None

        def _resolve_mods_dir(value: str, label: str) -> Path | None:
            if value.strip():
                return Path(value.strip())
            if default_mods_dir is not None:
                return Path(default_mods_dir)
            logger.error("DLMOD",
                f"{label} mods directory not set and not resolvable from game path.")
            return None

        input_mods_dir  = _resolve_mods_dir(self.input_mods_dir_str,  "Input")
        output_mods_dir = _resolve_mods_dir(self.output_mods_dir_str, "Output")
        if input_mods_dir is None or output_mods_dir is None:
            return

        if not input_mods_dir.exists():
            logger.error("DLMOD", f"Input mods directory does not exist: {input_mods_dir}")
            return

        # The output dir only receives downloads, so create it if needed.
        if not output_mods_dir.exists():
            try:
                output_mods_dir.mkdir(parents=True, exist_ok=True)
                logger.info("DLMOD", f"Created output mods directory: {output_mods_dir}")
            except OSError as e:
                logger.error("DLMOD",
                    f"Output mods directory does not exist and could not be created: "
                    f"{output_mods_dir} ({e})")
                return

        try:
            same_mods_dir = input_mods_dir.resolve() == output_mods_dir.resolve()
        except OSError:
            same_mods_dir = input_mods_dir == output_mods_dir

        if not self.content_types:
            logger.error("DLMOD",
                "No content types selected (Characters/Scenes/Coordinates) — nothing to scan.")
            return

        scan_chara = "Chara" in self.content_types
        scan_scene = "Scene" in self.content_types
        scan_coord = "Coord" in self.content_types

        chara_dirs: list[Path] = []
        if scan_chara:
            if self.chara_dir_str:
                chara_dirs = [Path(self.chara_dir_str)]
            else:
                chara_dirs = [
                    d for d in [game_path.get("charaFemale"), game_path.get("charaMale")]
                    if d is not None and d.exists()
                ]
            if not chara_dirs:
                logger.error("DLMOD", "Chara directory not set and not resolvable from game path.")
                return

        scene_dirs: list[Path] = []
        if scan_scene:
            if self.scene_dir_str:
                scene_dirs = [Path(self.scene_dir_str)]
            else:
                scene_dirs = [
                    d for d in [game_path.get("scene")]
                    if d is not None and d.exists()
                ]

        coord_dirs: list[Path] = []
        if scan_coord:
            if self.coord_dir_str:
                coord_dirs = [Path(self.coord_dir_str)]
            else:
                coord_dirs = [
                    d for d in [game_path.get("coordinate")]
                    if d is not None and d.exists()
                ]

        self.log_start("DLMOD")
        if same_mods_dir:
            logger.info("DLMOD", f"Mods dir  : {output_mods_dir} (input and output)")
        else:
            logger.info("DLMOD", f"Input mods dir  : {input_mods_dir}")
            logger.info("DLMOD", f"Output mods dir : {output_mods_dir}")
        if scan_chara:
            for d in chara_dirs:
                logger.info("DLMOD", f"Chara dir : {d}")
        else:
            logger.info("DLMOD", "Chara dir : skipped (Characters not selected)")
        if scan_scene:
            if scene_dirs:
                for d in scene_dirs:
                    logger.info("DLMOD", f"Scene dir : {d}")
            else:
                logger.info("DLMOD", "Scene dir : not set / Studio not installed — skipping scenes")
        else:
            logger.info("DLMOD", "Scene dir : skipped (Scenes not selected)")
        if scan_coord:
            if coord_dirs:
                for d in coord_dirs:
                    logger.info("DLMOD", f"Coord dir : {d}")
            else:
                logger.info("DLMOD", "Coord dir : not set / not resolvable — skipping coordinates")
        else:
            logger.info("DLMOD", "Coord dir : skipped (Coordinates not selected)")
        logger.info("DLMOD", f"Modpack   : {self.modpack_mode}")

        # ── Step 1: mods caches (input + output) ──────────────────────────
        # guid_str_map deliberately EXCLUDES the Sideloader Modpack subtree
        # (include_modpack=False) and only covers the OUTPUT dir — it is the
        # set new downloads get written into ("Local GUIDs: N" describes the
        # user's own non-modpack mods there).
        guid_str_map = build_mods_cache(output_mods_dir, include_modpack=False, use_cache=self.use_cache)
        logger.info("DLMOD", f"Output dir GUIDs: {len(guid_str_map)}")

        # But "is this GUID already installed at all" (used below to decide
        # what's actually missing) needs the FULL picture — including mods
        # inside a Sideloader Modpack folder, which is where BetterRepack's
        # own installer puts everything — from BOTH directories:
        #   missing = referenced - input GUIDs - output GUIDs
        # Otherwise "OnlyUsed" would re-download every modpack GUID the user
        # already has, and "All" would try to re-download the whole modpack.
        # The cache file is shared between include_modpack scopes, so this
        # doesn't re-hash anything the call above already covered.
        output_guid_map = build_mods_cache(output_mods_dir, include_modpack=True, use_cache=self.use_cache)
        output_guids: set[str] = set(output_guid_map.keys())

        if same_mods_dir:
            input_guids: set[str] = set(output_guids)
        else:
            input_guid_map = build_mods_cache(input_mods_dir, include_modpack=True, use_cache=self.use_cache)
            input_guids = set(input_guid_map.keys())
            logger.info("DLMOD", f"Input dir GUIDs : {len(input_guids)}")

        all_local_guids: set[str] = input_guids | output_guids

        # ── Step 2: modpack index ─────────────────────────────────────────
        game_type     = self.config.config_data.get("Core", {}).get("GameType", GameType.KOIKATSU.value)
        modpack_index = load_modpack_index(game_type=game_type) or {}
        if modpack_index:
            logger.info("DLMOD", f"Modpack index loaded: {len(modpack_index)} GUIDs")
        else:
            logger.warning("DLMOD",
                "kkafio_modpack_index_kk/kks.json not found — "
                "BetterRepack downloads unavailable.")

        # ── Step 3: chara + scene + coord GUIDs ─────────────────────────────
        chara_guids: set[str] = set()
        if scan_chara:
            chara_guids = collect_chara_guids(chara_dirs, self.use_cache)
            logger.info("DLMOD", f"Chara references: {len(chara_guids)} unique GUIDs")

        scene_guids: set[str] = set()
        if scan_scene and scene_dirs:
            scene_guids = collect_scene_guids(scene_dirs, self.use_cache)
            logger.info("DLMOD", f"Scene references: {len(scene_guids)} unique GUIDs")

        coord_guids: set[str] = set()
        if scan_coord and coord_dirs:
            coord_guids = collect_coord_guids(coord_dirs, self.use_cache)
            logger.info("DLMOD", f"Coord references: {len(coord_guids)} unique GUIDs")

        referenced_guids = chara_guids | scene_guids | coord_guids

        # ── Step 4: decide what to download ──────────────────────────────
        # all_local_guids = input dir GUIDs | output dir GUIDs (both including
        # any Sideloader Modpack subtree) — see Step 1 — so a mod already
        # present in either directory isn't treated as missing.
        missing_local: set[str] = referenced_guids - all_local_guids

        match self.modpack_mode:
            case "Skip":
                to_download = {g for g in missing_local if g not in modpack_index}
            case "OnlyUsed":
                to_download = missing_local
            case "All":
                to_download = missing_local | (set(modpack_index.keys()) - all_local_guids)
            case _:
                to_download = missing_local

        logger.info("DLMOD",
            f"Missing local: {len(missing_local)} | "
            f"To download ({self.modpack_mode}): {len(to_download)}")

        if not to_download:
            logger.success("DLMOD", "Nothing to download.")
            return

        # ── Step 5: partition ─────────────────────────────────────────────
        use_koikatsucards = self.telegram_source in ("KoikatsuCards", "Both")
        use_chat_links    = self.telegram_source in ("ChatLinks", "Both")
        use_telegram      = use_koikatsucards or use_chat_links
        if not use_telegram:
            logger.info("DLMOD",
                "Telegram Source is 'No' — "
                "only BetterRepack mods will be downloaded.")

        from_betterrepack: dict[str, str] = {}
        from_telegram    : list[str]      = []
        unresolved       : list[str]      = []

        for guid in sorted(to_download):
            if guid in modpack_index:
                from_betterrepack[guid] = modpack_index[guid]
            elif use_telegram:
                from_telegram.append(guid)
            else:
                unresolved.append(guid)

        logger.info("DLMOD",
            f"  BetterRepack: {len(from_betterrepack)} | "
            f"Telegram: {len(from_telegram)} | "
            f"Unresolved: {len(unresolved)}")

        if unresolved:
            logger.warning("DLMOD",
                f"{len(unresolved)} GUID(s) unresolvable:")
            for guid in unresolved:
                logger.warning("DLMOD", f"  {guid}")

        # ── Step 6: download ──────────────────────────────────────────────
        ok = fail = 0
        failed_guids:  set[str]  = set()
        downloaded_br:  set[str] = set()
        downloaded_tg:  set[str] = set()

        async def _run_all() -> None:
            nonlocal ok, fail, failed_guids, downloaded_br, downloaded_tg
            async with make_http_client() as br_client:

                # BetterRepack — concurrent
                br_failed     = {}   # guid -> rel_path for failed BR downloads
                downloaded_br = set()  # actually downloaded (not skipped)

                if from_betterrepack:
                    logger.info("DLMOD",
                        f"Downloading {len(from_betterrepack)} mod(s) from BetterRepack...")
                    results = await asyncio.gather(*[
                        download_betterrepack(br_client, guid, rel, output_mods_dir, guid_str_map)
                        for guid, rel in from_betterrepack.items()
                    ], return_exceptions=True)
                    for guid, result in zip(from_betterrepack, results, strict=True):
                        if result is True:
                            ok += 1
                            downloaded_br.add(guid)
                        elif result == "skipped":
                            pass  # already existed — don't count or report
                        else:
                            fail += 1
                            if isinstance(result, Exception):
                                logger.error("DLMOD", f"Exception [{guid}]: {result}")
                            # Queue for Telegram fallback if enabled
                            if use_telegram:
                                br_failed[guid] = from_betterrepack[guid]
                                logger.info("DLMOD",
                                    f"  [{guid}] will be retried via Telegram")
                            else:
                                failed_guids.add(guid)

                # Merge BetterRepack failures into Telegram queue
                telegram_queue: list[tuple[str, str | None]] = [
                    (guid, None) for guid in from_telegram
                ] + [
                    (guid, rel) for guid, rel in br_failed.items()
                ]

                # Telegram — sequential
                if telegram_queue:
                    kkc_index: dict[str, str] = {}
                    if use_koikatsucards:
                        kkc_index = await load_kkc_mod_index(br_client)

                    # Load/prompt for credentials once before the loop
                    tg_data = tg_cfg.get_or_prompt()
                    if tg_data is None:
                        logger.error("DLMOD",
                            "Telegram credentials not provided — "
                            f"skipping {len(telegram_queue)} mod(s).")
                        # br_failed's `fail += 1` already happened in the
                        # BetterRepack loop above (this used to double-count
                        # it here on top of that). from_telegram was never
                        # counted anywhere yet, so it still needs it. Both
                        # groups are added to failed_guids here too — the
                        # BetterRepack loop deliberately leaves a br_failed
                        # guid out of failed_guids while Telegram is still
                        # queued to retry it, and neither group was ever
                        # added to the itemized failure list otherwise, so
                        # without this the final "failed" count and the
                        # per-GUID list shown in the report would disagree.
                        fail += len(from_telegram)
                        failed_guids.update(from_telegram)
                        failed_guids.update(br_failed)
                    else:
                        # Ensure session file exists before starting downloads
                        authorised = await ensure_session(tg_data)
                        if not authorised:
                            logger.error("DLMOD",
                                "Telegram sign-in failed — "
                                f"skipping {len(telegram_queue)} mod(s).")
                            fail += len(from_telegram)
                            failed_guids.update(from_telegram)
                            failed_guids.update(br_failed)
                        else:
                            teleget_downloader = None

                            # One TelegramClient, connected once and reused for
                            # every GUID in this batch (both the KKC-index
                            # metadata/fallback-download path and the Telegram
                            # Chat Links search/download path below) — this used
                            # to open and close a brand new connection per GUID
                            # per path, which for a batch of N missing mods meant
                            # up to ~2N-3N separate MTProto connect/auth round
                            # trips to Telegram for what only ever needed one.
                            from telethon import TelegramClient as _TelegramClient
                            from kkafio.core.paths import CONFIG_DIR as _TG_CFG_DIR
                            _tg_session_dir = _TG_CFG_DIR / "config" / "tg_session"
                            tg_client = _TelegramClient(
                                str(_tg_session_dir / "kkafio"),
                                tg_data["api_id"], tg_data["api_hash"],
                            )
                            await tg_client.connect()

                            if use_koikatsucards:
                                # [FIX-2026-09-13-ENTITY-CACHE-WARMUP] Resolve the
                                # KK_archive_modlibrary channel by username *once*,
                                # using the primary session, before the daemon
                                # subprocess is started below.
                                #
                                # Why this is needed: every download further down
                                # this pipeline (both the per-GUID metadata lookup
                                # in download_via_teleget, and teleget9527's own
                                # daemon-side get_messages(request.chat_id, ...)
                                # call) references the channel via the raw numeric
                                # KK_ARCHIVE_CHAT_ID constant, not its username.
                                # Telethon can only turn a bare numeric peer ID
                                # into a usable InputPeer if it already has that
                                # entity's access_hash cached in the session file
                                # (populated by an earlier get_entity/get_dialogs
                                # call, or by the account having already interacted
                                # with the chat via the Telegram app itself). On a
                                # brand-new session that has never touched this
                                # channel, resolving the raw ID directly raises:
                                #   ValueError: Could not find the input entity
                                #   for PeerUser(user_id=...)
                                #
                                # The daemon's own session is a one-time copy of
                                # this primary session, taken when
                                # teleget_downloader.start() launches it just
                                # below — so if the cache isn't warmed *before*
                                # that copy happens, the daemon inherits a cold
                                # cache and hits the exact same error internally,
                                # just deeper in the pipeline and harder to
                                # diagnose. Resolving by username here (which
                                # Telethon can do via a fresh API call even with
                                # no prior cache) warms the primary session's
                                # cache first, so the copy the daemon receives is
                                # already warm, and every later raw-numeric-ID
                                # lookup — in this file and inside teleget9527 —
                                # succeeds on the very first attempt.
                                try:
                                    # Warm the *shared* client's entity cache — it
                                    # stays connected for the rest of this batch,
                                    # so this also directly benefits every
                                    # Telegram metadata lookup below, not
                                    # just the daemon's copied session.
                                    await tg_client.get_entity("KK_archive_modlibrary")
                                    logger.info("DLMOD",
                                        "Warmed entity cache for KK_archive_modlibrary")
                                except Exception as warm_err:
                                    logger.warning("DLMOD",
                                        f"Could not pre-warm channel entity cache "
                                        f"(non-fatal, downloads may still fail on a "
                                        f"cold cache): {warm_err}")

                            # Create TGDownloader once and reuse across all downloads,
                            # for both the KKC-index path and the Telegram Chat
                            # Links path.
                            from kkafio.core.paths import CONFIG_DIR as _CFG_DIR
                            _session_dir = _CFG_DIR / "config" / "tg_session"
                            try:
                                from tg_downloader import TGDownloader

                                # [FIX-2026-09-14-SUPPRESS-TELEGET-CONSOLE-LOGS]
                                # The download daemon runs as a separate
                                # multiprocessing child process, re-executing
                                # this frozen app fresh — kkafio_cli.py's own
                                # __main__ logic (including the console-log
                                # suppression it applies to *this* process,
                                # see suppress_teleget_console_logs()) never
                                # runs there at all, since
                                # multiprocessing.freeze_support() intercepts
                                # before reaching it. daemon_console_log_level
                                # is a separate teleget9527 config key
                                # (added specifically to support this) that
                                # controls only the daemon's console handler,
                                # independent of its file handler — unlike
                                # daemon_log_level, which controls both
                                # together. Setting it to CRITICAL effectively
                                # silences the daemon's console output
                                # entirely (nothing below CRITICAL is ever
                                # emitted there) while its log file keeps
                                # full INFO/DEBUG detail for troubleshooting.
                                # Errors from this task are already reported
                                # to the user through KKAFIO's own error
                                # handling, which points them at the log
                                # file, so there's no need to also mirror
                                # teleget's own console output for
                                # visibility. Only applied in frozen/packaged
                                # builds — keep full console detail for
                                # developers running from source.
                                _teleget_config = (
                                    {
                                        "daemon_log_level": "INFO",
                                        "daemon_console_log_level": "CRITICAL",
                                    }
                                    if getattr(sys, "frozen", False)
                                    else None
                                )

                                teleget_downloader = TGDownloader(
                                    api_id=tg_data["api_id"],
                                    api_hash=tg_data["api_hash"],
                                    session_dir=str(_session_dir.resolve()),
                                    config=_teleget_config,
                                )
                                await teleget_downloader.start("kkafio")
                                logger.info("DLMOD", "teleget9527 downloader started")
                            except ImportError:
                                logger.info("DLMOD",
                                    "teleget9527 not installed, using Telethon fallback "
                                    "(install with: pip install teleget9527[fast])")

                            chat_links: list[tuple[str | int, int | None]] = []
                            if use_chat_links:
                                chat_links = parse_chat_links(self.telegram_chat_links_raw)
                                if not chat_links:
                                    logger.warning("DLMOD",
                                        "Telegram Chat Links is enabled but no valid "
                                        "chat links are configured — nothing to search.")

                            source_label = telegram_source_label(self.telegram_source)
                            logger.info("DLMOD",
                                f"Processing {len(telegram_queue)} mod(s) via {source_label}...")

                            try:
                                for guid, rel_path in telegram_queue:
                                    found_source = False
                                    success: bool | str = False

                                    if use_koikatsucards:
                                        tg_link = kkc_index.get(guid, "")
                                        if tg_link:
                                            found_source = True
                                            logger.info("DLMOD", f"  Link: {tg_link}")
                                            # Pass rel_path so the file is saved to the
                                            # same subfolder as the modpack index
                                            success = await download_via_teleget(
                                                guid, tg_link, output_mods_dir, tg_data,
                                                guid_str_map, tg_client,
                                                downloader=teleget_downloader,
                                                rel_path=rel_path,
                                            )
                                        else:
                                            logger.warning("DLMOD",
                                                f"  {guid} — not in kkc mod index")

                                    if success not in (True, "skipped") and use_chat_links and chat_links:
                                        if use_koikatsucards:
                                            logger.info("DLMOD",
                                                f"  Trying Telegram Chat Links for {guid}...")
                                        chat_found, success = await search_chat_links_and_download(
                                            guid, chat_links, output_mods_dir, tg_data,
                                            guid_str_map, tg_client,
                                            downloader=teleget_downloader,
                                            rel_path=rel_path,
                                        )
                                        found_source = found_source or chat_found

                                    if success == "skipped":
                                        pass  # already existed — don't count or report
                                    elif success is True:
                                        failed_guids.discard(guid)
                                        downloaded_tg.add(guid)
                                        if guid in br_failed:
                                            fail -= 1
                                        ok += 1
                                    elif found_source:
                                        # A source was identified somewhere but the
                                        # download itself failed
                                        failed_guids.add(guid)
                                        if guid not in br_failed:
                                            fail += 1
                                    else:
                                        # No source found anywhere that was tried.
                                        # A br_failed guid was already counted
                                        # into `fail` back in the BetterRepack
                                        # loop (deferred there pending this
                                        # Telegram retry) — since it's ending up
                                        # here as unresolved rather than a
                                        # confirmed failure, undo that count so
                                        # the final "failed: N, unresolved: M"
                                        # summary doesn't count the same guid
                                        # in both buckets.
                                        if guid in br_failed:
                                            fail -= 1
                                            failed_guids.discard(guid)
                                        unresolved.append(guid)
                            finally:
                                if teleget_downloader is not None:
                                    try:
                                        await teleget_downloader.shutdown()
                                    except Exception as e:
                                        # teleget9527's ShutdownRequest bug — harmless, downloads are done
                                        logger.debug("DLMOD", f"teleget9527 shutdown error (ignored): {e}")
                                try:
                                    await tg_client.disconnect()
                                except Exception as e:
                                    logger.debug("DLMOD", f"Telegram disconnect failed (ignored): {e}")

        asyncio.run(_run_all())

        logger.line()

        # Write the report to the output mods dir summarising what was downloaded
        report_path = self._write_readme(
            input_mods_dir    = input_mods_dir,
            output_mods_dir   = output_mods_dir,
            chara_guids       = chara_guids,
            scene_guids       = scene_guids,
            coord_guids       = coord_guids,
            # Union of both directories (incl. any Sideloader Modpack
            # subtree), same as missing_local above.
            local_guids       = all_local_guids,
            modpack_index     = modpack_index,
            to_download       = to_download,
            from_betterrepack = from_betterrepack,
            downloaded_br     = downloaded_br,
            from_telegram     = from_telegram,
            downloaded_tg     = downloaded_tg,
            unresolved        = unresolved,
            failed_guids      = failed_guids,
            ok                = ok,
            fail              = fail,
            modpack_mode      = self.modpack_mode,
            telegram_source   = telegram_source_label(self.telegram_source),
            generated         = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        logger.success("DLMOD",
            f"Done — downloaded: {ok}, failed: {fail}, "
            f"unresolved: {len(unresolved)}")

        # Open the report when anything is still missing or a download failed
        # (mods skipped on purpose via the Sideloader Modpack mode don't count).
        if self.open_report and report_path is not None and (unresolved or failed_guids or fail):
            logger.info("DLMOD", "Mods are still missing — opening the report")
            self._open_file(report_path)
