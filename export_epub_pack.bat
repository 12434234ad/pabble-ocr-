@echo off
setlocal

if "%~1"=="" (
  echo Usage:
  echo   1^) Drag task output dir ^(contains merged_result.md^) onto export_epub_pack.bat
  echo   2^) Or run: export_epub_pack.bat ^"E:\output\xxx^"
  echo   3^) Force overwrite existing pack: export_epub_pack.bat ^"E:\output\xxx^" --force
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

echo Export md-epub pack for: %*
echo.

"%PY%" -m pabble_ocr.tools.export_epub_pack %*

echo.
pause
