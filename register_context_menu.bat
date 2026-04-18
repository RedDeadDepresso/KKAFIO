@echo off
:: Generates KKAFIO_register.reg using the folder this .bat lives in,
:: then imports it. Must be run as Administrator.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Please run this script as Administrator.
    pause
    exit /b 1
)

set "EXE=%~dp0kkafio_cli.exe"

:: Verify the exe exists next to this bat
if not exist "%EXE%" (
    echo ERROR: kkafio_cli.exe not found in %~dp0
    pause
    exit /b 1
)

:: Double the backslashes for REG_SZ values
set "EXE_REG=%EXE:\=\\%"

:: Write the .reg file
set "REG_FILE=%~dp0KKAFIO_register.reg"
(
echo Windows Registry Editor Version 5.00
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO]
echo @="KKAFIO"
echo "MUIVerb"="KKAFIO"
echo "Icon"="\"%EXE_REG%\""
echo "SubCommands"=""
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell]
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell\01InstallChara]
echo @="Install Chara"
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell\01InstallChara\command]
echo @="\"%EXE_REG%\" install-chara --input \"%%1\""
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell\02RemoveChara]
echo @="Remove Chara"
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell\02RemoveChara\command]
echo @="\"%EXE_REG%\" remove-chara --input \"%%1\""
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell\03FilterKKS]
echo @="Filter / Convert KKS"
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell\03FilterKKS\command]
echo @="\"%EXE_REG%\" fc-kks --input \"%%1\""
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell\04RunAll]
echo @="Run All (from config)"
echo.
echo [HKEY_CLASSES_ROOT\Directory\shell\KKAFIO\shell\04RunAll\command]
echo @="\"%EXE_REG%\" run"
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO]
echo @="KKAFIO"
echo "MUIVerb"="KKAFIO"
echo "Icon"="\"%EXE_REG%\""
echo "SubCommands"=""
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell]
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell\01InstallChara]
echo @="Install Chara"
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell\01InstallChara\command]
echo @="\"%EXE_REG%\" install-chara --input \"%%V\""
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell\02RemoveChara]
echo @="Remove Chara"
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell\02RemoveChara\command]
echo @="\"%EXE_REG%\" remove-chara --input \"%%V\""
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell\03FilterKKS]
echo @="Filter / Convert KKS"
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell\03FilterKKS\command]
echo @="\"%EXE_REG%\" fc-kks --input \"%%V\""
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell\04RunAll]
echo @="Run All (from config)"
echo.
echo [HKEY_CLASSES_ROOT\Directory\Background\shell\KKAFIO\shell\04RunAll\command]
echo @="\"%EXE_REG%\" run"
) > "%REG_FILE%"

regedit /s "%REG_FILE%"
if %errorLevel% neq 0 (
    echo ERROR: regedit failed. Make sure you are running as Administrator.
    pause
    exit /b 1
)

echo.
echo KKAFIO context menu registered successfully.
echo Right-click any folder in Explorer to see it.
echo.
echo To remove it, run unregister_context_menu.bat as Administrator.
pause
