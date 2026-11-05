@echo off
:: Turns on Windows clipboard history (Win+V) for the current user.
::
:: Download Contents and Download Missing Mods can copy the links / mod GUIDs
:: that failed to the clipboard ("If Downloads Fail" option). With clipboard
:: history on, that copy is kept in the Win+V list instead of being replaced
:: by the next thing you copy.
::
:: Needs Windows 10 (version 1809) or newer. No administrator rights needed.

set "KEY=HKCU\Software\Microsoft\Clipboard"

reg add "%KEY%" /v EnableClipboardHistory /t REG_DWORD /d 1 /f >nul
if errorlevel 1 (
    echo Could not enable clipboard history.
    goto done
)
echo Clipboard history is enabled. Press Win+V to open it.

:: A group policy can force it off no matter what the setting above says.
reg query "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v AllowClipboardHistory 2>nul | find "0x0" >nul
if not errorlevel 1 (
    echo.
    echo Note: clipboard history is disabled by a policy on this PC
    echo ^(AllowClipboardHistory = 0^), so it will stay off until that is changed.
)

:done
echo.
pause
