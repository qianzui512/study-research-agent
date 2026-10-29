@echo off
REM Force use of the project venv so 'python' never resolves to conda.
REM   run.bat smoke_test.py
REM   run.bat run_agent.py --tools
REM   run.bat run_agent.py "analyze data/sample_grades.csv"
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] venv not found: %PY%
    echo Create it with: python -m venv .venv
    echo Then install:  .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)
"%PY%" %*
exit /b %ERRORLEVEL%
