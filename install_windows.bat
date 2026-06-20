@echo off
setlocal EnableExtensions
cd /d "%~dp0"

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

echo.
echo Install complete.
echo Start PromptMosaic with:
echo   PromptMosaic.bat

endlocal
