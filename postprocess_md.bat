@echo off
setlocal

if "%~1"=="" (
  echo Usage:
  echo   Drag a .md file onto postprocess_md.bat
  echo   Or run: postprocess_md.bat "D:\path\to\xxx.md"
  echo.
  pause
  exit /b 2
)

set "MD=%~1"
if not exist "%MD%" (
  echo MD not found: %MD%
  pause
  exit /b 2
)

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Python venv not found: %PY%
  echo Please create venv and install deps first.
  pause
  exit /b 2
)

REM Default: base-dir is md directory
set "BASE=%~dp1"

REM Auto-detect common output layouts
if exist "%BASE%imgs\img_in_image_box_*.*" (
  REM ok
) else if exist "%BASE%..\imgs\img_in_image_box_*.*" (
  set "BASE=%BASE%..\\"
) else if exist "%BASE%_parts\imgs\img_in_image_box_*.*" (
  set "BASE=%BASE%_parts\\"
)

echo Processing md: %MD%
echo Base dir: %BASE%
echo.

"%PY%" -m pabble_ocr.tools.postprocess_markdown_images "%MD%" --base-dir "%BASE%" --inplace
echo.
pause

