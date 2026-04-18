@echo off
:: Registers KKAFIO in the Explorer right-click menu for the current user.
:: This part is outside parentheses, so double colons are fine here.

set "EXE=%~dp0kkafio_cli.exe"

if not exist "%EXE%" (
    echo ERROR: kkafio_cli.exe not found in %~dp0
    pause
    exit /b 1
)

:: Double backslashes for REG_SZ values inside a .reg file
set "EXE_REG=%EXE:\=\\%"

set "REG_FILE=%TEMP%\KKAFIO_register.reg"

(
echo Windows Registry Editor Version 5.00
echo.

REM --- DIRECTORY (Right-click a folder icon) ---
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO]
echo "MUIVerb"="KKAFIO"
echo "Icon"="\"%EXE_REG%\""
echo "ExtendedSubCommandsKey"="Directory\\shell\\KKAFIO"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell]
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\01InstallChara]
echo @="Install Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\01InstallChara\command]
echo @="\"%EXE_REG%\" install-chara --input \"%%1\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\02RemoveChara]
echo @="Remove Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\02RemoveChara\command]
echo @="\"%EXE_REG%\" remove-chara --input \"%%1\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\03FilterKKS]
echo @="Filter / Convert KKS"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\03FilterKKS\command]
echo @="\"%EXE_REG%\" fc-kks --input \"%%1\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\04RunAll]
echo @="Run All (from config)"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\04RunAll\command]
echo @="\"%EXE_REG%\" run"
echo.

REM --- BACKGROUND (Right-click empty space inside a folder) ---
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO]
echo "MUIVerb"="KKAFIO"
echo "Icon"="\"%EXE_REG%\""
echo "ExtendedSubCommandsKey"="Directory\\Background\\shell\\KKAFIO"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell]
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\01InstallChara]
echo @="Install Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\01InstallChara\command]
echo @="\"%EXE_REG%\" install-chara --input \"%%V\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\02RemoveChara]
echo @="Remove Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\02RemoveChara\command]
echo @="\"%EXE_REG%\" remove-chara --input \"%%V\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\03FilterKKS]
echo @="Filter / Convert KKS"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\03FilterKKS\command]
echo @="\"%EXE_REG%\" fc-kks --input \"%%V\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\04RunAll]
echo @="Run All (from config)"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\04RunAll\command]
echo @="\"%EXE_REG%\" run"
) > "%REG_FILE%"

regedit /s "%REG_FILE%"
if %errorLevel% neq 0 (
    echo ERROR: regedit failed with code %errorLevel%.
    pause
    exit /b 1
)

echo.
echo KKAFIO context menu registered successfully.
echo Note: If it doesn't appear immediately, restart Explorer.
pause