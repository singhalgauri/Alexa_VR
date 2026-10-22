@echo off
REM Voice Gate for Alexa+ - Windows Runner
setlocal

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py %*
) else (
    py -3.10 run.py %* || python run.py %*
)
