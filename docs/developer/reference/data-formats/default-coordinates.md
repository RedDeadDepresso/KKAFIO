# Default coordinates

Default coordinates say nothing about whose outfit a coordinate is, so Group Coordinates can skip
them per file: a coordinate card that is one of your default coordinates is not matched to any
character, not moved, and not counted when grouping by hair.

Put the default coordinates in a folder (any depth; other PNGs are skipped) and run:

```
python tools/build_default_coordinate.py "C:/KK/default coordinates"
```

This writes `assets/indices/default_coordinate.json` (replacing its previous content). Each
default coordinate is recorded by its outfit digest, the same one Group Coordinates compares, so
a copy that was renamed, moved to another folder or saved with a different picture is still
recognised. Options:

| Option | Effect |
|---|---|
| `--merge` | Add to the existing file instead of replacing it. |
| `--output PATH` | Write somewhere else. |

Delete the file to stop skipping anything. Nothing needs rebuilding: the lookup happens on every run.
