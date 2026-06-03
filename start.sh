#!/usr/bin/env bash
# 启动桌面端（macOS / Linux）—— 终端里执行  ./start.sh
# 与 Windows 版 start.bat 一一对应。
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "desktop-app/node_modules" ]; then
    echo "[X] 还没安装。请先执行  ./setup.sh  完成一键安装"
    echo "    (setup.sh 会优先用 uv 装依赖，没装 uv 会自动回退到 pip)。"
    exit 1
fi
if [ ! -f "env_config.json" ]; then
    echo "[!] 未找到 env_config.json，可能没跑过 setup.sh。"
    echo "    仍然尝试启动，桌面端会自动尝试系统 Python。"
fi

cd desktop-app
npm run dev
