@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title PromptMosaic installer

call :main
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
    echo Install complete.
    echo Start PromptMosaic with:
    echo   PromptMosaic.bat
) else (
    echo Install failed. Error code: %RESULT%
    echo.
    echo If this window opened and closed immediately before, run this file again
    echo from the extracted PromptMosaic folder, or open PowerShell in this folder
    echo and execute:
    echo   .\install_windows.bat
)
echo.
if not defined PROMPTMOSAIC_NO_PAUSE pause
exit /b %RESULT%

:main
echo PromptMosaic Windows installer
echo.

if not exist "requirements.txt" (
    echo requirements.txt was not found.
    echo.
    echo This installer must be run from the full PromptMosaic folder.
    echo Download the whole repository ZIP or clone the repository, extract it,
    echo then run install_windows.bat inside that folder.
    exit /b 2
)

if not exist "main.py" (
    echo main.py was not found.
    echo This does not look like the PromptMosaic application folder.
    exit /b 2
)

rem Build a regular Python venv while ignoring any active Conda/Anaconda env.
set "CONDA_PREFIX="
set "CONDA_DEFAULT_ENV="
set "PYTHONHOME="
set "PYTHONPATH="
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0;%LocalAppData%\Programs\Python\Launcher"

set "PYLAUNCH="
for %%V in (3.11 3.12 3.10) do (
    if not defined PYLAUNCH (
        py -%%V -c "import sys; p=sys.executable.lower(); raise SystemExit(0 if all(x not in p for x in ('conda','anaconda','miniconda')) else 1)" >nul 2>nul
        if not errorlevel 1 set "PYLAUNCH=py -%%V"
    )
)

if not defined PYLAUNCH (
    echo Could not find a regular Python installation via the Windows Python Launcher.
    echo Install Python 3.11 from https://www.python.org/downloads/windows/
    echo Make sure "Install launcher for all users" is enabled, then run this file again.
    exit /b 1
)

if exist ".venv\pyvenv.cfg" (
    findstr /i "conda anaconda miniconda" ".venv\pyvenv.cfg" >nul
    if not errorlevel 1 (
        echo Existing .venv was created from Conda/Anaconda.
        echo Delete .venv and run install_windows.bat again:
        echo   rmdir /s /q .venv
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv with %PYLAUNCH% ...
    %PYLAUNCH% -m venv .venv
    if errorlevel 1 exit /b 1
)

findstr /i "conda anaconda miniconda" ".venv\pyvenv.cfg" >nul
if not errorlevel 1 (
    echo The created .venv points to Conda/Anaconda. Aborting.
    exit /b 1
)

set "PATH=%CD%\.venv\Scripts;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

exit /b 0
