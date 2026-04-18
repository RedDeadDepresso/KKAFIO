@echo off
:: Removes KKAFIO from the Explorer right-click menu for the current user.
:: Does NOT require Administrator.

set "REG_FILE=%TEMP%\KKAFIO_unregister.reg"

(
echo Windows Registry Editor Version 5.00
echo.
echo [-HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO]
echo.
echo [-HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO]
) > "%REG_FILE%"

regedit /s "%REG_FILE%"
if %errorLevel% neq 0 (
    echo ERROR: regedit failed with code %errorLevel%.
    pause
    exit /b 1
)

echo.
echo KKAFIO context menu removed successfully.
pause
