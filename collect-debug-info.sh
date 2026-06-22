#!/usr/bin/env bash
# ============================================================
#  一键收集诊断信息（macOS / Linux）
#  输出：logs/debug-info.txt
# ============================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs
OUT="$SCRIPT_DIR/logs/debug-info.txt"

section() {
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

run() {
    echo "$ $*"
    "$@" 2>&1 || true
    echo
}

run_shell() {
    echo "$ $*"
    sh -c "$*" 2>&1 || true
    echo
}

{
    echo "Danbooru Deck debug info"
    echo "Generated: $(date)"
    echo "Root: $SCRIPT_DIR"
    echo

    section "System"
    run uname -a
    if command -v sw_vers >/dev/null 2>&1; then run sw_vers; fi
    if command -v lsb_release >/dev/null 2>&1; then run lsb_release -a; fi
    echo "SHELL=$SHELL"
    echo "HOME=$HOME"
    echo

    section "Node / npm"
    run_shell "command -v node"
    run node --version
    run_shell "command -v npm"
    run npm --version
    run npm config get registry
    echo "ELECTRON_MIRROR=${ELECTRON_MIRROR:-}"
    echo

    section "Python / optional tools"
    run_shell "command -v python3"
    run python3 --version
    run_shell "command -v python"
    run python --version
    run_shell "command -v uv"
    run uv --version
    run_shell "command -v ffmpeg"
    run ffmpeg -version

    section "Project files"
    if [ -f ".env" ]; then
        echo ".env: exists, content hidden"
    else
        echo ".env: missing"
    fi
    if [ -f "env_config.json" ]; then
        echo "env_config.json: exists, content hidden"
    else
        echo "env_config.json: missing"
    fi
    if [ -f "desktop-app/package.json" ]; then
        echo "desktop-app/package.json: exists"
    else
        echo "desktop-app/package.json: missing"
    fi
    if [ -f "desktop-app/package-lock.json" ]; then
        echo "desktop-app/package-lock.json: exists"
    else
        echo "desktop-app/package-lock.json: missing"
    fi
    echo

    if [ -f "desktop-app/package.json" ]; then
        cd desktop-app

        section "Electron package"
        run node -p "require('./package.json').devDependencies.electron"
        if [ -f "package-lock.json" ]; then
            run node -p "((p=require('./package-lock.json')).packages && p.packages['node_modules/electron'] ? p.packages['node_modules/electron'].version : '')"
        fi
        if [ -f "node_modules/electron/package.json" ]; then
            run node -p "require('./node_modules/electron/package.json').version"
        else
            echo "node_modules/electron/package.json: missing"
            echo
        fi

        section "Electron install state"
        if [ -d "node_modules/electron" ]; then
            run ls -la node_modules/electron
        else
            echo "node_modules/electron: missing"
            echo
        fi
        if [ -d "node_modules/electron/dist" ]; then
            run ls -la node_modules/electron/dist
        else
            echo "node_modules/electron/dist: missing"
            echo
        fi
        if [ -f "node_modules/electron/path.txt" ]; then
            echo "path.txt:"
            cat node_modules/electron/path.txt
            echo
        else
            echo "path.txt: missing"
            echo
        fi

        if [ -f "node_modules/electron/path.txt" ]; then
            ELECTRON_REL="$(cat node_modules/electron/path.txt 2>/dev/null || true)"
            if [ -n "$ELECTRON_REL" ] && [ -e "node_modules/electron/dist/$ELECTRON_REL" ]; then
                chmod +x "node_modules/electron/dist/$ELECTRON_REL" 2>/dev/null || true
                run "node_modules/electron/dist/$ELECTRON_REL" --version
            else
                echo "Electron executable from path.txt: missing"
                echo
            fi
        fi

        cd "$SCRIPT_DIR"
    fi

    section "Electron cache"
    run_shell "find \"$HOME/.cache/electron\" -maxdepth 3 -type f -name '*.zip' -ls"
    run_shell "find \"$HOME/Library/Caches/electron\" -maxdepth 3 -type f -name '*.zip' -ls"

    section "Recent repair log"
    if [ -f "logs/electron-repair.log" ]; then
        run tail -n 120 logs/electron-repair.log
    else
        echo "logs/electron-repair.log: missing"
        echo
    fi
} > "$OUT" 2>&1

echo
echo "诊断信息已写入：$OUT"
echo "提交 issue 前可以先打开看一眼，确认没有你不想公开的本地路径。"
echo
