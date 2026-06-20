@echo off
setlocal
cd /d "%~dp0"

rem Avoid loading Qt DLLs from an active Conda/Anaconda environment.
set "CONDA_PREFIX="
set "CONDA_DEFAULT_ENV="
set "PYTHONHOME="
set "PYTHONPATH="
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0;%CD%\.venv\Scripts;%PATH%"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
) else (
    python main.py
)

endlocal
