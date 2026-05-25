@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "desktop-app\node_modules" (
    echo [X] 还没安装。请先双击 setup.bat 完成一键安装
    echo     ^(setup.bat 会优先用 uv 装依赖，没装 uv 会自动回退到 pip^)。
    pause
    exit /b 1
)
if not exist "env_config.json" (
    echo [!] 未找到 env_config.json，可能没跑过 setup.bat。
    echo     仍然尝试启动，桌面端会自动尝试系统 Python。
)

cd desktop-app
call npm run dev
