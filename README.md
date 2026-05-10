# Danbooru 图片抓取与管理桌面应用

这是一个基于 **Electron** + **Vue 3** + **FastAPI (Python)** 开发的桌面应用程序，旨在提供高效的 Danbooru 图片抓取、本地库管理、标签翻译以及基础图片处理功能。

## 主要功能

*   **多模式抓取**：
    *   **排行榜**：获取当日热门图片。
    *   **日期热门**：按指定日期获取 Danbooru 的 Explore 热门图片。
    *   **日期范围抓取**：支持批量获取一段时间内的热门内容。
    *   **按 ID 下载**：直接输入特定 ID 进行下载。
*   **智能本地库管理**：
    *   按下载日期自动归类文件夹（`hot_pic/YYYY-MM-DD`）。
    *   支持本地图片搜索（按画师或角色名）。
    *   **格式自动识别**：支持图片、视频（webm/mp4）以及动图。
*   **标签翻译系统**：
    *   自动将 Danbooru 的英文角色标签翻译为中文。
    *   支持导入自定义 `json` 翻译词典。
    *   Electron 端实现预加载翻译，启动时零延迟显示中文。
*   **动画转换工具**：
    *   自动识别下载的 `ZIP` 动画包。
    *   支持一键通过 **FFMPEG** 将 `ZIP` 转换为 `GIF` 并在界面中动态预览。
*   **内置查看器与编辑器**：
    *   全屏大图查看，支持缩放与翻页。
    *   内置打码/水印编辑器（位于 `EditorPage`）。

## 安装与配置指南

### 1. 准备 Python 环境
建议使用 Anaconda 或 venv 创建一个独立的虚拟环境（推荐 Python 3.9 或更高版本）：

#### 使用 Anaconda 
```bash
# 创建名为 danbooru 的环境
conda create -n danbooru python=3.10
# 激活环境
conda activate danbooru
```

### 2. 安装 Python 依赖项
项目所需的包已列在根目录（或上一级目录）的 `requirements.txt` 中。请执行以下命令：

```bash
# 安装基础依赖
pip install -r requirements.txt
```

### 3. 指定 Python 解释器
为了确保桌面端能够自动拉起后台服务，你需要在项目根目录下创建`env_config.json` 文件，例如：
```json
{
    "python_path": "D:\\Anaconda3\\envs\\pic_web\\python.exe"
}
```
*提示：使用 Conda，可以通过 `where python` 命令查看当前环境的解释器路径，一般时给出路径的第一个，注意Windows下的两个斜杆。*

### 4. 前端开发环境
如果你需要自行编译或修改前端代码：
1.  安装 [Node.js](https://nodejs.org/) (推荐 18+)。
2.  进入前端目录安装依赖：
    ```bash
    cd desktop-app
    npm install
    ```

### 5. 外部组件
*   **FFMPEG**：转换动图功能强依赖 FFMPEG。
    *   请从 [FFMPEG 官网](https://ffmpeg.org/download.html) 下载。
    *   将 `ffmpeg.exe` 所在的 `bin` 文件夹路径添加到系统的 **环境变量 Path** 中。
    *   验证：在终端输入 `ffmpeg -version` 应当有正常输出。

## 运行与使用

### 快速启动

#### 方式1：直接运行 `desktop-app` 编译后的程序，**启动桌面端**：
先进入到 `desktop-app` 目录：
```bash
cd desktop-app
```
第一次运行时，需要先编译前端代码：
```bash
npm run build
```
编译完成后，运行以下命令启动桌面应用：
```bash
npm run dev
```

## 项目结构

*   `/hot_pic/`: 默认图片下载根目录。
*   `/desktop-app/`: Electron + Vue 3 前端代码。
*   `main.py`: 后端 API 服务主入口。
*   `translator.py`: 标签翻译核心逻辑。
*   `custom_translation.json`: 用户自定义翻译词典。

---
*Created by Antigravity AI assistant.*
