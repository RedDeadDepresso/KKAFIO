@echo off
:: Deletes KKAFIO's saved configuration/session data and the default task
:: folders. This is DESTRUCTIVE and cannot be undone.
::
:: Deletes:
::   %APPDATA%\KKAFIO   - saved MXU config, 7-Zip location cache, Telegram
::                        session/config, download history
::   C:\KKAFIO          - the default task folders: Backups, Downloads,
::                        Archived Cards, Exported Mods
::
:: If you moved any of these elsewhere, or pointed a task at a custom
:: folder instead of the default, this will NOT touch those - only the
:: exact paths above.

echo WARNING: This will permanently delete:
echo   - %APPDATA%\KKAFIO   (saved config, Telegram/session data, download history)
echo   - C:\KKAFIO           (Backups, Downloads, Archived Cards, Exported Mods)
echo.
echo This cannot be undone.
echo.
set /p CONFIRM="Are you sure you want to continue? (y/N): "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)

if exist "%APPDATA%\KKAFIO" (
    rmdir /s /q "%APPDATA%\KKAFIO"
    echo Deleted: %APPDATA%\KKAFIO
) else (
    echo Not found: %APPDATA%\KKAFIO
)

if exist "C:\KKAFIO" (
    rmdir /s /q "C:\KKAFIO"
    echo Deleted: C:\KKAFIO
) else (
    echo Not found: C:\KKAFIO
)

echo.
echo Done.
pause
