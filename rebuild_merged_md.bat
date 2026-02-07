@echo off
setlocal enabledelayedexpansion

if "%~1"=="" (
  echo Usage:
  echo   1^) Drag output dir that contains task_state.json OR _parts dir OR task_state.json onto rebuild_merged_md.bat
  echo   2^) Or run: rebuild_merged_md.bat ^"E:\output\xxx^"
  echo.
  pause
  exit /b 2
)

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Python venv not found: %PY%
  echo Please create venv and install deps:
  echo   py -3 -m venv .venv
  echo   .venv\Scripts\activate
  echo   pip install -r requirements.txt
  echo.
  pause
  exit /b 2
)

echo Rebuild merged_result.md for: %*
echo.

"%PY%" -m pabble_ocr.tools.rebuild_merged_md %*

echo.
pause
