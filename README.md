# Danbooru 图片抓取与管理桌面应用

基于 **Electron + Vue 3 + FastAPI** 的桌面端，提供 Danbooru 抓图、本地图库管理、标签翻译和打码编辑等功能。

---

## 📸 界面预览

**抓图主界面** — 左侧配置抓取任务（模式 / 标签 / 起止页 / 日期），右侧本地图库网格；可进入选择模式跨页勾选图片，配合 🗜 加密工具把长 ID 列表压成短串方便分享。

![抓图主界面](overview_pic/sample1.png)

**点击缩略图打开大图查看器** — 信息栏默认悬浮显示（可 📌 固定），支持 ⛶ 原始大小 / ▣ 适应窗口 切换，并提供画师/角色收藏、编辑跳转、上下张切换等。

![大图查看器](overview_pic/sample2.png)

---

## 🚀 快速开始（Windows）

> 需要 [Python 3.9+](https://www.python.org/downloads/) 和 [Node.js 18+](https://nodejs.org/)。
> 推荐使用 [uv](https://docs.astral.sh/uv/)进行包管理：
>
> ```text
> winget install --id=astral-sh.uv -e
> ```
>
> `setup.bat` 会自动识别 uv 并使用；没装也行，会回退到标准 `venv + pip`。

```text
1. 下载/克隆本仓库
2. 双击 setup.bat   ← 一键创建 .venv、装依赖、编译前端（优先 uv，回退 pip）
3. 双击 start.bat   ← 启动桌面端
```

`setup.bat` 的作用：

- 检查 Python / Node.js / uv
- 在项目根目录创建独立 `.venv`，跑 `uv pip install -r requirements.txt`（或 `pip install`）
- `desktop-app/` 里跑 `npm install` + `npm run build`
- 写入 `env_config.json`，把 Python 路径指向上面的 `.venv`

之后只要双击 `start.bat` 即可启动。需要桌面快捷方式的话，对 `start.bat` 右键 → 发送到 → 桌面快捷方式。

---

## 🚀 快速开始（macOS / Linux）

> 需要 [Python 3.9+](https://www.python.org/downloads/) 和 [Node.js 18+](https://nodejs.org/)。
> macOS 推荐先装 [Homebrew](https://brew.sh/)，再用它把依赖一次装齐：
>
> ```bash
> brew install python node uv      # uv 可选，但强烈推荐（装依赖快一个数量级）
> ```
>
> `setup.sh` 会自动识别 uv 并使用；没装也行，会回退到标准 `venv + pip`。

```bash
1. 下载/克隆本仓库
2. cd 到项目目录
3. chmod +x setup.sh start.sh   ← 首次需要给脚本加执行权限
4. ./setup.sh                   ← 创建 .venv、装依赖、编译前端（优先 uv，回退 pip）
5. ./start.sh                   ← 启动桌面端
```

`setup.sh` 的作用（与 Windows 版 `setup.bat` 一一对应）：

- 检查 Python3 / Node.js / uv
- 在项目根目录创建独立 `.venv`，跑 `uv pip install -r requirements.txt`（或 `pip install`）
- `desktop-app/` 里跑 `npm install` + `npm run build`
- 写入 `env_config.json`，把 Python 路径指向上面 `.venv` 里的 `bin/python`

之后只要执行 `./start.sh` 即可启动。

> 不想加执行权限的话，直接 `bash setup.sh` / `bash start.sh` 也能跑。

---

## 主要功能

- **多模式抓取**：排行榜 / 日期热门 / 日期范围热门 / 仅收集 ID / 按 ID 下载 / 按 Tag 下载。
- **本地库管理**：按下载日期归类（`hot_pic/YYYY-MM-DD/`），支持画师 / 角色搜索、score 排序、筛选格式（图片/视频/动图）。
- **热度刷新**：当前页 / 指定范围 / 全部 三种方式刷新 score、收藏数、画师；范围 >5 页时自动每 4 页休 40s 防风控。
- **画师 / 角色收藏**：点 chip 旁的 ★ 可分组收藏，角色按 `source_hint` 自动合并归类；被收藏的卡片在画廊中异色高亮。
- **标签翻译**：英文角色 tag → 中文，支持在线/手动补全；可导入自定义 `json` 词典。
- **ZIP→GIF 转换**（可选，依赖 FFMPEG，见下文）。
- **打码编辑器**：内置全屏查看器 + EditorPage 打码/水印工具。

---

## 自定义配置环境方案（不想用一键脚本时）

### 1. Python 环境

**推荐：uv**

```bash
uv venv .venv
# Windows
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
# macOS / Linux
uv pip install --python .venv/bin/python -r requirements.txt
```

> uv 自动处理 Python 发现、venv 创建、并行下载和 wheel 缓存，第一次完整安装通常在 10 秒级。

其它方式也行：


Anaconda：

```bash
conda create -n danbooru python=3.10
conda activate danbooru
pip install -r requirements.txt
```

venv：

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

### 2. 指定 Python 解释器

桌面端启动时按以下顺序寻找 Python：

1. 项目根目录的 `env_config.json` 中 `python_path` 字段（绝对路径，Windows 注意 JSON 里反斜杠要写成 `\\`）
2. 环境变量 `WEB_MOSAIC_PYTHON`
3. 系统 `PATH` 中的 `python` / `py -3`

`setup.bat`（Windows）/ `setup.sh`（macOS / Linux）会自动生成 `env_config.json`；手动写时格式如下：

Windows（注意 JSON 里反斜杠要写成 `\\`）：

```json
{
    "python_path": "D:\\Anaconda3\\envs\\danbooru\\python.exe"
}
```

macOS / Linux（正斜杠，无需转义）：

```json
{
    "python_path": "/Users/you/web_mosaic_gpt/.venv/bin/python"
}
```

提示：在激活的环境里跑 `where python`（Windows）/ `which python3`（macOS / Linux）取第一行即可。

### 3. 前端

```bash
cd desktop-app
npm install
npm run build      # 生产构建（start.bat 走这条）
npm run dev        # 开发模式（带热重载 + Electron）
```

---

## 可选：FFMPEG（仅 ZIP→GIF 转换需要）

抓图、管理、打码都不需要 FFMPEG。**只有点"转 GIF"按钮时**才会调用它。需要时任选其一：

```bash
winget install Gyan.FFmpeg   # Windows
brew install ffmpeg          # macOS
```

或从 [FFMPEG 官网](https://ffmpeg.org/download.html) 下载，把 `bin/` 加入系统 `PATH`，在终端中验证：

```bash
ffmpeg -version
```

---

## 无法连接 Danbooru？

国内网络偶尔会被运营商劫持，可以改 hosts 绕过。

文件位置：

- Windows：`C:\Windows\System32\drivers\etc\hosts`（用管理员权限编辑）
- macOS / Linux：`/etc/hosts`（`sudo nano /etc/hosts`）

在末尾追加一行：

```text
104.26.11.39 danbooru.donmai.us
```

桌面端右上角的「无法连接？修改Hosts教程」按钮里也有同样的说明。

---

## Electron 装不上 / 报 "Electron failed to install correctly"？

`npm install` 会在 postinstall 阶段从 GitHub 下载约 285MB 的 Electron 本体，国内网络经常失败，导致 `npm run dev` / `start.bat` 报：

```text
Error: Electron failed to install correctly, please delete node_modules/electron and try installing again
```

`setup.bat` / `setup.sh` 已经在跑 `npm install` 前把下载源指向国内镜像（npmmirror），走一键脚本通常不会遇到。手动 `npm install` 的话，自己设一下镜像环境变量再装——**`set` / `export` 只在当前终端有效，务必和 `npm install` 在同一个窗口执行**：

**Windows（cmd）：**

```cmd
cd desktop-app
rmdir /s /q node_modules\electron
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
npm install
```

**macOS / Linux：**

```bash
cd desktop-app
rm -rf node_modules/electron
export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
npm install
```

仍失败的话，多半是 `@electron/get` 缓存了半截的坏文件，清掉再装：

```bash
# Windows
rmdir /s /q "%LOCALAPPDATA%\electron\Cache"
# macOS / Linux
rm -rf ~/.cache/electron ~/Library/Caches/electron
```

想用别的镜像或走默认源，改上面命令里的 `ELECTRON_MIRROR` 值即可（不设这个变量就是默认从 GitHub 下载）。

### 镜像、缓存都弄了还报错？——多半是"下载成功但解压失败"

这是最隐蔽的一种：Electron 本体其实**已经下载下来了**，但解压那一步（Electron 内部用的 `extract-zip`）在部分 Windows 环境会**静默崩溃**——进程不报错、也不写完成标记，常见诱因是杀毒软件实时扫描把正在解压出来的 `electron.exe` / `.dll` 拦截或隔离掉了。表现就是 `npm install` 看着没报错，`npm run dev` 却依旧 `Electron failed to install correctly`，而且 `node_modules\electron\dist\` 里几乎是空的。

先花十秒判断卡在「下载」还是「解压」（在 `desktop-app` 目录下执行）：

```cmd
REM ① 看下载缓存里有没有完整的 zip（约 110~120MB 才算完整）
dir "%LOCALAPPDATA%\electron\Cache" /s /b | findstr .zip

REM ② 看本体有没有解压出来：应有 electron.exe，且 path.txt 应存在
dir node_modules\electron\dist\electron.exe
type node_modules\electron\path.txt
```

- zip **不存在 / 只有几 KB** → 是下载没成功，回到上面「设镜像 + 清缓存」重装即可。
- zip **完整存在**，但 `dist\electron.exe` 或 `path.txt` 缺失 → 就是解压崩了，照下面手动解压。

手动解压（绕开会崩的 `extract-zip`，改用 Windows 自带的 `tar`；整段在 `desktop-app` 目录下执行）：

```cmd
REM 0) 确认实际版本号（下面 URL 要用，注意可能不是 package.json 里写的 ^35.1.5）
type node_modules\electron\package.json | findstr version

REM 1) 手动下载对应版本（curl 是 Win10/11 自带；把 35.7.5 换成上一步看到的版本）
curl -L -o electron.zip "https://registry.npmmirror.com/-/binary/electron/v35.7.5/electron-v35.7.5-win32-x64.zip"

REM 2) 清掉残缺的 dist，重建空目录
rmdir /s /q node_modules\electron\dist
mkdir node_modules\electron\dist

REM 3) 用系统自带 tar 解压（Win10/11 自带 bsdtar，支持 zip）
tar -xf electron.zip -C node_modules\electron\dist

REM 4) 写 path.txt 让 Electron 能定位到本体（用 node 写，避免 echo 带多余换行导致路径出错）
node -e "require('fs').writeFileSync('node_modules/electron/path.txt','electron.exe')"

