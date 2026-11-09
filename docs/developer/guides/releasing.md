# Releasing

Releases are built by `.github/workflows/build.yml` ("Build and Release KKAFIO") on GitHub Actions. This page
describes what it does so you know what a release contains and what to check first.

## Cutting a release

1. Update `CHANGELOG.md`.
2. Set the new version in `pyproject.toml` and `interface.json` (both are edited by hand; the workflow does not check
   that they agree with the tag).
3. Run the [checks](development-setup.md#before-you-push).
4. Push a tag of the form `vX.Y.Z`.

The workflow builds the package, then publishes a GitHub release with generated release notes. A tag containing
`alpha`, `beta`, `rc` or `dev` is published as a pre-release.

## When it runs

| Event | What happens |
|---|---|
| Push of a tag `v*.*.*` | Build, then publish a release. |
| Push to `main`, or any pull request | Build only, when `build.yml`, `*.py`, `src/**` or `pyproject.toml` changed. The package is kept as a workflow artifact for 30 days. |
| Manual run | Build only. |

A build that isn't a tag push gets a version like `v<latest release>-ci.<date>-<commit>`, and is never published.

## What the build does

1. **`meta`** works out the tag, and whether this is a release and a pre-release. It also finds the latest
   release of [MXU-KKAFIO](https://github.com/RedDeadDepresso/MXU-KKAFIO), skipping pre-releases and drafts when
   the run is a stable release, and drafts only otherwise.
2. **`build`** runs on Windows (x86_64):
    1. `uv sync --no-dev --group build` installs the build dependencies (PyInstaller).
    2. It checks that `tkinter` and `customtkinter` import, because the dialogs need them.
    3. It downloads the MXU-KKAFIO release for the platform.
    4. PyInstaller builds the CLI in `--onedir` mode from `kkafio_cli.py`, with `--collect-submodules kkafio` (task
       classes are imported lazily by name, so PyInstaller can't see them) and `--collect-data customtkinter`.
    5. It verifies that the Tcl/Tk libraries and customtkinter's theme made it into the bundle, and fails otherwise.
    6. It assembles the release folder (below), copies the OpenSSL DLLs from the Python installation, converts
       `assets/icon.png` to `kkafio.ico` with ImageMagick, and sets the icon and version strings on `KKAFIO.exe`
       with rcedit.
    7. It zips the folder as `KKAFIO-win-x86_64-<tag>.zip`.
3. **`release`** (tag pushes only) attaches that zip to a GitHub release.

## What is in the package

PyInstaller produces the CLI as `KKAFIO.exe`. The workflow renames it to `kkafio_cli.exe` and copies the downloaded
GUI (`mxu.exe`) in as `KKAFIO.exe`, which is why the GUI looks for `kkafio_cli.exe` next to itself.

| In the zip | Comes from |
|---|---|
| `KKAFIO.exe` | the MXU-KKAFIO release (the GUI) |
| `kkafio_cli.exe` and `_internal/` | PyInstaller |
| `interface.json` | the repository, with `version` set from the tag |
| `assets/`, `scripts/`, `kkafio_setup.bat` | the repository, copied as they are |
| `README.md`, `CHANGELOG.md`, `LICENSE` | the repository |

Shipped data is copied next to the executables rather than bundled into them, and code finds it through
`kkafio.core.paths`. `docs/` is not shipped.

!!! note "The version written into `interface.json` comes from the ref name"

    The step takes the version from `github.ref_name` and strips a leading `v`. On a tag build that is the version
    number; on a branch or pull request build it is the branch name. Only tag builds are meant to be shipped.
