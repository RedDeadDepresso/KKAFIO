@echo off
:: Registers KKAFIO in the Explorer right-click menu for the current user.
:: Does NOT require Administrator.
set "EXE=%~dp0kkafio_cli.exe"
set "GUI=%~dp0KKAFIO.exe"

if not exist "%EXE%" (
    echo ERROR: kkafio_cli.exe not found in %~dp0
    pause
    exit /b 1
)
if not exist "%GUI%" (
    echo ERROR: KKAFIO.exe not found in %~dp0
    pause
    exit /b 1
)

:: Double backslashes for REG_SZ values inside a .reg file
set "EXE_REG=%EXE:\=\\%"
set "GUI_REG=%GUI:\=\\%"

:: cmd /k keeps the window open after the task finishes so the user can read output.
:: Each command is wrapped as: cmd.exe /k ""exe" args"
:: The double outer quotes are required by cmd.exe when the inner string starts with a quote.

set "REG_FILE=%TEMP%\KKAFIO_register.reg"
(
echo Windows Registry Editor Version 5.00
echo.
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
echo @="cmd.exe /k \"\"%EXE_REG%\" install-chara --input \"%%1\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\02UninstallChara]
echo @="Uninstall Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\02UninstallChara\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" uninstall-chara --input \"%%1\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\03FilterKKS]
echo @="Filter / Convert KKS"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\03FilterKKS\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" fc-kks --input \"%%1\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\04FilterDuplicates]
echo @="Filter Duplicates"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\04FilterDuplicates\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" filter-duplicates --input \"%%1\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\05GroupChara]
echo @="Group Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\05GroupChara\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" group-chara --input \"%%1\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\06UngroupChara]
echo @="Ungroup Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\06UngroupChara\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" ungroup-chara --input \"%%1\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\07RunGUI]
echo @="Run GUI"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\shell\KKAFIO\shell\07RunGUI\command]
echo @="\"%GUI_REG%\""
echo.
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
echo @="cmd.exe /k \"\"%EXE_REG%\" install-chara --input \"%%V\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\02UninstallChara]
echo @="Uninstall Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\02UninstallChara\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" uninstall-chara --input \"%%V\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\03FilterKKS]
echo @="Filter / Convert KKS"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\03FilterKKS\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" fc-kks --input \"%%V\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\04FilterDuplicates]
echo @="Filter Duplicates"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\04FilterDuplicates\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" filter-duplicates --input \"%%V\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\05GroupChara]
echo @="Group Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\05GroupChara\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" group-chara --input \"%%V\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\06UngroupChara]
echo @="Ungroup Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\06UngroupChara\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" ungroup-chara --input \"%%V\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\07RunGUI]
echo @="Run GUI"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\KKAFIO\shell\07RunGUI\command]
echo @="\"%GUI_REG%\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\.png\shell\KKAFIO]
echo "MUIVerb"="KKAFIO"
echo "Icon"="\"%EXE_REG%\""
echo "ExtendedSubCommandsKey"="SystemFileAssociations\\.png\\shell\\KKAFIO"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\.png\shell\KKAFIO\shell]
echo.
echo [HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\.png\shell\KKAFIO\shell\01ArchiveChara]
echo @="Archive Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\.png\shell\KKAFIO\shell\01ArchiveChara\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" archive-chara \"%%1\"\""
echo.
echo [HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\.png\shell\KKAFIO\shell\02DeleteChara]
echo @="Delete Chara"
echo.
echo [HKEY_CURRENT_USER\Software\Classes\SystemFileAssociations\.png\shell\KKAFIO\shell\02DeleteChara\command]
echo @="cmd.exe /k \"\"%EXE_REG%\" delete-chara \"%%1\"\""
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
