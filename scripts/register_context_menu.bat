@echo off
:: Registers KKAFIO in the Explorer right-click menu for the current user.
:: Does NOT require Administrator.
::
:: This is a thin wrapper. Windows' default PowerShell execution policy
:: blocks .ps1 scripts from running at all (even just double-clicking one
:: does nothing, or errors), so this .bat - which isn't subject to that
:: policy - launches the real script with -ExecutionPolicy Bypass for just
:: this one run. Nothing is changed system-wide.
::
:: The actual work (removing any existing entries, asking for a language,
:: asking which tasks to show and in what order, and writing the registry
:: keys) is done by register_context_menu.ps1, in this same folder.
::
:: This script lives in scripts\, one level below the KKAFIO install root
:: where kkafio_cli.exe and KKAFIO.exe actually are.

set "ROOT=%~dp0.."
set "PS1=%~dp0register_context_menu.ps1"
set "EXE=%ROOT%\kkafio_cli.exe"
set "GUI=%ROOT%\KKAFIO.exe"

if not exist "%PS1%" (
    echo ERROR: register_context_menu.ps1 not found in %~dp0
    pause
    exit /b 1
)
if not exist "%EXE%" (
    echo ERROR: kkafio_cli.exe not found in %ROOT%
    pause
    exit /b 1
)
if not exist "%GUI%" (
    echo ERROR: KKAFIO.exe not found in %ROOT%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Exe "%EXE%" -Gui "%GUI%"
if %errorLevel% neq 0 (
    echo ERROR: register_context_menu.ps1 failed with code %errorLevel%.
    pause
    exit /b 1
)
