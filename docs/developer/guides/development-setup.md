# Development setup

## Requirements

- **Python 3.14** (`requires-python = ">=3.14"` in `pyproject.toml`). [uv](https://docs.astral.sh/uv/getting-started/installation/) installs it for you.
- **uv**, which manages the virtual environment and runs everything below.
- **7-Zip**, the same as for users, if you run the tasks that create or extract archives for real.

## Get the code running

```
uv sync                         # installs the package (editable) and the dev group (pytest)
uv run tools/download_gui.py    # downloads the MXU-KKAFIO GUI release as KKAFIO.exe next to kkafio_cli.py
```

Open `KKAFIO.exe`. It finds `kkafio_cli.py` in its own folder and runs it with `python -u` when there is no
`kkafio_cli.exe` (see [The MXU GUI](../architecture/mxu-gui.md#how-the-rust-side-finds-and-runs-the-cli)). You can
also skip the GUI and run tasks from the command line.

## Running from source

The package uses a `src/` layout. After `uv sync` it is installed in editable
mode, so any of these work:

```
uv run kkafio <command>            # console script (kkafio.cli:main)
uv run python -m kkafio <command>
uv run python kkafio_cli.py <command>   # the launcher; also works without installing
```

Imports are always `kkafio.<subpackage>.<module>` (for example
`from kkafio.core.logger import logger`). Shipped data files are found through
`kkafio.core.paths` — never by walking up from `__file__`.

!!! warning "Running from source uses your real configuration"

    Nothing is sandboxed when you run KKAFIO yourself: it reads and writes your real
    [config directory](../reference/configuration.md#the-config-root-directory) (`%APPDATA%/KKAFIO`),
    including sessions and caches. The tests are different: they redirect it to a temporary folder (see
    [Testing](testing.md#the-sandbox)).

Debug-level log lines are shown when running from source and hidden in packaged builds. Set `KKAFIO_DEBUG=1`
to see them in a packaged build, or `KKAFIO_DEBUG=0` to silence them from source.

## Maintainer tools

Standalone scripts in `tools/` that `kkafio_cli` never imports:

| Script | What it does |
|---|---|
| `tools/generate_config.py` | Regenerates `src/kkafio/core/config.py` from `interface.json`. See [interface.json](../reference/interface-json.md#pipeline_override-doesnt-do-what-it-looks-like-it-does). |
| `tools/sync_i18n_keys.py` | Adds and removes keys in `assets/i18n/*.json` to match `interface.json`. See [interface.json](../reference/interface-json.md#translations-languages). |
| `tools/build_default_coordinate.py` | Records a folder of default coordinates for Group Coordinates. See [Default coordinates](../reference/data-formats/default-coordinates.md). |
| `tools/download_gui.py` | Downloads the MXU-KKAFIO GUI release and saves it as `KKAFIO.exe`. Options: `--arch`, `--tag`, `--prerelease`. |

## Before you push

```
uv run pytest
python tools/generate_config.py --check
python tools/sync_i18n_keys.py --check
```

The tests, and the two generators' `--check` mode, exit non-zero when something is out of date. The release
workflow (`build.yml`) only builds the executable: it does not run any of the three, so run them yourself.

## Working on the documentation

The documentation site is built with [Zensical](https://zensical.org/) from `docs/`, configured in `zensical.toml`.

```
uvx zensical serve     # live preview at http://localhost:8000
uvx zensical build     # static site into ./site (ignored by git)
```

- The navigation is listed by hand in `zensical.toml`: a new page does not appear until you add it there.
- `docs/changelog.md` includes the repository's `CHANGELOG.md`; edit that file, not the page.
- `.github/workflows/docs.yml` publishes the site to GitHub Pages when `docs/`, `zensical.toml` or
  `CHANGELOG.md` change on a branch it lists. Pages has to be enabled with **GitHub Actions** as its source, and the
  `github-pages` environment must allow deployments from that branch (**Settings → Environments → github-pages**).
- The release package ships `README.md`, `CHANGELOG.md` and `LICENSE` but not `docs/`, so links in the README point at
  the published site rather than at files.
