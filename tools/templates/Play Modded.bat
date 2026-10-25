@echo off
setlocal
set "SETUP_PS1=%~dp0installer\Setup.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SETUP_PS1%" -Action Play %*
set "SETUP_EXIT=%ERRORLEVEL%"
if not "%SETUP_EXIT%"=="0" echo Play failed with exit code %SETUP_EXIT%.
pause
exit /b %SETUP_EXIT%
