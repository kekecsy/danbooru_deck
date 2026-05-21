@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  一键安装脚本 —— 双击运行即可
REM  做的事：
REM    1. 检查 Python / Node.js 是否就绪
REM    2. 在项目根目录创建 .venv 虚拟环境
REM    3. pip install -r requirements.txt
REM    4. cd desktop-app && npm install && npm run build
REM    5. 生成 env_config.json，指向上面创建的 .venv
REM  完成后双击 start.bat 即可启动桌面端。
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo   Danbooru 抓图桌面端 - 一键安装
echo ============================================================
echo.

REM ---------- [1/5] 检查 Python ----------
echo [1/5] 检查 Python ...
set "PYTHON_CMD="
where python >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
) else (
    where py >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if "!PYTHON_CMD!"=="" (
    echo.
    echo [X] 没找到 Python。请先安装 Python 3.9+（勾选 "Add to PATH"）：
    echo     https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
echo     OK: !PYTHON_CMD!

REM ---------- [2/5] 检查 Node.js ----------
echo [2/5] 检查 Node.js ...
where node >nul 2>nul
if errorlevel 1 (
    echo.
    echo [X] 没找到 Node.js。请先安装 Node.js 18+：
    echo     https://nodejs.org/
    echo.
    pause
    exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
    echo [X] 找到了 node 但找不到 npm，请重装 Node.js。
    pause
    exit /b 1
)
echo     OK

REM ---------- [3/5] 创建 .venv + 装 Python 依赖 ----------
echo [3/5] 创建虚拟环境 .venv 并安装 Python 依赖 ...
if not exist ".venv\Scripts\python.exe" (
    !PYTHON_CMD! -m venv .venv
    if errorlevel 1 (
        echo [X] 创建 .venv 失败。
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [X] pip install 失败，请检查上方报错。
    pause
    exit /b 1
)
echo     OK

REM ---------- [4/5] 装前端依赖 + 编译 ----------
echo [4/5] 安装前端依赖并编译 ...
pushd desktop-app
if not exist "node_modules" (
    call npm install
    if errorlevel 1 (
        echo [X] npm install 失败。
        popd
        pause
        exit /b 1
    )
)
call npm run build
if errorlevel 1 (
    echo [X] npm run build 失败。
    popd
    pause
    exit /b 1
)
popd
echo     OK

REM ---------- [5/5] 写 env_config.json ----------
echo [5/5] 写入 env_config.json ...
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
REM JSON 里的反斜杠要转义成 \\
set "VENV_PY_JSON=!VENV_PY:\=\\!"
> env_config.json (
    echo {
    echo     "python_path": "!VENV_PY_JSON!"
    echo }
)
echo     OK -^> !VENV_PY!

echo.
echo ============================================================
echo   安装完成！双击 start.bat 启动桌面端。
echo ============================================================
echo.
echo 备注：
echo   * ZIP -^> GIF 转换需要 FFMPEG（可选）。需要时执行：
echo       winget install Gyan.FFmpeg
echo     或从 https://ffmpeg.org/download.html 下载并加入 PATH。
echo.
pause
