# Danbooru 图片抓取与管理桌面应用

基于 **Electron + Vue 3 + FastAPI** 的 Windows 桌面应用，提供 Danbooru 抓图、本地图库管理、标签翻译、收藏和图片编辑等功能。

普通用户不需要安装 Python、Node.js、Electron，也不需要配置开发环境。

---

## 界面预览

**抓图主界面** — 左侧配置抓取任务，右侧浏览本地图库；支持多种抓取模式、跨页选择、筛选和排序。

![抓图主界面](overview_pic/sample1.png)

**大图查看器** — 支持原始大小、适应窗口、画师/角色收藏、编辑跳转和上下张切换。

![大图查看器](overview_pic/sample2.png)

---

## 下载与使用

Windows 用户可以选择安装版或便携版，两者都已包含 Electron 前端、Python 后端及运行依赖。

### 安装版

下载并运行：

```text
Danbooru-Deck-<版本号>-x64.exe
```

按照安装向导完成安装，然后从桌面或开始菜单启动。

安装版的用户数据默认保存在：

```text
%APPDATA%\Danbooru Deck\data
```

卸载或升级应用不会自动删除 `hot_pic`、收藏和其他用户数据。

### 便携版

下载并直接双击：

```text
Danbooru-Deck-<版本号>-x64-Portable.exe
```

便携版无需安装。首次启动时会在 exe 同目录创建：

```text
Danbooru Deck Data/
├── hot_pic/       # 下载的图片和媒体文件
├── drawer/        # 爬虫运行数据
├── app-state/     # 窗口状态和缓存
└── *.json         # 收藏、图库配置和翻译数据
```

迁移或备份便携版时，请同时复制便携版 exe 和 `Danbooru Deck Data` 文件夹。

> 单文件便携版首次启动需要解压内置资源，速度可能比后续启动稍慢。

### Windows 安全提示

如果发布版本没有配置商业代码签名证书，Windows SmartScreen 可能显示“未知发布者”。请确认文件来自本项目的正式发布页面后再运行。

---

## 主要功能

- **多模式抓取**：排行榜、日期热门、日期范围热门、仅收集 ID、按 ID 下载、按 Tag 下载。
- **本地图库管理**：按日期归档，支持画师/角色搜索、热度排序和媒体格式筛选。
- **热度刷新**：支持当前页、指定范围和全部刷新 score、收藏数及画师信息。
- **画师与角色收藏**：支持分组收藏、来源归类和画廊高亮。
- **标签翻译**：英文角色标签转中文，支持在线补全和自定义翻译数据。
- **打码与图片编辑**：内置大图查看器和图片编辑工具。
- **ZIP 转 GIF**：安装 FFMPEG 后可使用可选的转换功能。

---

## 用户数据说明

以下内容属于用户数据，不会打包进程序，也不会在升级时被覆盖：

- `hot_pic/`
- `drawer/`
- `artist_favorites.json`
- `character_favorites.json`
- `image_favorites.json`
- `library_roots.json` — 多图库根目录配置。每个 root 可加 `"lazy_scan": true` 让 app 启动时只枚举日期目录名，不数图（适合机械盘 / 外置盘 / NAS）。详见 `library_roots.example.json` 的注释。
- `custom_translation.json`
- `character_chinese_search.json`
- `character_supplement.json`

便携版将这些文件保存在 exe 旁的 `Danbooru Deck Data` 中；安装版将它们保存在当前 Windows 用户的应用数据目录中。

---

## 可选：FFMPEG

抓图、图库管理、收藏和图片编辑不需要 FFMPEG。只有 ZIP 转 GIF 功能需要它。

Windows 可以使用以下命令安装：

```powershell
winget install Gyan.FFmpeg
```

安装完成后可在终端验证：

```powershell
ffmpeg -version
```

---

## 无法连接 Danbooru

如果所在网络无法连接 `danbooru.donmai.us`，请先检查代理、DNS 和防火墙设置。应用内也提供了“无法连接”相关说明。

如确实需要修改 Windows hosts，请使用管理员权限编辑：

```text
C:\Windows\System32\drivers\etc\hosts
```

网络地址可能发生变化，不建议长期使用未经确认的固定 IP。

---

## 从源码开发

以下内容仅面向希望修改代码或自行构建发布包的开发者。普通用户无需执行。

### 环境要求

- Python 3.9+
- Node.js 18+
- Windows x64

### 安装源码依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller

cd desktop-app
npm install
```

也可以使用仓库中的 `setup.bat` 初始化开发环境，使用 `start.bat` 启动源码版。

### 开发模式

```powershell
cd desktop-app
npm run dev
```

### 构建 Python 后端

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath build\python-dist `
  --workpath build\pyinstaller `
  crawler-backend.spec
```

### 构建 Windows 发布包

```powershell
cd desktop-app

# 安装版
npm run dist:win

# 便携版
npm run dist:portable
```

默认输出目录：

```text
../danbooru_deck_exe/
```

---

## 项目结构

```text
.
├── main.py                    # FastAPI 后端入口
├── runtime_paths.py           # 程序资源与用户数据路径管理
├── crawler-backend.spec       # PyInstaller 后端打包配置
├── translator.py              # 标签翻译核心
├── requirements.txt           # Python 源码开发依赖
├── setup.bat / start.bat      # Windows 源码开发辅助脚本
├── hot_pic/                   # 源码开发模式下的下载目录
└── desktop-app/
    ├── electron/main.cjs      # Electron 主进程
    ├── src/                   # Vue 3 前端源码
    └── package.json           # 前端及 Windows 打包配置
```

---

## 许可证与第三方服务

请遵守 Danbooru 的使用规则、目标站点的访问频率限制，以及所在地适用的法律法规。下载内容的版权归原作者或权利人所有。
