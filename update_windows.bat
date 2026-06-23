@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title PromptMosaic updater

call :main
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
    echo Update complete.
    echo Start PromptMosaic with:
    echo   PromptMosaic.bat
) else (
    echo Update failed. Error code: %RESULT%
    echo.
    echo Make sure PromptMosaic is closed, then run this file again.
)
echo.
if not defined PROMPTMOSAIC_NO_PAUSE pause
exit /b %RESULT%

:main
echo PromptMosaic Windows updater
echo.

if not exist "main.py" (
    echo main.py was not found.
    echo Run this file inside your existing PromptMosaic folder.
    exit /b 2
)

if not exist "requirements.txt" (
    echo requirements.txt was not found.
    echo Run this file inside your existing PromptMosaic folder.
    exit /b 2
)

echo Keep PromptMosaic closed while updating.
echo User data is stored in the data folder.
echo.

if exist "data" (
    for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%I"
    if not defined STAMP set "STAMP=backup"
    set "BACKUP_DIR=_update_backups\data_!STAMP!"
    echo Backing up data to:
    echo   !BACKUP_DIR!
    robocopy "data" "!BACKUP_DIR!" /E /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 (
        echo Data backup failed.
        exit /b 1
    )
    echo Data backup complete.
) else (
    echo No data folder was found. Skipping data backup.
)

echo.
echo Updating Python environment ...
set "PROMPTMOSAIC_NO_PAUSE=1"
call install_windows.bat
if errorlevel 1 exit /b 1

exit /b 0
