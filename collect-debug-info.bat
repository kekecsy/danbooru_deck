@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  一键收集诊断信息（Windows）
REM  输出：logs\debug-info.txt
REM ============================================================

cd /d "%~dp0"

if not exist "logs" mkdir "logs"
set "OUT=%CD%\logs\debug-info.txt"

> "%OUT%" echo Danbooru Deck debug info
>> "%OUT%" echo Generated: %date% %time%
>> "%OUT%" echo Root: %CD%
>> "%OUT%" echo.

call :section "System"
call :run ver
>> "%OUT%" echo PROCESSOR_ARCHITECTURE=%PROCESSOR_ARCHITECTURE%
>> "%OUT%" echo PROCESSOR_ARCHITEW6432=%PROCESSOR_ARCHITEW6432%
>> "%OUT%" echo.

call :section "Node / npm"
call :run where node
call :run node --version
call :run where npm
call :run npm --version
call :run npm config get registry
>> "%OUT%" echo ELECTRON_MIRROR=%ELECTRON_MIRROR%
>> "%OUT%" echo.

call :section "Python / optional tools"
call :run where python
call :run python --version
call :run where py
call :run py -3 --version
call :run where uv
call :run uv --version
call :run where ffmpeg
call :run ffmpeg -version

call :section "Project files"
if exist ".env" (
    >> "%OUT%" echo .env: exists, content hidden
) else (
    >> "%OUT%" echo .env: missing
)
if exist "env_config.json" (
    >> "%OUT%" echo env_config.json: exists, content hidden
) else (
    >> "%OUT%" echo env_config.json: missing
)
if exist "desktop-app\package.json" (
    >> "%OUT%" echo desktop-app\package.json: exists
) else (
    >> "%OUT%" echo desktop-app\package.json: missing
)
if exist "desktop-app\package-lock.json" (
    >> "%OUT%" echo desktop-app\package-lock.json: exists
) else (
    >> "%OUT%" echo desktop-app\package-lock.json: missing
)
>> "%OUT%" echo.

if exist "desktop-app\package.json" (
    pushd desktop-app

    call :section "Electron package"
    call :run node -p "require('./package.json').devDependencies.electron"
    if exist "package-lock.json" (
        call :run node -p "((p=require('./package-lock.json')).packages && p.packages['node_modules/electron'] ? p.packages['node_modules/electron'].version : '')"
    )
    if exist "node_modules\electron\package.json" (
        call :run node -p "require('./node_modules/electron/package.json').version"
    ) else (
        >> "%OUT%" echo node_modules\electron\package.json: missing
        >> "%OUT%" echo.
    )

    call :section "Electron install state"
    if exist "node_modules\electron" (
        call :run dir "node_modules\electron"
    ) else (
        >> "%OUT%" echo node_modules\electron: missing
        >> "%OUT%" echo.
    )
    if exist "node_modules\electron\dist" (
        call :run dir "node_modules\electron\dist"
    ) else (
        >> "%OUT%" echo node_modules\electron\dist: missing
        >> "%OUT%" echo.
    )
    if exist "node_modules\electron\path.txt" (
        >> "%OUT%" echo path.txt:
        type "node_modules\electron\path.txt" >> "%OUT%" 2>&1
        >> "%OUT%" echo.
    ) else (
        >> "%OUT%" echo path.txt: missing
        >> "%OUT%" echo.
    )
    if exist "node_modules\electron\dist\electron.exe" (
        call :run "node_modules\electron\dist\electron.exe" --version
    ) else (
        >> "%OUT%" echo electron.exe: missing
        >> "%OUT%" echo.
    )

    popd
)

call :section "Electron cache"
if defined LOCALAPPDATA (
    if exist "%LOCALAPPDATA%\electron\Cache" (
        call :run dir "%LOCALAPPDATA%\electron\Cache" /s
    ) else (
        >> "%OUT%" echo %LOCALAPPDATA%\electron\Cache: missing
        >> "%OUT%" echo.
    )
) else (
    >> "%OUT%" echo LOCALAPPDATA is not set.
    >> "%OUT%" echo.
)

call :section "Recent repair log"
if exist "logs\electron-repair.log" (
    where powershell >nul 2>nul
    if errorlevel 1 (
        call :run type "logs\electron-repair.log"
    ) else (
        >> "%OUT%" echo $ powershell -NoProfile -Command "Get-Content logs\electron-repair.log -Tail 120"
        powershell -NoProfile -Command "Get-Content -Path 'logs\electron-repair.log' -Tail 120" >> "%OUT%" 2>&1
        >> "%OUT%" echo.
    )
) else (
    >> "%OUT%" echo logs\electron-repair.log: missing
    >> "%OUT%" echo.
)

echo.
echo 诊断信息已写入：%OUT%
echo 提交 issue 前可以先打开看一眼，确认没有你不想公开的本地路径。
echo.
pause
exit /b 0

:section
>> "%OUT%" echo ============================================================
>> "%OUT%" echo %~1
>> "%OUT%" echo ============================================================
exit /b 0

:run
>> "%OUT%" echo $ %*
%* >> "%OUT%" 2>&1
>> "%OUT%" echo.
exit /b 0
