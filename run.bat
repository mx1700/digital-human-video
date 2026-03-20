@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%venv"

if not exist "%VENV_DIR%" (
    echo [First run] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    "%VENV_DIR%\Scripts\pip.exe" install -r "%SCRIPT_DIR%requirements.txt"
)

"%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%run.py" %*
