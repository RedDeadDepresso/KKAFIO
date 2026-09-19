@echo off
setlocal
title KKAFIO Setup

:menu
cls
echo ============================================
echo   KKAFIO Setup
echo ============================================
echo.
echo   1. Create default task folders (C:\KKAFIO\...)
echo   2. Register context menu
echo   3. Unregister context menu
echo   4. Delete KKAFIO config and default task folders
echo   5. Exit
echo.
set "CHOICE="
set /p "CHOICE=Select an option (1-5): "

if "%CHOICE%"=="1" call "%~dp0scripts\create_default_task_folders.bat" & goto menu
if "%CHOICE%"=="2" call "%~dp0scripts\register_context_menu.bat" & goto menu
if "%CHOICE%"=="3" call "%~dp0scripts\unregister_context_menu.bat" & goto menu
if "%CHOICE%"=="4" call "%~dp0scripts\delete_config_and_task_folders.bat" & goto menu
if "%CHOICE%"=="5" exit /b 0

echo.
echo Invalid choice: %CHOICE%
pause
goto menu
