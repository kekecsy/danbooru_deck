@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  一键安装脚本 —— 双击运行即可
REM  做的事：
REM    1. 检查 Python / Node.js 是否就绪
REM    2. 优先用 uv 创建 .venv 并装 Python 依赖（更快），没装 uv 则回退到 venv + pip
REM    3. cd desktop-app && npm install && npm run build
REM    4. 生成 .env 模板（用户填 API key）
REM    5. 生成 env_config.json，指向上面创建的 .venv
REM  完成后双击 start.bat 即可启动桌面端。
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo   Danbooru 抓图桌面端 - 一键安装
echo ============================================================
echo.

REM ---------- [1/6] 检查 Python ----------
echo [1/6] 检查 Python ...
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

REM ---------- [2/6] 检查 Node.js ----------
echo [2/6] 检查 Node.js ...
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

REM ---------- [3/6] 检查 uv（推荐用，更快；没有就回退 pip） ----------
echo [3/6] 检查 uv ...
set "USE_UV=0"
where uv >nul 2>nul
if not errorlevel 1 (
    set "USE_UV=1"
    echo     OK: 找到 uv，将用它装依赖（比 pip 快一个数量级）
) else (
    echo     未找到 uv，将回退到标准 venv + pip。
    echo     建议安装 uv 后重跑：
    echo         winget install --id=astral-sh.uv -e
    echo     或参考 https://docs.astral.sh/uv/getting-started/installation/
)

REM ---------- [4/6] 创建 .venv + 装 Python 依赖 ----------
echo [4/6] 创建虚拟环境 .venv 并安装 Python 依赖 ...
if "!USE_UV!"=="1" (
    if not exist ".venv\Scripts\python.exe" (
        uv venv .venv
        if errorlevel 1 (
            echo [X] uv venv 失败。
            pause
            exit /b 1
        )
    )
    uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
    if errorlevel 1 (
        echo [X] uv pip install 失败，请检查上方报错。
        pause
        exit /b 1
    )
) else (
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
)
echo     OK

REM ---------- [5/6] 装前端依赖 + 编译 ----------
echo [5/6] 安装前端依赖并编译 ...
pushd desktop-app
REM Electron 本体二进制默认从 GitHub 下载，国内常失败；指向国内镜像（仅本次安装生效，不污染系统环境）
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
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

REM ---------- [6/6] 生成 .env 模板 + env_config.json ----------
echo [6/6] 生成配置文件 ...
if not exist ".env" (
    > .env (
        echo google_api_key=
        echo openrouter_api_key=
    )
    echo     OK -^> 已生成 .env，请填入你的 API key
) else (
    echo     OK -^> 已存在 .env，跳过（不覆盖你已填的 key）
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
REM JSON 里的反斜杠要转义成 \\
set "VENV_PY_JSON=!VENV_PY:\=\\!"
> env_config.json (
    echo {
    echo     "python_path": "!VENV_PY_JSON!"
    echo }
)
echo     OK -^> env_config.json: !VENV_PY!

echo.
echo ============================================================
echo   安装完成！双击 start.bat 启动桌面端。
echo ============================================================
echo.
echo 备注：
echo   * 请打开项目根目录的 .env，填入 openrouter_api_key 和 google_api_key
echo     （留空也能用，但角色翻译等在线功能会失效）
echo   * ZIP -^> GIF 转换需要 FFMPEG（可选）。需要时执行：
echo       winget install Gyan.FFmpeg
echo     或从 https://ffmpeg.org/download.html 下载并加入 PATH。
if "!USE_UV!"=="0" (
    echo   * 这次用的是 pip。装 uv 后重跑会快很多：
    echo       winget install --id=astral-sh.uv -e
)
echo.
pause
