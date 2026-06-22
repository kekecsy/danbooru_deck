#!/usr/bin/env bash
# ============================================================
#  Electron 一键修复脚本（macOS / Linux）
#  适用：Electron failed to install correctly / dist 为空 / path.txt 缺失
#  日志：logs/electron-repair.log
# ============================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs
LOG="$SCRIPT_DIR/logs/electron-repair.log"
: > "$LOG"

log() {
    printf '%s\n' "$*" | tee -a "$LOG"
}

run_logged() {
    "$@" >> "$LOG" 2>&1
}

fail() {
    log ""
    log "[X] 自动修复没有成功。"
    log "    请运行 bash collect-debug-info.sh，然后把 logs/debug-info.txt 发到 issue。"
    log "    日志位置：$LOG"
    exit 1
}

resolve_platform() {
    case "$(uname -s)" in
        Darwin*)
            ELECTRON_PLATFORM="darwin"
            ELECTRON_PATH_TXT="Electron.app/Contents/MacOS/Electron"
            ;;
        Linux*)
            ELECTRON_PLATFORM="linux"
            ELECTRON_PATH_TXT="electron"
            ;;
        *)
            log "[X] 当前系统暂不支持此脚本：$(uname -s)"
            exit 1
            ;;
    esac
}

verify_electron() {
    log "[检查] 验证 Electron 本体 ..."
    if [ ! -f "node_modules/electron/path.txt" ]; then
        echo "[verify] Missing node_modules/electron/path.txt" >> "$LOG"
        return 1
    fi

    local rel_path
    rel_path="$(cat node_modules/electron/path.txt 2>/dev/null || true)"
    if [ -z "$rel_path" ]; then
        rel_path="$ELECTRON_PATH_TXT"
    fi

    if [ ! -x "node_modules/electron/dist/$rel_path" ] && [ ! -f "node_modules/electron/dist/$rel_path" ]; then
        echo "[verify] Missing node_modules/electron/dist/$rel_path" >> "$LOG"
        return 1
    fi

    chmod +x "node_modules/electron/dist/$rel_path" 2>/dev/null || true
    "node_modules/electron/dist/$rel_path" --version >> "$LOG" 2>&1 || return 1
    echo "[verify] Electron OK." >> "$LOG"
    return 0
}

resolve_electron_version() {
    local version=""
    if [ -f "node_modules/electron/package.json" ]; then
        version="$(node -p "require('./node_modules/electron/package.json').version" 2>/dev/null || true)"
    fi
    if [ -z "$version" ] && [ -f "package-lock.json" ]; then
        version="$(node -p "((p=require('./package-lock.json')).packages && p.packages['node_modules/electron'] ? p.packages['node_modules/electron'].version : '')" 2>/dev/null || true)"
    fi
    if [ -z "$version" ]; then
        version="$(node -p "require('./package.json').devDependencies.electron.replace(/^[^0-9]*/, '')" 2>/dev/null || true)"
    fi
    printf '%s' "$version"
}

echo
echo "============================================================"
echo "  Electron 一键修复（macOS / Linux）"
echo "============================================================"
echo "日志会写入：$LOG"
echo

{
    echo "Danbooru Deck Electron repair log"
    echo "Started: $(date)"
    echo "Root: $SCRIPT_DIR"
    echo
} >> "$LOG"

if [ ! -f "desktop-app/package.json" ]; then
    log "[X] 没找到 desktop-app/package.json，请在项目根目录运行本脚本。"
    exit 1
fi

if ! command -v node >/dev/null 2>&1; then
    log "[X] 没找到 Node.js。请先安装 Node.js 18+：https://nodejs.org/"
    exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
    log "[X] 找到了 node 但找不到 npm，请重装 Node.js。"
    exit 1
fi

resolve_platform

cd desktop-app

log "[1/5] 设置 Electron 国内镜像 ..."
export ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
echo "ELECTRON_MIRROR=$ELECTRON_MIRROR" >> "$LOG"

log "[2/5] 清理残缺 Electron 包和下载缓存 ..."
rm -rf node_modules/electron >> "$LOG" 2>&1
rm -rf "$HOME/.cache/electron" "$HOME/Library/Caches/electron" >> "$LOG" 2>&1 || true

log "[3/5] 重新安装前端依赖（这一步可能需要几分钟）..."
if run_logged npm install; then
    log "    npm install 完成。"
else
    log "    npm install 失败，将尝试手动下载并解压 Electron。"
fi

if verify_electron; then
    log "[5/5] 修复完成。现在可以重新运行 ./start.sh。"
    log "日志位置：$LOG"
    exit 0
fi

log "[4/5] 自动下载并手动解压 Electron ..."

ELECTRON_VERSION="$(resolve_electron_version)"
if [ -z "$ELECTRON_VERSION" ]; then
    log "[X] 无法识别 Electron 版本。请把日志发到 issue。"
    fail
fi

NODE_ARCH="$(node -p "process.arch" 2>/dev/null || echo x64)"
case "$NODE_ARCH" in
    x64|arm64|ia32) ;;
    *) NODE_ARCH="x64" ;;
esac

ZIP_NAME="electron-v${ELECTRON_VERSION}-${ELECTRON_PLATFORM}-${NODE_ARCH}.zip"
ZIP_URL="https://registry.npmmirror.com/-/binary/electron/v${ELECTRON_VERSION}/${ZIP_NAME}"

log "    版本：$ELECTRON_VERSION"
log "    平台：${ELECTRON_PLATFORM}-${NODE_ARCH}"
log "    下载：$ZIP_URL"

if command -v curl >/dev/null 2>&1; then
    run_logged curl -L --fail -o electron.zip "$ZIP_URL" || fail
elif command -v wget >/dev/null 2>&1; then
    run_logged wget -O electron.zip "$ZIP_URL" || fail
else
    log "[X] 没找到 curl 或 wget，无法自动下载。"
    fail
fi

rm -rf node_modules/electron/dist >> "$LOG" 2>&1
mkdir -p node_modules/electron/dist

if command -v unzip >/dev/null 2>&1; then
    run_logged unzip -q electron.zip -d node_modules/electron/dist || fail
elif command -v python3 >/dev/null 2>&1; then
    run_logged python3 -m zipfile -e electron.zip node_modules/electron/dist || fail
elif command -v python >/dev/null 2>&1; then
    run_logged python -m zipfile -e electron.zip node_modules/electron/dist || fail
else
    log "[X] 没找到 unzip 或 Python，无法自动解压。"
    fail
fi

printf '%s' "$ELECTRON_PATH_TXT" > node_modules/electron/path.txt
rm -f electron.zip

if verify_electron; then
    log "[5/5] 修复完成。现在可以重新运行 ./start.sh。"
    log "日志位置：$LOG"
    exit 0
fi

fail