REM 5) 清理并验证：能打印出版本号（如 v35.7.5）就成功了
del electron.zip
node_modules\electron\dist\electron.exe --version
```

最后一步打印出版本号，就说明本体已就位，回去 `npm run dev` 即可。

> - 如果每次都卡在第 3 步之前的解压，先去杀毒软件的「隔离区 / 防护记录」看看 `electron.exe` 是不是被拦了，把 `desktop-app\node_modules\electron` 加进白名单再重试。
> - macOS / Linux 一般不会遇到解压崩溃；真要手动解压，把第 3 步换成 `unzip electron.zip -d node_modules/electron/dist`，第 4 步的 `path.txt` 内容写成 `Electron.app/Contents/MacOS/Electron`（macOS）或 `electron`（Linux）。

---

## 项目结构

```text
.
├── setup.bat / start.bat   # 一键安装 / 启动（Windows）
├── setup.sh  / start.sh    # 一键安装 / 启动（macOS / Linux）
├── main.py                 # FastAPI 后端入口
├── translator.py           # 标签翻译核心
├── requirements.txt        # Python 依赖
├── env_config.json         # 桌面端用来找 Python 的配置（setup.bat 自动生成）
├── custom_translation.json # 用户自定义翻译词典
├── artist_favorites.json   # 画师收藏（分组）
├── character_favorites.json# 角色收藏（按 source_hint 合并）
├── hot_pic/                # 下载根目录，按日期归档
├── exiftool/               # 已自带，无需另装
└── desktop-app/            # Electron + Vue 3 前端
    ├── electron/main.cjs   # Electron 主进程，负责拉起 Python 后端
    └── src/                # Vue 源码（CrawlerPage / EditorPage / FavoritesPage）
```

---
*Created by Claude Code AI assistant.*
