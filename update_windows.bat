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
call :update_app_files
if errorlevel 1 exit /b 1

echo.
echo Updating Python environment ...
set "PROMPTMOSAIC_NO_PAUSE=1"
call install_windows.bat
if errorlevel 1 exit /b 1

exit /b 0

:update_app_files
if exist ".git" (
    where git >nul 2>nul
    if errorlevel 1 (
        echo This folder looks like a Git checkout, but git.exe was not found.
        echo Install Git for Windows, or update by downloading the ZIP manually.
        exit /b 1
    )
    echo Updating application files with git pull ...
    git pull --ff-only
    if errorlevel 1 (
        echo git pull failed.
        echo If PromptMosaic files were edited manually, save them elsewhere and try again.
        exit /b 1
    )
    exit /b 0
)

echo This folder is not a Git checkout.
echo Downloading the latest PromptMosaic ZIP from GitHub ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=(Resolve-Path -LiteralPath '.').Path; $tmp=Join-Path $env:TEMP ('PromptMosaic_update_' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $tmp | Out-Null; try { $zip=Join-Path $tmp 'PromptMosaic-main.zip'; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/i1623/PromptMosaic/archive/refs/heads/main.zip' -OutFile $zip; Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force; $src=Join-Path $tmp 'PromptMosaic-main'; if (!(Test-Path -LiteralPath $src)) { throw 'Extracted PromptMosaic-main folder was not found.' }; $skip=@('data','.venv','_update_backups','.git','update_windows.bat'); Get-ChildItem -LiteralPath $src -Force | Where-Object { $skip -notcontains $_.Name } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $root -Recurse -Force }; } finally { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue } }"
if errorlevel 1 (
    echo Download update failed.
    echo Check your internet connection, or download the ZIP manually from GitHub.
    exit /b 1
)
echo Application files updated.
exit /b 0
