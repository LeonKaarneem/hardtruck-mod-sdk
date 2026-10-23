@echo off
rem Hard Truck 18 WoS QoL modpack installer (Windows helper).
rem Copies the contents of game\ over your game folder.
rem
rem Usage: INSTALL.bat "C:\Games\Hard Truck 18 WoS"
setlocal
set "SRC=%~dp0game"
set "DEST=%~1"

if "%DEST%"=="" (
    echo Usage: %~nx0 "path-to-game-folder"   (the folder containing prism3d.exe^)
    exit /b 1
)
if not exist "%DEST%\prism3d.exe" (
    echo error: "%DEST%" does not look like the game folder (prism3d.exe not found^).
    exit /b 1
)
if not exist "%DEST%\game" (
    echo error: "%DEST%\game" not found - wrong folder?
    exit /b 1
)

echo This will copy the modpack's game\ contents into:
echo   %DEST%\game
echo.
echo BACKUP FIRST: your existing savegames will NOT load with this mod.
echo Copy your "%DEST%\game\usr\save" folder somewhere safe, or start a new profile.
echo.
set /p REPLY=Continue? [y/N]
if /i not "%REPLY%"=="y" (
    echo Aborted.
    exit /b 1
)

xcopy /e /i /y "%SRC%\*" "%DEST%\game\"
if errorlevel 1 (
    echo Copy failed.
    exit /b 1
)
echo.
echo Done. Next:
echo   1. Edit %DEST%\game\usr\config.cfg (resolution etc.^).
echo   2. Start the game and create a NEW profile.
echo Windows 10/11: consider d3d8to9 (see README.md^) and/or
echo   python patches\patch_dx8_vsync.py "%DEST%\sys\dx8.dll"
endlocal
