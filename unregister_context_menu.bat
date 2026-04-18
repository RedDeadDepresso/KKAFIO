@echo off
:: Removes KKAFIO from the Explorer right-click menu.
:: Must be run as Administrator.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Please run this script as Administrator.
    pause
    exit /b 1
)

set "REG_FILE=%~dp0KKAFIO_unregister.reg"
(
echo Windows Registry Editor Version 5.00
echo.
echo [-HKEY_CLASSES_ROOT\Directory\shell\KKAFIO]
echo.
echo [-HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO]
) > "%REG_FILE%"

regedit /s "%REG_FILE%"
if %errorLevel% neq 0 (
    echo ERROR: regedit failed. Make sure you are running as Administrator.
    pause
    exit /b 1
)

echo.
echo KKAFIO context menu removed successfully.
pause
