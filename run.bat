@echo off
chcp 65001 >nul
title KeyFlow - visual automation
cd /d "%~dp0"

rem ---- local Python environment (.venv): keeps system Python clean ----
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo First run: creating local Python environment .venv ...
  python -m venv .venv
)

if not exist "%PY%" (
  echo ERROR: could not create .venv. Is Python installed and on PATH?
  pause
  exit /b 1
)

rem ---- install core dependencies only when missing (fast when present) ----
"%PY%" -c "import pynput, mss, requests" >nul 2>&1
if errorlevel 1 (
  echo Installing core dependencies into .venv ...
  "%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
)

echo Starting KeyFlow... (keep this console open - it IS the program)
"%PY%" server.py
pause
