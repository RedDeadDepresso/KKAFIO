"""
sync_i18n_keys.py — Keep assets/i18n/*.json in sync with interface.json.

interface.json is the single source of truth for which "$dotted.key"
translation keys exist (see docs/03-interface-json.md). Every time a task,
option, group, or preset is added/renamed/removed there, the language files
under assets/i18n/ need the matching keys added or removed by hand — easy to
forget, and easy to leave stale/orphaned keys behind. This script automates
that:

  - Walks interface.json the same way the GUI resolves translations: root
    label/title/description, then each group's label, then each option's
    label/description/case labels/input labels, then each task's
    label/description, then each preset's label/description.
  - For every language listed under interface.json's "languages" field:
      - Adds any key interface.json now references but the file is missing.
        A brand-new key is seeded with English text if en_us.json already
        has it (flagged as needing real translation), otherwise with a
        "[TODO] dotted.key.name" placeholder.
      - Removes any key the file has that interface.json no longer
        references anywhere.
      - Leaves the value of every key that's still referenced untouched —
        already-translated text is never rewritten.
      - Reorders keys to match interface.json's structural order (root,
        group, option, task, preset), so new keys land in a sensible spot
        instead of always being appended at the end.
  - Prints a summary per language (added/removed/still-needs-translation)
    and exits non-zero on --check if anything is out of sync, for CI.

Usage:
  python tools/sync_i18n_keys.py                  # sync in place
  python tools/sync_i18n_keys.py --dry-run         # show what would change
  python tools/sync_i18n_keys.py --check           # exit 1 if out of sync, change nothing
  python tools/sync_i18n_keys.py --interface path/to/interface.json
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIMARY_LANG = "en_us"


# ---------------------------------------------------------------------------
# Key collection — mirrors "Translations (languages)" in
# docs/03-interface-json.md: top-level, group, option (+ cases + inputs),
# task, preset, in that order.
# ---------------------------------------------------------------------------

def _ref(value) -> str | None:
    """Return the dotted key of a "$dotted.key" string, else None."""
    if isinstance(value, str) and value.startswith("$") and len(value) > 1:
        return value[1:]
    return None


def collect_keys(interface: dict) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add(value) -> None:
        key = _ref(value)
        if key is not None and key not in seen:
            seen.add(key)
            keys.append(key)

    # Top-level: label, title, description
    for field in ("label", "title", "description"):
        add(interface.get(field))

    # Groups
    for group in interface.get("group", []):
        add(group.get("label"))

    # Options: label, description, case labels, input labels
    for option in interface.get("option", {}).values():
        add(option.get("label"))
        add(option.get("description"))
        for case in option.get("cases", []):
            add(case.get("label"))
        for inp in option.get("inputs", []):
            add(inp.get("label"))

    # Tasks
    for task in interface.get("task", []):
        add(task.get("label"))
        add(task.get("description"))

    # Presets
    for preset in interface.get("preset", []):
        add(preset.get("label"))
        add(preset.get("description"))

    return keys


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def sync_language(
    keys: list[str],
    existing: dict[str, str],
    primary: dict[str, str],
    lang: str,
) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """Build the new key->value dict for one language.

    Returns (new_dict, added_keys, removed_keys, needs_translation_keys).
    """
    added: list[str] = []
    needs_translation: list[str] = []
    new_dict: dict[str, str] = {}

    for key in keys:
        if key in existing:
            new_dict[key] = existing[key]
            continue

        added.append(key)
        if lang == PRIMARY_LANG:
            placeholder = f"[TODO] {key}"
            needs_translation.append(key)
        elif key in primary and not primary[key].startswith("[TODO] "):
            placeholder = primary[key]
            needs_translation.append(key)
        else:
            placeholder = f"[TODO] {key}"
            needs_translation.append(key)
        new_dict[key] = placeholder

    removed = [k for k in existing if k not in new_dict]
    return new_dict, added, removed, needs_translation


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--interface", default=None, metavar="PATH",
        help="Path to interface.json (default: <repo root>/interface.json)",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing any files",
    )
    ap.add_argument(
        "--check", action="store_true",
        help="Change nothing; exit with status 1 if any file is out of sync (for CI)",
    )
    args = ap.parse_args()

    interface_path = Path(args.interface).resolve() if args.interface else REPO_ROOT / "interface.json"
    if not interface_path.exists():
        print(f"ERROR: interface.json not found: {interface_path}", file=sys.stderr)
        sys.exit(1)

    interface = json.loads(interface_path.read_text(encoding="utf-8"))
    languages: dict[str, str] = interface.get("languages", {})
    if not languages:
        print("ERROR: interface.json has no \"languages\" field.", file=sys.stderr)
        sys.exit(1)

    keys = collect_keys(interface)
    print(f"interface.json references {len(keys)} translation key(s) across "
          f"{len(languages)} language(s)\n")

    if PRIMARY_LANG not in languages:
        print(f"WARNING: primary language '{PRIMARY_LANG}' not found in "
              f"interface.json's languages — new keys won't get an English "
              f"fallback.\n")

    # Load the primary (English) language first so other languages can fall
    # back to its text for brand-new keys.
    primary_path = (interface_path.parent / languages[PRIMARY_LANG]).resolve() if PRIMARY_LANG in languages else None
    primary_existing: dict[str, str] = {}
    if primary_path and primary_path.exists():
        primary_existing = json.loads(primary_path.read_text(encoding="utf-8"))

    out_of_sync = False
    any_needs_translation = False
    any_file_changed = False

    # Process the primary language first, then the rest — so the "fall back
    # to English" step below sees the primary's *new* placeholders too.
    ordered_langs = ([PRIMARY_LANG] if PRIMARY_LANG in languages else []) + \
                    [l for l in languages if l != PRIMARY_LANG]

    primary_final: dict[str, str] = primary_existing

    for lang in ordered_langs:
        rel_path = languages[lang]
        path = (interface_path.parent / rel_path).resolve()
        if not path.exists():
            print(f"WARNING: {lang}: file not found ({path}), skipping")
            out_of_sync = True
            continue

        existing = json.loads(path.read_text(encoding="utf-8"))
        new_dict, added, removed, needs_translation = sync_language(
            keys, existing, primary_final, lang,
        )

        if lang == PRIMARY_LANG:
            primary_final = new_dict

        reordered = list(existing.keys()) != [k for k in new_dict if k in existing]
        changed = bool(added or removed) or reordered
        meaningfully_changed = bool(added or removed)

        status = []
        if added:
            status.append(f"+{len(added)} added")
        if removed:
            status.append(f"-{len(removed)} removed")
        if not changed:
            status.append("up to date")
        elif reordered and not added and not removed:
            status.append("reordered only")
        try:
            shown_path = path.relative_to(REPO_ROOT)
        except ValueError:
            shown_path = path
        print(f"[{lang}] {', '.join(status)}  ({shown_path})")

        for key in added:
            print(f"    + {key}  (needs translation)")
        for key in removed:
            print(f"    - {key}  (no longer referenced)")

        if needs_translation:
            any_needs_translation = True

        if meaningfully_changed:
            out_of_sync = True
        if changed:
            any_file_changed = True
            if not args.check and not args.dry_run:
                path.write_text(
                    json.dumps(new_dict, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

    print()
    if args.check:
        if out_of_sync:
            print("Out of sync — run without --check to fix.")
            sys.exit(1)
        print("All language files are in sync with interface.json.")
        return

    if args.dry_run:
        print("Dry run — no files were changed." if any_file_changed else "Nothing to change.")
        return

    if any_file_changed:
        print("Language files updated.")
    else:
        print("All language files were already in sync — nothing changed.")

    if any_needs_translation:
        print("Some keys were seeded with English text or a [TODO] placeholder "
              "and still need real translation — see the 'translate' lines above.")


if __name__ == "__main__":
    main()
