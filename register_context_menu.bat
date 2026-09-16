@echo off
:: Registers KKAFIO in the Explorer right-click menu for the current user.
:: Does NOT require Administrator.
::
:: This is a thin wrapper: the actual work (removing any existing entries,
:: asking for a language, asking which tasks to show and in what order, and
:: writing the registry keys) is done by register_context_menu.ps1, since
:: PowerShell handles Unicode text (task names with emoji, and non-English
:: languages) far more reliably than a plain batch/.reg file can.

set "EXE=%~dp0kkafio_cli.exe"
set "GUI=%~dp0KKAFIO.exe"
set "PS1=%~dp0register_context_menu.ps1"

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
if not exist "%PS1%" (
    echo ERROR: register_context_menu.ps1 not found in %~dp0
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Exe "%EXE%" -Gui "%GUI%"
if %errorLevel% neq 0 (
    echo ERROR: register_context_menu.ps1 failed with code %errorLevel%.
    pause
    exit /b 1
)
