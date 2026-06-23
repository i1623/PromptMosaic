@echo off
setlocal
cd /d "%~dp0"
title PromptMosaic

rem Avoid loading Qt DLLs from an active Conda/Anaconda environment.
set "CONDA_PREFIX="
set "CONDA_DEFAULT_ENV="
set "PYTHONHOME="
set "PYTHONPATH="
set "PATH=%CD%\.venv\Scripts;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"

if not exist ".venv\Scripts\python.exe" (
    echo PromptMosaic virtual environment was not found.
    echo Run install_windows.bat first.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo PromptMosaic stopped with an error.
    echo If the message mentions QtGui or DLL load failed, run install_windows.bat
    echo after installing the regular Python 3.11 from python.org.
    echo.
    pause
)

endlocal
