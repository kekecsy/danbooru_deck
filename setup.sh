#!/usr/bin/env bash
# ============================================================
#  一键安装脚本（macOS / Linux）—— 终端里执行  ./setup.sh
#  做的事（与 Windows 版 setup.bat 一一对应）：
#    1. 检查 Python3 / Node.js 是否就绪
#    2. 优先用 uv 创建 .venv 并装 Python 依赖（更快），没装 uv 则回退到 venv + pip
#    3. cd desktop-app && npm install && npm run build
#    4. 生成 .env 模板（用户填 API key）
#    5. 生成 env_config.json，指向上面创建的 .venv
#  完成后执行  ./start.sh  即可启动桌面端。
# ============================================================

set -u

# 切到脚本所在目录（保证相对路径正确）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo
echo "============================================================"
echo "  Danbooru 抓图桌面端 - 一键安装（macOS / Linux）"
echo "============================================================"
echo

# ---------- [1/6] 检查 Python ----------
echo "[1/6] 检查 Python ..."
PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi
if [ -z "$PYTHON_CMD" ]; then
    echo
    echo "[X] 没找到 Python。请先安装 Python 3.9+："
    echo "    macOS:  brew install python      （或 https://www.python.org/downloads/）"
    echo "    Linux:  用发行版包管理器装 python3"
    echo
    exit 1
fi
echo "    OK: $PYTHON_CMD ($("$PYTHON_CMD" --version 2>&1))"

# ---------- [2/6] 检查 Node.js ----------
echo "[2/6] 检查 Node.js ..."
if ! command -v node >/dev/null 2>&1; then
    echo
    echo "[X] 没找到 Node.js。请先安装 Node.js 18+："
    echo "    macOS:  brew install node        （或 https://nodejs.org/）"
    echo
    exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
    echo "[X] 找到了 node 但找不到 npm，请重装 Node.js。"
    exit 1
fi
echo "    OK"

# ---------- [3/6] 检查 uv（推荐用，更快；没有就回退 pip） ----------
echo "[3/6] 检查 uv ..."
USE_UV=0
if command -v uv >/dev/null 2>&1; then
    USE_UV=1
    echo "    OK: 找到 uv，将用它装依赖（比 pip 快一个数量级）"
else
    echo "    未找到 uv，将回退到标准 venv + pip。"
    echo "    建议安装 uv 后重跑："
    echo "        brew install uv"
    echo "    或： curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# ---------- [4/6] 创建 .venv + 装 Python 依赖 ----------
echo "[4/6] 创建虚拟环境 .venv 并安装 Python 依赖 ..."
VENV_PY="$SCRIPT_DIR/.venv/bin/python"
if [ "$USE_UV" = "1" ]; then
    if [ ! -x "$VENV_PY" ]; then
        uv venv .venv || { echo "[X] uv venv 失败。"; exit 1; }
    fi
    uv pip install --python "$VENV_PY" -r requirements.txt \
        || { echo "[X] uv pip install 失败，请检查上方报错。"; exit 1; }
else
    if [ ! -x "$VENV_PY" ]; then
        "$PYTHON_CMD" -m venv .venv || { echo "[X] 创建 .venv 失败。"; exit 1; }
    fi
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r requirements.txt \
        || { echo "[X] pip install 失败，请检查上方报错。"; exit 1; }
fi
echo "    OK"

# ---------- [5/6] 装前端依赖 + 编译 ----------
echo "[5/6] 安装前端依赖并编译 ..."
cd desktop-app
# Electron 本体二进制默认从 GitHub 下载，国内常失败；指向国内镜像（仅本脚本进程生效，不污染用户环境）
export ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
if [ ! -d "node_modules" ]; then
    npm install || { echo "[X] npm install 失败。"; cd "$SCRIPT_DIR"; exit 1; }
fi
npm run build || { echo "[X] npm run build 失败。"; cd "$SCRIPT_DIR"; exit 1; }
cd "$SCRIPT_DIR"
echo "    OK"

# ---------- [6/6] 生成 .env 模板 + env_config.json ----------
echo "[6/6] 生成配置文件 ..."
if [ ! -f ".env" ]; then
    cat > .env <<'EOF'
google_api_key=
openrouter_api_key=
EOF
    echo "    OK -> 已生成 .env，请填入你的 API key"
else
    echo "    OK -> 已存在 .env，跳过（不覆盖你已填的 key）"
fi

# macOS / Linux 路径用正斜杠，JSON 里无需转义
cat > env_config.json <<EOF
{
    "python_path": "$VENV_PY"
}
EOF
echo "    OK -> env_config.json: $VENV_PY"

echo
echo "============================================================"
echo "  安装完成！执行  ./start.sh  启动桌面端。"
echo "============================================================"
echo
echo "备注："
echo "  * 请打开项目根目录的 .env，填入 openrouter_api_key 和 google_api_key"
echo "    （留空也能用，但角色翻译等在线功能会失效）"
echo "  * ZIP -> GIF 转换需要 FFMPEG（可选）。需要时执行："
echo "      brew install ffmpeg"
if [ "$USE_UV" = "0" ]; then
    echo "  * 这次用的是 pip。装 uv 后重跑会快很多："
    echo "      brew install uv"
fi
echo
