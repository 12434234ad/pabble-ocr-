@echo off
setlocal enabledelayedexpansion

REM 用法：把 .md 文件拖拽到本脚本上（或在命令行传入 md 路径）
if "%~1"=="" (
  echo 用法：
  echo   1^) 直接把需要处理的 .md 文件拖拽到 postprocess_md.bat 上
  echo   2^) 或执行：postprocess_md.bat ^"D:\path\to\xxx.md^"
  echo.
  pause
  exit /b 2
)

set "MD=%~1"
if not exist "%MD%" (
  echo 找不到 md：%MD%
  pause
  exit /b 2
)

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo 找不到 Python 虚拟环境：%PY%
  echo 请先在当前项目目录创建 .venv 并安装依赖。
  pause
  exit /b 2
)

REM 默认：图片相对路径基于 md 所在目录
set "BASE=%~dp1"

REM 自动探测 imgs 目录（服务端常用输出）
if exist "%BASE%imgs\img_in_image_box_*.*" (
  REM ok
) else if exist "%BASE%..\imgs\img_in_image_box_*.*" (
  set "BASE=%BASE%..\\"
) else if exist "%BASE%_parts\imgs\img_in_image_box_*.*" (
  set "BASE=%BASE%_parts\\"
)

echo 处理 md：%MD%
echo 图片基准目录：%BASE%
echo.

"%PY%" -m pabble_ocr.tools.postprocess_markdown_images "%MD%" --base-dir "%BASE%" --inplace
echo.
pause

