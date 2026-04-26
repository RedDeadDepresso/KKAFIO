@echo off
setlocal enabledelayedexpansion

echo ===============================
echo   KKAFIO Local Build Script
echo ===============================

REM ── Ensure uv is installed ─────────────────────────────
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] uv is not installed. Install it first:
    echo https://docs.astral.sh/uv/
    exit /b 1
)

REM ── Install Python via uv ──────────────────────────────
echo [INFO] Installing Python...
uv python install

REM ── Install dependencies (no dev) ──────────────────────
echo [INFO] Installing dependencies...
uv sync --no-dev --group build

REM ── Clean previous builds ──────────────────────────────
echo [INFO] Cleaning old builds...
rmdir /s /q build 2>nul
rmdir /s /q dist  2>nul

REM ── Build kkafio_cli.exe ───────────────────────────────
echo [INFO] Building kkafio_cli.exe...
uv run pyinstaller --noconfirm --onedir ^
    --name "kkafio_cli" ^
    --hidden-import "util.config" ^
    --hidden-import "util.logger" ^
    --hidden-import "util.file_manager" ^
    --hidden-import "util.special_tasks" ^
    --hidden-import "tasks.install_chara" ^
    --hidden-import "tasks.remove_chara" ^
    --hidden-import "tasks.fc_kks" ^
    --hidden-import "tasks.create_backup" ^
    --hidden-import "tasks.delete_chara" ^
    --hidden-import "tasks.archive_chara" ^
    --hidden-import "tasks.group_chara" ^
    --hidden-import "tasks.ungroup_chara" ^
    --hidden-import "tasks.filter_duplicates" ^
    --hidden-import "tasks.download_chara" ^
    kkafio_cli.py

if %errorlevel% neq 0 exit /b 1

REM ── Assemble release folder ────────────────────────────
echo [INFO] Assembling release folder...
set SRC=dist\kkafio_cli
set DST=dist\KKAFIO

mkdir "%DST%"

REM Main exe and internals
copy "%SRC%\kkafio_cli.exe" "%DST%\kkafio_cli.exe"
xcopy "%SRC%\_internal\*" "%DST%\_internal\" /E /I /Y

REM Helper scripts
copy "register_context_menu.bat"   "%DST%"
copy "unregister_context_menu.bat" "%DST%"

REM interface.json for MXU
copy "interface.json" "%DST%\interface.json"

REM ── Copy SSL DLLs ──────────────────────────────────────
echo [INFO] Copying SSL DLLs...
for /f %%i in ('uv run python -c "import sys; print(sys.base_prefix)"') do set PYBASE=%%i

set DLLS=%PYBASE%\DLLs
set INTERNAL=%DST%\_internal

for %%T in ("%INTERNAL%" "%DST%") do (
    copy "%DLLS%\_ssl.pyd"            %%T /Y
    copy "%DLLS%\libcrypto-3-x64.dll" %%T /Y
    copy "%DLLS%\libssl-3-x64.dll"    %%T /Y
)

REM ── Verify SSL DLLs ────────────────────────────────────
echo [INFO] Verifying SSL DLLs...
for %%F in (
    "%DST%\_internal\_ssl.pyd"
    "%DST%\_internal\libcrypto-3-x64.dll"
    "%DST%\_internal\libssl-3-x64.dll"
    "%DST%\_ssl.pyd"
    "%DST%\libcrypto-3-x64.dll"
    "%DST%\libssl-3-x64.dll"
) do (
    if exist %%F (
        echo OK: %%F
    ) else (
        echo MISSING: %%F
        exit /b 1
    )
)

echo ===============================
echo   BUILD COMPLETE  ^>  dist\KKAFIO
echo ===============================
echo.
echo To use with MXU: copy dist\KKAFIO\* next to your mxu.exe
pause
