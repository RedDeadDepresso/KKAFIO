@echo off
setlocal enabledelayedexpansion

echo ===============================
echo    KKAFIO Direct Build Script
echo ===============================

REM --- Clean previous builds ---
echo [INFO] Cleaning dist\KKAFIO...
rmdir /s /q "dist\KKAFIO" 2>nul

REM --- Build directly into dist\KKAFIO ---
REM We use --distpath to set the base folder 
REM and --name to set the specific subfolder name.
echo [INFO] Building...
uv run pyinstaller --noconfirm --onedir ^
 --distpath "dist" ^
 --name "KKAFIO" ^
 --hidden-import "util.config" ^
 --hidden-import "util.logger" ^
 --hidden-import "util.file_manager" ^
 --hidden-import "util.special_tasks" ^
 --hidden-import "tasks.install_chara" ^
 --hidden-import "tasks.uninstall_chara" ^
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

set DST=dist\KKAFIO

REM --- Copy Assets directly to the destination ---
echo [INFO] Adding supplementary files...
if exist "register_context_menu.bat"   copy "register_context_menu.bat"   "%DST%\"
if exist "unregister_context_menu.bat" copy "unregister_context_menu.bat" "%DST%\"
if exist "interface.json"              copy "interface.json"              "%DST%\"

if not exist "%DST%\assets" mkdir "%DST%\assets"
if exist "assets\logo.png" copy "assets\logo.png" "%DST%\assets\logo.png"

REM --- Copy SSL DLLs ---
echo [INFO] Copying SSL DLLs...
for /f "delims=" %%i in ('uv run python -c "import sys; print(sys.base_prefix)"') do set PYBASE=%%i
set DLLS=%PYBASE%\DLLs

copy "%DLLS%\_ssl.pyd"           "%DST%\_internal\" /Y
copy "%DLLS%\libcrypto-3-x64.dll" "%DST%\_internal\" /Y
copy "%DLLS%\libssl-3-x64.dll"    "%DST%\_internal\" /Y
copy "%DLLS%\_ssl.pyd"           "%DST%\" /Y
copy "%DLLS%\libcrypto-3-x64.dll" "%DST%\" /Y
copy "%DLLS%\libssl-3-x64.dll"    "%DST%\" /Y

echo ===============================
echo   SUCCESS: Build at dist\KKAFIO
echo ===============================
pause