@echo off
:: Creates the default folders that interface.json ships as option defaults:
::   Create Backup           -> C:\KKAFIO\Backups
::   Download Contents       -> C:\KKAFIO\Downloads
::   Filter & Convert KKS,
::   Filter Duplicate Contents,
::   Install Contents,
::   Uninstall Contents      -> C:\KKAFIO\Downloads
::   Archive Cards           -> C:\KKAFIO\Archived Cards
::   Export Mods             -> C:\KKAFIO\Exported Mods
::
:: These are created automatically the first time a task actually runs with
:: its default path unchanged, so running this is optional - it just lets
:: you set the folders up (e.g. to drop files into Downloads) before running
:: anything.

set "ROOT=C:\KKAFIO"

for %%D in ("Backups" "Downloads" "Archived Cards" "Exported Mods") do (
    if not exist "%ROOT%\%%~D" (
        mkdir "%ROOT%\%%~D"
        echo Created: %ROOT%\%%~D
    ) else (
        echo Already exists: %ROOT%\%%~D
    )
)

echo.
echo Done.
pause
