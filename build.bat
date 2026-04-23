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
rmdir /s /q dist 2>nul

REM ── Build kkafio_cli.exe ───────────────────────────────
echo [INFO] Building kkafio_cli.exe...
uv run pyinstaller --noconfirm --onedir ^
--name "kkafio_cli" ^
--hidden-import "util.config" ^
--hidden-import "util.logger" ^
--hidden-import "util.file_manager" ^
--hidden-import "tasks.install_chara" ^
--hidden-import "tasks.remove_chara" ^
--hidden-import "tasks.fc_kks" ^
--hidden-import "tasks.create_backup" ^
kkafio_cli.py

if %errorlevel% neq 0 exit /b 1

REM ── Build KKAFIO.exe ───────────────────────────────────
echo [INFO] Building KKAFIO.exe...
uv run pyinstaller --noconfirm --onedir --windowed ^
--name "KKAFIO" ^
--icon "gui/resource/images/logo.ico" ^
--add-data "gui/resource;gui/resource" ^
--collect-data "qfluentwidgets" ^
--collect-data "certifi" ^
--copy-metadata "kkafio" ^
--hidden-import "PySide6.QtSvg" ^
--hidden-import "qfluentwidgets" ^
--hidden-import "psutil" ^
KKAFIO.py

if %errorlevel% neq 0 exit /b 1

REM ── Assemble release folder ────────────────────────────
echo [INFO] Assembling release folder...
copy "dist\kkafio_cli\kkafio_cli.exe" "dist\KKAFIO\kkafio_cli.exe"

xcopy "dist\kkafio_cli_internal*" "dist\KKAFIO_internal" /E /I /Y

copy "register_context_menu.bat"   "dist\KKAFIO"
copy "unregister_context_menu.bat" "dist\KKAFIO"

REM ── Copy SSL DLLs ──────────────────────────────────────
echo [INFO] Copying SSL DLLs...
for /f %%i in ('uv run python -c "import sys; print(sys.base_prefix)"') do set PYBASE=%%i

set DLLS=%PYBASE%\DLLs
set INTERNAL=dist\KKAFIO_internal
set ROOT=dist\KKAFIO

for %%T in ("%INTERNAL%" "%ROOT%") do (
copy "%DLLS%_ssl.pyd" %%T /Y
copy "%DLLS%\libcrypto-3-x64.dll" %%T /Y
copy "%DLLS%\libssl-3-x64.dll" %%T /Y
)

REM ── Verify SSL DLLs ────────────────────────────────────
echo [INFO] Verifying SSL DLLs...
for %%F in (
"dist\KKAFIO_internal\_ssl.pyd"
"dist\KKAFIO_internal\libcrypto-3-x64.dll"
"dist\KKAFIO_internal\libssl-3-x64.dll"
"dist\KKAFIO\libcrypto-3-x64.dll"
"dist\KKAFIO\libssl-3-x64.dll"
) do (
if exist %%F (
echo OK: %%F
) else (
echo MISSING: %%F
exit /b 1
)
)

echo ===============================
echo   BUILD COMPLETE
echo ===============================
pause
