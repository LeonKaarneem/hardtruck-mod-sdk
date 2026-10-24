@echo off
setlocal
set "SETUP_PS1=%~dp0installer\Setup.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SETUP_PS1%" %*
set "SETUP_EXIT=%ERRORLEVEL%"
if "%SETUP_EXIT%"=="2" (
    pause
    exit /b 0
)
if not "%SETUP_EXIT%"=="0" (
    echo Setup failed with exit code %SETUP_EXIT%.
    pause
)
exit /b %SETUP_EXIT%
