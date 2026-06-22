@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  Electron 一键修复脚本（Windows）
REM  适用：Electron failed to install correctly / dist 为空 / path.txt 缺失
REM  日志：logs\electron-repair.log
REM ============================================================

cd /d "%~dp0"

if not exist "logs" mkdir "logs"
set "LOG=%CD%\logs\electron-repair.log"
> "%LOG%" echo Danbooru Deck Electron repair log
>> "%LOG%" echo Started: %date% %time%
>> "%LOG%" echo Root: %CD%
>> "%LOG%" echo.

echo.
echo ============================================================
echo   Electron 一键修复（Windows）
echo ============================================================
echo 日志会写入：%LOG%
echo.

if not exist "desktop-app\package.json" (
    echo [X] 没找到 desktop-app\package.json，请在项目根目录运行本脚本。
    >> "%LOG%" echo [X] desktop-app\package.json not found.
    pause
    exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
    echo [X] 没找到 Node.js。请先安装 Node.js 18+：https://nodejs.org/
    >> "%LOG%" echo [X] node not found.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [X] 找到了 node 但找不到 npm，请重装 Node.js。
    >> "%LOG%" echo [X] npm not found.
    pause
    exit /b 1
)

pushd desktop-app

echo [1/5] 设置 Electron 国内镜像 ...
>> "%LOG%" echo [1/5] ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"

echo [2/5] 清理残缺 Electron 包和下载缓存 ...
>> "%LOG%" echo [2/5] Cleaning node_modules\electron and Electron cache.
if exist "node_modules\electron" rmdir /s /q "node_modules\electron" >> "%LOG%" 2>&1
if defined LOCALAPPDATA (
    if exist "%LOCALAPPDATA%\electron\Cache" rmdir /s /q "%LOCALAPPDATA%\electron\Cache" >> "%LOG%" 2>&1
)

echo [3/5] 重新安装前端依赖（这一步可能需要几分钟）...
>> "%LOG%" echo [3/5] npm install
call npm install >> "%LOG%" 2>&1
if errorlevel 1 (
    echo     npm install 失败，将尝试手动下载并解压 Electron。
    >> "%LOG%" echo npm install failed, fallback to manual extraction.
) else (
    echo     npm install 完成。
)

call :verify_electron
if not errorlevel 1 goto success

echo [4/5] 自动下载并手动解压 Electron ...
>> "%LOG%" echo [4/5] Manual Electron extraction.

set "ELECTRON_VERSION="
if exist "node_modules\electron\package.json" (
    for /f "delims=" %%v in ('node -p "require('./node_modules/electron/package.json').version" 2^>nul') do set "ELECTRON_VERSION=%%v"
)
if not defined ELECTRON_VERSION if exist "package-lock.json" (
    for /f "delims=" %%v in ('node -p "((p=require('./package-lock.json')).packages && p.packages['node_modules/electron'] ? p.packages['node_modules/electron'].version : '')" 2^>nul') do set "ELECTRON_VERSION=%%v"
)
if not defined ELECTRON_VERSION (
    for /f "delims=" %%v in ('node -p "require('./package.json').devDependencies.electron.replace(/^[^0-9]*/, '')" 2^>nul') do set "ELECTRON_VERSION=%%v"
)

if not defined ELECTRON_VERSION (
    echo [X] 无法识别 Electron 版本。请把日志发到 issue。
    >> "%LOG%" echo [X] Could not resolve Electron version.
    goto fail
)

set "NODE_ARCH=x64"
for /f "delims=" %%a in ('node -p "process.arch" 2^>nul') do set "NODE_ARCH=%%a"
if /i not "!NODE_ARCH!"=="x64" if /i not "!NODE_ARCH!"=="arm64" if /i not "!NODE_ARCH!"=="ia32" set "NODE_ARCH=x64"

set "ZIP_NAME=electron-v!ELECTRON_VERSION!-win32-!NODE_ARCH!.zip"
set "ZIP_URL=https://registry.npmmirror.com/-/binary/electron/v!ELECTRON_VERSION!/!ZIP_NAME!"

echo     版本：!ELECTRON_VERSION!
echo     架构：win32-!NODE_ARCH!
echo     下载：!ZIP_URL!
>> "%LOG%" echo Version: !ELECTRON_VERSION!
>> "%LOG%" echo Artifact: !ZIP_NAME!
>> "%LOG%" echo URL: !ZIP_URL!

where curl >nul 2>nul
if errorlevel 1 (
    echo [X] 系统没有 curl，无法自动下载。请安装 Windows 10/11 自带 curl 或手动处理。
    >> "%LOG%" echo [X] curl not found.
    goto fail
)

curl -L --fail -o "electron.zip" "!ZIP_URL!" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [X] 下载 Electron zip 失败。请检查网络，或把日志发到 issue。
    >> "%LOG%" echo [X] curl download failed.
    goto fail
)

where tar >nul 2>nul
if errorlevel 1 (
    echo [X] 系统没有 tar，无法自动解压。Windows 10/11 通常自带 tar。
    >> "%LOG%" echo [X] tar not found.
    goto fail
)

if exist "node_modules\electron\dist" rmdir /s /q "node_modules\electron\dist" >> "%LOG%" 2>&1
mkdir "node_modules\electron\dist" >> "%LOG%" 2>&1
tar -xf "electron.zip" -C "node_modules\electron\dist" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [X] 解压失败。请检查杀毒软件是否拦截 electron.exe。
    >> "%LOG%" echo [X] tar extraction failed.
    goto fail
)

node -e "require('fs').writeFileSync('node_modules/electron/path.txt','electron.exe')" >> "%LOG%" 2>&1
if exist "electron.zip" del /q "electron.zip" >> "%LOG%" 2>&1

call :verify_electron
if not errorlevel 1 goto success

goto fail

:verify_electron
echo [检查] 验证 Electron 本体 ...
>> "%LOG%" echo [verify] Checking Electron files.
if not exist "node_modules\electron\dist\electron.exe" (
    >> "%LOG%" echo [verify] Missing node_modules\electron\dist\electron.exe
    exit /b 1
)
if not exist "node_modules\electron\path.txt" (
    >> "%LOG%" echo [verify] Missing node_modules\electron\path.txt
    exit /b 1
)
"node_modules\electron\dist\electron.exe" --version >> "%LOG%" 2>&1
if errorlevel 1 (
    >> "%LOG%" echo [verify] electron.exe --version failed.
    exit /b 1
)
>> "%LOG%" echo [verify] Electron OK.
exit /b 0

:success
echo [5/5] 修复完成。现在可以重新运行 start.bat。
>> "%LOG%" echo [OK] Repair completed.
popd
echo.
echo 完成。日志位置：%LOG%
pause
exit /b 0

:fail
echo.
echo [X] 自动修复没有成功。
echo     请运行 collect-debug-info.bat，然后把 logs\debug-info.txt 发到 issue。
>> "%LOG%" echo [X] Repair failed.
popd
echo.
echo 日志位置：%LOG%
pause
exit /b 1
