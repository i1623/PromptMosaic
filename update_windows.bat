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
) else if "%RESULT%"=="3" (
    echo Update cancelled.
) else (
    echo Update failed. Error code: %RESULT%
    echo.
    echo Keep this window open and read the message above.
    echo If the message is unclear, send a screenshot of this window.
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

powershell -NoProfile -EncodedCommand WwBDAG8AbgBzAG8AbABlAF0AOgA6AE8AdQB0AHAAdQB0AEUAbgBjAG8AZABpAG4AZwAgAD0AIABbAFMAeQBzAHQAZQBtAC4AVABlAHgAdAAuAFUAVABGADgARQBuAGMAbwBkAGkAbgBnAF0AOgA6AG4AZQB3ACgAKQAKAFsAQwBvAG4AcwBvAGwAZQBdADoAOgBXAHIAaQB0AGUATABpAG4AZQAoACcAWwDobA9hIAAvACAAVwBhAHIAbgBpAG4AZwBdACcAKQAKAFsAQwBvAG4AcwBvAGwAZQBdADoAOgBXAHIAaQB0AGUATABpAG4AZQAoACcAUzBuMPRmsGVvMCAARwBpAHQASAB1AGIAIABuMABnsGVIcmcwojDXMOowLGdTT9UwoTCkMOswkjA4TlQwaDBuf00w22NIMH4wWTACMCcAKQAKAFsAQwBvAG4AcwBvAGwAZQBdADoAOgBXAHIAaQB0AGUATABpAG4AZQAoACcAZABhAHQAYQAgAC8AIAAuAHYAZQBuAHYAIAAvACAALgBnAGkAdAAgAC8AIABfAHUAcABkAGEAdABlAF8AYgBhAGMAawB1AHAAcwAgAG8w5omKMH4wWzCTMAIwJwApAAoAWwBDAG8AbgBzAG8AbABlAF0AOgA6AFcAcgBpAHQAZQBMAGkAbgBlACgAJwBdMIww5U4WWW4wNFhAYmsw6oEGUmcwCVn0ZlcwXzAgAC4AcAB5ACAALwAgAC4AbQBkACAALwAgADt1z1AgAC8AIAAtippb1TChMKQw6zBJe0wwQjCLMDRYCFQBMApO+GZNMH4wXzBvMEpSZJZVMIwwizDvU/2AJ2BMMEIwijB+MFkwAjAnACkACgBbAEMAbwBuAHMAbwBsAGUAXQA6ADoAVwByAGkAdABlAEwAaQBuAGUAKAAnACcAKQAKAFsAQwBvAG4AcwBvAGwAZQBdADoAOgBXAHIAaQB0AGUATABpAG4AZQAoACcAVABoAGkAcwAgAHUAcABkAGEAdABlACAAcgBlAHAAbABhAGMAZQBzACAAdABoAGUAIABhAHAAcABsAGkAYwBhAHQAaQBvAG4AIABmAGkAbABlAHMAIAB3AGkAdABoACAAdABoAGUAIABsAGEAdABlAHMAdAAgAEcAaQB0AEgAdQBiACAAdgBlAHIAcwBpAG8AbgAuACcAKQAKAFsAQwBvAG4AcwBvAGwAZQBdADoAOgBXAHIAaQB0AGUATABpAG4AZQAoACcAVABoAGUAIABkAGEAdABhACAALwAgAC4AdgBlAG4AdgAgAC8AIAAuAGcAaQB0ACAALwAgAF8AdQBwAGQAYQB0AGUAXwBiAGEAYwBrAHUAcABzACAAZgBvAGwAZABlAHIAcwAgAGEAcgBlACAAcAByAGUAcwBlAHIAdgBlAGQALgAnACkACgBbAEMAbwBuAHMAbwBsAGUAXQA6ADoAVwByAGkAdABlAEwAaQBuAGUAKAAnAEEAbgB5ACAAcABlAHIAcwBvAG4AYQBsACAAYwBoAGEAbgBnAGUAcwAgAG8AdQB0AHMAaQBkAGUAIAB0AGgAbwBzAGUAIABwAHIAbwB0AGUAYwB0AGUAZAAgAGYAbwBsAGQAZQByAHMAIABtAGEAeQAgAGIAZQAgAG8AdgBlAHIAdwByAGkAdAB0AGUAbgAgAG8AcgAgAHIAZQBtAG8AdgBlAGQALgAnACkA
echo.
choice /C YN /M "Continue update"
if errorlevel 2 exit /b 3

echo.
if exist "data" (
    choice /C YN /M "Back up the data folder first"
    if errorlevel 2 (
        echo Skipping data backup.
    ) else (
        call :backup_data
        if errorlevel 1 exit /b 1
    )
) else (
    echo No data folder was found. Skipping data backup.
)

echo.
echo Downloading and replacing application files ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=(Resolve-Path -LiteralPath '.').Path; $tmp=Join-Path $env:TEMP ('PromptMosaic_update_' + [guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $tmp | Out-Null; try { $zip=Join-Path $tmp 'PromptMosaic-main.zip'; Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/i1623/PromptMosaic/archive/refs/heads/main.zip' -OutFile $zip; Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force; $src=Join-Path $tmp 'PromptMosaic-main'; if (!(Test-Path -LiteralPath $src)) { throw 'Extracted PromptMosaic-main folder was not found.' }; $args=@($src,$root,'/MIR','/XD','data','.venv','.git','_update_backups','/XF','update_windows.bat','/NFL','/NDL','/NJH','/NJS','/NP'); & robocopy @args; if ($LASTEXITCODE -ge 8) { throw ('robocopy failed with exit code ' + $LASTEXITCODE) }; exit 0 } finally { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue } }"
if errorlevel 1 (
    echo Application file update failed.
    echo Check your internet connection and free disk space, then run this file again.
    exit /b 1
)

echo.
echo Updating Python environment ...
set "PROMPTMOSAIC_NO_PAUSE=1"
call install_windows.bat
if errorlevel 1 exit /b 1

exit /b 0

:backup_data
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%I"
if not defined STAMP set "STAMP=backup"
set "BACKUP_DIR=_update_backups\data_!STAMP!"
echo Backing up data to:
echo   !BACKUP_DIR!
robocopy "data" "!BACKUP_DIR!" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo Data backup failed.
    echo You can choose No for the backup question if disk space is tight.
    exit /b 1
)
echo Data backup complete.
exit /b 0
