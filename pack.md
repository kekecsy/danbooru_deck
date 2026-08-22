构建 portable 可执行文件


第 1 步：构建 Python 后端（PyInstaller）

在项目根目录（C:\Users\kekecsy\Desktop\danbooru_deck\danbooru_deck）打开 PowerShell，执行：

.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath build\python-dist --workpath build\pyinstaller crawler-backend.spec

第 2 步：构建 portable Electron 包

cd desktop-app
npm run dist:portable

输出：C:\Users\kekecsy\Desktop\danbooru_deck\danbooru_deck_exe\Danbooru-Deck-0.1.0-x64-Portable.exe
