# Danbooru 图片抓取与管理桌面应用

基于 **Electron + Vue 3 + FastAPI** 的桌面端，提供 Danbooru 抓图、本地图库管理、标签翻译和打码编辑等功能。

---

## 🚀 快速开始（Windows）

> 需要 [Python 3.9+](https://www.python.org/downloads/)和 [Node.js 18+](https://nodejs.org/)。

```text
1. 下载/克隆本仓库
2. 双击 setup.bat   ← 一键创建 .venv、装依赖、编译前端
3. 双击 start.bat   ← 启动桌面端
```

`setup.bat` 的作用：

- 检查 Python / Node.js
- 在项目根目录创建独立 `.venv`，跑 `pip install -r requirements.txt`
- `desktop-app/` 里跑 `npm install` + `npm run build`
- 写入 `env_config.json`，把 Python 路径指向上面的 `.venv`

之后只要双击 `start.bat` 即可启动。需要桌面快捷方式的话，对 `start.bat` 右键 → 发送到 → 桌面快捷方式。

---

## 主要功能

- **多模式抓取**：排行榜 / 日期热门 / 日期范围热门 / 仅收集 ID / 按 ID 下载。
- **本地库管理**：按下载日期归类（`hot_pic/YYYY-MM-DD/`），支持画师 / 角色搜索、score 排序、筛选格式（图片/视频/动图）。
- **热度刷新**：当前页 / 指定范围 / 全部 三种方式刷新 score、收藏数、画师；范围 >5 页时自动每 4 页休 40s 防风控。
- **画师 / 角色收藏**：点 chip 旁的 ★ 可分组收藏，角色按 `source_hint` 自动合并归类；被收藏的卡片在画廊中异色高亮。
- **标签翻译**：英文角色 tag → 中文，支持在线/手动补全；可导入自定义 `json` 词典。
- **ZIP→GIF 转换**（可选，依赖 FFMPEG，见下文）。
- **打码编辑器**：内置全屏查看器 + EditorPage 打码/水印工具。

---

## 进阶安装（不想用一键脚本时）

### 1. Python 环境

任选其一。

venv：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Anaconda（推荐）：

```bash
conda create -n danbooru python=3.10
conda activate danbooru
pip install -r requirements.txt
```

### 2. 指定 Python 解释器

桌面端启动时按以下顺序寻找 Python：

1. 项目根目录的 `env_config.json` 中 `python_path` 字段（绝对路径，Windows 注意 JSON 里反斜杠要写成 `\\`）
2. 环境变量 `WEB_MOSAIC_PYTHON`
3. 系统 `PATH` 中的 `python` / `py -3`

`setup.bat` 会自动生成 `env_config.json`；手动写时格式如下：

```json
{
    "python_path": "D:\\Anaconda3\\envs\\danbooru\\python.exe"
}
```

提示：在激活的环境里跑 `where python` 取第一行即可。

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
winget install Gyan.FFmpeg
```

或从 [FFMPEG 官网](https://ffmpeg.org/download.html) 下载，把 `bin/` 加入系统 `PATH`，验证：

```bash
ffmpeg -version
```

---

## 无法连接 Danbooru？

国内网络偶尔会被运营商劫持，可以改 hosts 绕过。

文件位置：`C:\Windows\System32\drivers\etc\hosts`，在末尾追加一行：

```text
104.26.11.39 danbooru.donmai.us
```

桌面端右上角的「无法连接？修改Hosts教程」按钮里也有同样的说明。

---

## 项目结构

```text
.
├── setup.bat / start.bat   # 一键安装 / 启动
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
