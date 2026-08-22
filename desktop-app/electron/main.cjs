const { app, BrowserWindow, Menu, ipcMain, dialog, shell, clipboard, nativeImage, protocol, net, screen } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');

const isDev = !app.isPackaged;
const sourceRoot = path.resolve(__dirname, '..', '..');
const resourceRoot = isDev ? sourceRoot : process.resourcesPath;
// 旧 Portable 版は exe の場所（PORTABLE_EXECUTABLE_DIR）隣に Danbooru Deck Data を置いていたが、
// exe を移動/コピーしたりインストーラ版と併用すると参照先がブレて習慣・収藏を見失う問題があった。
// 現在は Portable/インストーラを問わず userData（%APPDATA%\Danbooru Deck）に固定する。
// portableRoot は旧データの「初回自動移行」の移行元探索にのみ使う（下の migrateLegacyPortableData）。
const portableRoot = process.env.PORTABLE_EXECUTABLE_DIR || '';
const dataRoot = isDev
  ? sourceRoot
  : path.join(app.getPath('userData'), 'data');
const repoRoot = dataRoot;
const hotPicDir = path.join(dataRoot, 'hot_pic');
const presetDirs = isDev
  ? [
      // dev 模式：直接指向源码目录
      path.join(sourceRoot, 'pic_web', 'present')
    ]
  : [
      // 打包分发：贴图素材放在用户数据目录 danbooru_DATA/present（首次启动由后端播种），
      // 不再从 exe 同梱资源里读，用户增删的贴图会被保留。
      path.join(dataRoot, 'present')
    ];
const crawlerApiBase = 'http://127.0.0.1:8000';
const DEFAULT_WINDOW_STATE = { width: 1540, height: 980, isMaximized: true };
const MIN_WINDOW_WIDTH = 1240;
const MIN_WINDOW_HEIGHT = 760;

let crawlerProcess = null;
let crawlerStartPromise = null;
let crawlerStdout = [];
let crawlerLastError = '';
let thumbCacheDir = '';
// 本次 Electron 会话随机注入到 uvicorn 命令行的小 token（_danbooru_deck_token='xxxx'）。
// 用来「正向上匹配」这是不是我们之前留下的孤儿进程；不是安全边界。
let crawlerSessionToken = '';
// 「spawn 失败 → 杀孤儿 → 再 spawn」这条链路的 bounded retry 计数。
let crawlerBindRetries = 0;

function windowStatePath() {
  return path.join(app.getPath('userData'), 'window-state.json');
}

function clampWindowSize(value, min, max, fallback) {
  const candidate = Number.isFinite(value) ? value : fallback;
  return Math.max(min, Math.min(Math.round(candidate), max));
}

function loadWindowState() {
  const state = { ...DEFAULT_WINDOW_STATE };
  try {
    const raw = JSON.parse(fs.readFileSync(windowStatePath(), 'utf-8'));
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;
    state.width = clampWindowSize(raw?.width, MIN_WINDOW_WIDTH, Math.max(MIN_WINDOW_WIDTH, width), DEFAULT_WINDOW_STATE.width);
    state.height = clampWindowSize(raw?.height, MIN_WINDOW_HEIGHT, Math.max(MIN_WINDOW_HEIGHT, height), DEFAULT_WINDOW_STATE.height);
    state.isMaximized = !!raw?.isMaximized;
  } catch {
    // First launch or invalid state file: keep the historical default size and maximized startup.
  }
  return state;
}

function saveWindowState(win) {
  if (!win || win.isDestroyed()) return;
  try {
    const bounds = typeof win.getNormalBounds === 'function' ? win.getNormalBounds() : win.getBounds();
    const state = {
      width: Math.max(MIN_WINDOW_WIDTH, Math.round(bounds.width)),
      height: Math.max(MIN_WINDOW_HEIGHT, Math.round(bounds.height)),
      isMaximized: win.isMaximized()
    };
    fs.mkdirSync(path.dirname(windowStatePath()), { recursive: true });
    fs.writeFileSync(windowStatePath(), `${JSON.stringify(state, null, 2)}\n`, 'utf-8');
  } catch {
    // Window state persistence is best-effort; failing here should not block app shutdown.
  }
}

function loadLibraryRoots() {
  const roots = [{ id: 'default', label: 'hot_pic', path: path.resolve(hotPicDir), isDefault: true }];
  const configPath = path.join(repoRoot, 'library_roots.json');
  if (!fs.existsSync(configPath)) return roots;
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
  } catch {
    return roots;
  }
  const entries = Array.isArray(raw) ? raw : (Array.isArray(raw?.roots) ? raw.roots : []);
  const seenPaths = new Set(roots.map(r => path.resolve(r.path).toLowerCase()));
  const seenIds = new Set(['default']);
  entries.forEach((entry, index) => {
    const rawPath = typeof entry === 'string' ? entry : (entry?.path || entry?.root || '');
    if (!rawPath) return;
    const resolved = path.isAbsolute(rawPath) ? path.resolve(rawPath) : path.resolve(repoRoot, rawPath);
    const pathKey = resolved.toLowerCase();
    if (seenPaths.has(pathKey)) return;
    let id = String(typeof entry === 'object' ? (entry.id || '') : '').replace(/[^a-zA-Z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || `lib${index + 1}`;
    const baseId = id;
    let suffix = 2;
    while (seenIds.has(id)) {
      id = `${baseId}_${suffix}`;
      suffix += 1;
    }
    seenPaths.add(pathKey);
    seenIds.add(id);
    // 机械盘 / 外置盘 / 网盘根目录上开懒扫：只枚举日期目录名，不数图。
    // 用户在 JSON 里显式写 "lazyScan": false 可以覆盖（适用于该 root 是本地 SSD）。
    // Python 端 _load_library_roots_config 也有同样的字段，命名用 snake_case；这里
    // 保持 camelCase 是 Electron 这一侧的历史命名习惯，避免影响其它读 lazyScan 的代码。
    const lazyScan = !!(entry && typeof entry === 'object' && entry.lazyScan);
    roots.push({
      id,
      label: (typeof entry === 'object' && (entry.label || entry.name)) || path.basename(resolved) || id,
      path: resolved,
      isDefault: false,
      lazyScan
    });
  });
  return roots;
}

function allowedLibraryRoots() {
  return loadLibraryRoots().map(root => root.path);
}

function isWithinLibraryRoots(targetPath) {
  return allowedLibraryRoots().some(root => isWithin(root, targetPath));
}

let localCustomDict = null;
let localCustomDictMtime = 0;
function loadLocalCustomDict() {
  const dictPath = path.join(repoRoot, 'custom_translation.json');
  if (fs.existsSync(dictPath)) {
    try {
      const mtime = fs.statSync(dictPath).mtimeMs;
      if (!localCustomDict || mtime !== localCustomDictMtime) {
        localCustomDict = JSON.parse(fs.readFileSync(dictPath, 'utf-8'));
        localCustomDictMtime = mtime;
      }
    } catch (e) {
      localCustomDict = {};
      localCustomDictMtime = 0;
    }
  }
  return localCustomDict || {};
}

function translateTags(tagString) {
  if (!tagString) return [];
  const dict = loadLocalCustomDict();
  const aliases = dict["__source_hint_aliases__"] || {};
  return tagString.split(' ').map(tag => {
    const entry = dict[tag];
    const chinese_name = (entry && entry.has_chinese && entry.chinese_name) ? entry.chinese_name : tag;
    const hint = (entry && entry.source_hint) ? entry.source_hint : '';
    const alias = (hint && aliases[hint]) ? aliases[hint] : '';
    
    let meta = chinese_name;
    if (hint) meta += ` [${hint}]`;
    if (alias) meta += ` [${alias}]`;
    return meta;
  });
}

function createWindow() {
  const windowState = loadWindowState();
  const win = new BrowserWindow({
    width: windowState.width,
    height: windowState.height,
    minWidth: MIN_WINDOW_WIDTH,
    minHeight: MIN_WINDOW_HEIGHT,
    backgroundColor: '#f3ede2',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // 默认 true 时 Chromium 会在窗口失焦/最小化后把 setTimeout/setInterval 节流到
      // ≥1s（隐藏 5 分钟后进 intensive 模式，≥1min/次）。CrawlerPage.vue 的范围
      // 刷新用前端 setTimeout 凑 40s 批间休息，被节流会膨胀到几十分钟看起来像卡死。
      // 关掉换前后台一致的 timer 节奏。
      backgroundThrottling: false
    }
  });
  win.setMenuBarVisibility(false);
  if (windowState.isMaximized) win.maximize();

  // 全局右键复制：所有能划取文字的面上，右键直接落剪贴板 + 弹轻量 toast。
  // - 有选中文字：clipboard.writeText + 通知 renderer 弹 toast
  //   · 非可编辑区（div/pre/log/角色 chip 之外的部分…）：preventDefault 屏蔽默认菜单
  //     （这些地方没有 Cut/Paste 等可用项，二级菜单点 Copy 没必要）
  //   · 可编辑区（input/textarea/contenteditable）：不 preventDefault，让默认菜单
  //     出来，Cut/Paste/Select All 还能用；用户从默认菜单点 Copy 会再写一次（幂等无害）
  // - 无选区：什么都不做（让默认行为或自定义菜单照常）
  // 已有 renderer 端 event.preventDefault() 的自定义菜单（CrawlerPage 字符 tag chip、
  // PosePage 姿势 SVG、pic_web 画布）会在主进程之前拦截 context-menu 事件，保留原行为。
  win.webContents.on('context-menu', (event, params) => {
    const text = params.selectionText;
    if (text && text.trim()) {
      clipboard.writeText(text);
      win.webContents.send('context-menu:copied', {
        length: text.length,
        preview: text.slice(0, 40).replace(/\s+/g, ' '),
      });
      if (!params.isEditable) event.preventDefault();
    }
  });

  // 关闭拦截：右上角 × / Alt+F4 / 系统菜单 关闭时弹出异步确认对话框，
  // 避免后台抓图任务被误关打断。用户确认后再真正关闭。
  let confirmingClose = false;
  win.on('close', (event) => {
    if (win.__forceClose) {
      saveWindowState(win);
      return;
    }
    event.preventDefault();
    if (confirmingClose) return;
    confirmingClose = true;
    dialog.showMessageBox(win, {
      type: 'question',
      buttons: ['确认', '取消'],
      defaultId: 1,
      cancelId: 1,
      noLink: true,
      title: '确认关闭',
      message: '确认要关闭抓图工具吗？',
      detail: '后台正在运行的抓取任务会被中断。'
    }).then(({ response }) => {
      confirmingClose = false;
      if (response === 0) {
        win.__forceClose = true;
        win.close();
      }
    }).catch(() => {
      confirmingClose = false;
    });
  });

  if (isDev) {
    win.loadURL('http://localhost:5173');
  } else {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }
}

function isDateFolder(name) {
  return /^\d{4}-\d{2}-\d{2}$/.test(name);
}

const GALLERY_MEDIA_EXT_RE = /\.(jpg|jpeg|png|gif|webp|bmp|avif|zip|mp4|webm|mov|mkv|avi)$/i;

function countGalleryMediaFiles(dirPath) {
  if (!fs.existsSync(dirPath)) return 0;
  let count = 0;
  for (const item of fs.readdirSync(dirPath, { withFileTypes: true })) {
    if (!item.isFile() || !GALLERY_MEDIA_EXT_RE.test(item.name)) continue;
    if (item.name.toLowerCase().endsWith('.gif')) {
      const zipName = item.name.slice(0, -4) + '.zip';
      if (fs.existsSync(path.join(dirPath, zipName))) continue;
    }
    count += 1;
  }
  return count;
}

function getTodayString() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

function safeReadJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch {
    return fallback;
  }
}

function toAbsolutePath(targetPath) {
  if (!targetPath) return '';
  return path.isAbsolute(targetPath) ? path.normalize(targetPath) : path.resolve(repoRoot, targetPath);
}

function countPendingIds(dirPath) {
  // 读 folder/ids_data.json 里待下载的 id 数量，给日历标出「有 id 待下载」的日期。
  // 文件不存在 / 解析失败 / 非数组都按 0 计。
  const idsFile = path.join(dirPath, 'ids_data.json');
  if (!fs.existsSync(idsFile)) return 0;
  try {
    const raw = fs.readFileSync(idsFile, 'utf-8');
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data.length : 0;
  } catch (_) {
    return 0;
  }
}

function listDateFolderDetails() {
  const byDate = new Map();
  for (const root of loadLibraryRoots()) {
    if (!fs.existsSync(root.path)) continue;
    // 懒扫根：只读日期目录名，不调 countGalleryMediaFiles / countPendingIds。
    // 机械盘 / 外置盘 / 网盘上几百个日期 × 几百张图，iterdir 几次就卡死。
    // 数量留 null + countKnown=false，让前端走"有图片但未统计"分支；
    // 点进具体日期时由 buildGalleryByDate 触发单日扫描补上。
    const isLazy = !!root.lazyScan;
    for (const item of fs.readdirSync(root.path, { withFileTypes: true })) {
      if (!item.isDirectory() || !isDateFolder(item.name)) continue;
      const rec = byDate.get(item.name) || {
        date: item.name,
        imageCount: isLazy ? null : 0,
        sourceCount: 0,
        hasImages: isLazy,
        pendingIds: 0,
        countKnown: !isLazy
      };
      rec.sourceCount += 1;
      if (isLazy) {
        byDate.set(item.name, rec);
        continue;
      }
      rec.imageCount += countGalleryMediaFiles(path.join(root.path, item.name));
      rec.hasImages = rec.imageCount > 0;
      rec.pendingIds += countPendingIds(path.join(root.path, item.name));
      byDate.set(item.name, rec);
    }
  }
  return Array.from(byDate.values()).sort((a, b) => b.date.localeCompare(a.date));
}

function listDateFolders() {
  return listDateFolderDetails().map(item => item.date);
}

function normalizeDateFolderDetails(rawDetails, fallbackDates = []) {
  const seen = new Set();
  const details = [];
  for (const item of Array.isArray(rawDetails) ? rawDetails : []) {
    const date = item?.date || item?.folder;
    if (!isDateFolder(date) || seen.has(date)) continue;
    // 显式保留 null：懒扫根上 imageCount=null 表示"未统计"，不要 Number() 转成 0
    // 让前端误以为文件夹是空的。
    const rawCount = item.imageCount ?? item.image_count ?? item.count;
    const imageCount = rawCount == null ? null : Number(rawCount);
    const sourceCount = Number(item.sourceCount ?? item.source_count ?? 1);
    const pendingIds = Number(item.pendingIds ?? item.pending_ids ?? 0);
    seen.add(date);
    details.push({
      date,
      imageCount: imageCount == null ? null : (Number.isFinite(imageCount) ? imageCount : 0),
      sourceCount: Number.isFinite(sourceCount) ? sourceCount : 1,
      pendingIds: Number.isFinite(pendingIds) ? pendingIds : 0,
      hasImages: imageCount == null
        ? Boolean(item.hasImages ?? item.has_images ?? true)
        : Boolean(item.hasImages ?? item.has_images ?? imageCount > 0),
      countKnown: imageCount != null
    });
  }
  for (const date of fallbackDates || []) {
    if (!isDateFolder(date) || seen.has(date)) continue;
    seen.add(date);
    details.push({ date, imageCount: null, sourceCount: 1, pendingIds: 0, hasImages: true, countKnown: false });
  }
  return details.sort((a, b) => b.date.localeCompare(a.date));
}

function tryCreateEmptyDateFolder(date) {
  // 用户主动选中一个还没有文件夹的日期 → 在 default 根目录下建空文件夹。
  // 路径越界 / 非法日期 / 写盘失败时返回 false，让上层继续走 today 回退。
  if (!isDateFolder(date)) return false;
  const roots = loadLibraryRoots();
  if (!roots.length) return false;
  const baseRoot = path.resolve(roots[0].path);
  const target = path.resolve(baseRoot, date);
  // 路径穿越防护：解析后必须仍在 default 根目录下
  if (!target.startsWith(baseRoot + path.sep) && target !== baseRoot) return false;
  try {
    fs.mkdirSync(target, { recursive: true });
    return true;
  } catch (_) {
    return false;
  }
}

function resolveDate(requestedDate) {
  const dateFolders = listDateFolderDetails();
  const availableDates = dateFolders.map(item => item.date);
  const today = getTodayString();
  if (requestedDate && availableDates.includes(requestedDate)) {
    return { selectedDate: requestedDate, availableDates, dateFolders, today };
  }
  // 用户主动选中一个还没有文件夹的日期 → 建空文件夹并进入，
  // 避免后端回退到 today 之后前端又被「被改写的 selected_date」拽回去。
  if (requestedDate && tryCreateEmptyDateFolder(requestedDate)) {
    const refreshed = listDateFolderDetails();
    return {
      selectedDate: requestedDate,
      availableDates: refreshed.map(item => item.date),
      dateFolders: refreshed,
      today
    };
  }
  if (availableDates.includes(today)) {
    return { selectedDate: today, availableDates, dateFolders, today };
  }
  return { selectedDate: availableDates[0] || today, availableDates, dateFolders, today };
}

async function buildGalleryByDate(requestedDate) {
  // Try the Python backend first — it has character name translation
  try {
    const url = requestedDate
      ? `${crawlerApiBase}/api/gallery_data/${requestedDate}`
      : `${crawlerApiBase}/api/gallery_data`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    const resp = await fetch(url, { signal: controller.signal });
    clearTimeout(timeout);
    if (resp.ok) {
      const data = await resp.json();
      const images = (data.local_images || []).map(item => ({
        artist: item.artist || '未知',
        filename: item.filename,
        localPath: toAbsolutePath(item.local_path || path.join(hotPicDir, data.selected_date, item.filename)),
        postUrl: item.post_url || '',
        characters: item.characters || '',
        tags: item.tags || {},
        score: item.score || 0,
        favCount: item.fav_count || 0,
        libraryId: item.library_id || 'default',
        libraryLabel: item.library_label || '',
        libraryRoot: item.library_root || '',
        sourceDir: item.source_dir || '',
        date: item.date || data.selected_date
      }));
      return {
        selectedDate: data.selected_date,
        availableDates: data.available_dates || [],
        availableDateFolders: normalizeDateFolderDetails(data.available_date_folders, data.available_dates || []),
        availableTags: Array.isArray(data.available_tags) ? data.available_tags : [],
        libraryRoots: Array.isArray(data.library_roots) ? data.library_roots : [],
        today: data.today || getTodayString(),
        images
      };
    }
  } catch (_) {
    // Backend unavailable — fall back to local file reading
  }

  // Fallback: read directly from disk (no translation)
  const { selectedDate, availableDates, dateFolders, today } = resolveDate(requestedDate);
  const knownFiles = new Set();
  const images = [];
  const seenIdentity = new Set();

  for (const root of loadLibraryRoots()) {
    const dateDir = path.join(root.path, selectedDate);
    const viewerPath = path.join(dateDir, 'viewer_data.json');
    const viewerData = safeReadJson(viewerPath, []);

    for (const item of [...viewerData].reverse()) {
      const filename = item.filename;
      if (!filename) continue;
      const localPath = path.resolve(path.join(dateDir, filename));
      const identity = item.post_url || localPath.toLowerCase();
      if (seenIdentity.has(identity)) continue;
      seenIdentity.add(identity);
      knownFiles.add(localPath.toLowerCase());
      images.push({
        artist: item.artist || '未知',
        filename,
        localPath,
        postUrl: item.post_url || '',
        characters: translateTags(item.tags?.tag_string_character || ''),
        tags: item.tags || {},
        score: item.score || 0,
        favCount: item.fav_count || 0,
        libraryId: root.id,
        libraryLabel: root.label,
        libraryRoot: root.path,
        sourceDir: dateDir,
        date: selectedDate
      });
    }

    if (fs.existsSync(dateDir)) {
      const extraFiles = fs.readdirSync(dateDir)
        .filter(name => /\.(jpg|jpeg|png|gif|webp|bmp|avif|zip|mp4|webm)$/i.test(name))
        .sort((a, b) => b.localeCompare(a));

      for (const filename of extraFiles) {
        const localPath = path.resolve(path.join(dateDir, filename));
        if (knownFiles.has(localPath.toLowerCase())) continue;
        if (filename.toLowerCase().endsWith('.gif')) {
          const zipName = filename.slice(0, -4) + '.zip';
          if (fs.existsSync(path.join(dateDir, zipName))) continue;
        }
        images.push({
          artist: '未知',
          filename,
          localPath,
          postUrl: '',
          characters: translateTags(''),
          tags: {},
          libraryId: root.id,
          libraryLabel: root.label,
          libraryRoot: root.path,
          sourceDir: dateDir,
          date: selectedDate
        });
      }
    }
  }

  return { selectedDate, availableDates, availableDateFolders: dateFolders, today, libraryRoots: loadLibraryRoots(), images };
}

function isWithin(baseDir, targetPath) {
  const base = path.resolve(baseDir);
  const target = path.resolve(targetPath);
  return target === base || target.startsWith(`${base}${path.sep}`);
}

function mimeFromFile(targetPath) {
  const ext = path.extname(targetPath).toLowerCase();
  const map = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.bmp': 'image/bmp',
    '.avif': 'image/avif',
    '.mp4': 'video/mp4',
    '.webm': 'video/webm',
    '.mov': 'video/quicktime',
    '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo'
  };
  return map[ext] || 'application/octet-stream';
}

function listPresetFiles() {
  const seen = new Set();
  const items = [];
  for (const dir of presetDirs) {
    if (!fs.existsSync(dir)) continue;
    for (const name of fs.readdirSync(dir)) {
      const fullPath = path.join(dir, name);
      if (!fs.statSync(fullPath).isFile()) continue;
      if (!/\.(png|jpg|jpeg|bmp|webp)$/i.test(name)) continue;
      const key = name.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      items.push({ name, path: fullPath });
    }
  }
  return items;
}

function getPythonCommand() {
  if (!isDev) {
    return { command: path.join(resourceRoot, 'backend', 'crawler-backend.exe'), args: [] };
  }
  const configPath = path.join(repoRoot, 'env_config.json');
  try {
    if (fs.existsSync(configPath)) {
      const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
      if (config.python_path && fs.existsSync(config.python_path)) {
        return { command: config.python_path, args: [] };
      }
    }
  } catch (err) {
    console.error('读取 env_config.json 失败:', err);
  }

  if (process.env.WEB_MOSAIC_PYTHON) {
    return { command: process.env.WEB_MOSAIC_PYTHON, args: [] };
  }
  const candidates = [
    { command: path.join('D:', 'Anaconda3', 'envs', 'pic_web', 'python.exe'), args: [] },
    { command: path.join('D:', 'anaconda', 'python.exe'), args: [] },
    { command: 'python', args: [] },
    { command: 'py', args: ['-3'] }
  ];
  return candidates.find(item => fs.existsSync(item.command) || item.command === 'python' || item.command === 'py');
}

function getPythonSpawnArgs() {
  // 注入 sys._danbooru_deck_token='<8位hex>' 到 -c 字符串里，
  // 这样 wmic 拿到的 python.exe CommandLine 里就有这个 token，孤儿识别能匹配。
  // uvicorn 不读 sys 上的这个属性，纯当个关联键用，开销可忽略。
  const token = crawlerSessionToken || 'no-token';
  return [
    '-u',
    '-c',
    `import uvicorn, sys; sys._danbooru_deck_token='${token}'; uvicorn.run('main:app', host='127.0.0.1', port=8000, reload=False, access_log=False, log_level='warning')`
  ];
}

async function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function apiFetchJson(endpoint, options = {}) {
  const response = await fetch(`${crawlerApiBase}${endpoint}`, options);
  if (!response.ok) {
    throw new Error(`Crawler API ${endpoint} failed: ${response.status}`);
  }
  return response.json();
}

async function waitForCrawlerReady(retries = 40) {
  for (let i = 0; i < retries; i += 1) {
    try {
      await apiFetchJson('/api/status');
      return true;
    } catch {
      await delay(500);
    }
  }
  return false;
}

// 启动 uvicorn 子进程，bind 失败时尝试回收孤儿后 retry 1 次。
// 拆出来便于 ensureCrawlerService 在 retry 路径里重入。
async function spawnCrawlerProcess() {
  const python = getPythonCommand();
  if (!python) {
    throw new Error('未找到可用的 Python 解释器');
  }

  crawlerLastError = '';
  crawlerStdout = [];
  fs.mkdirSync(hotPicDir, { recursive: true });
  crawlerProcess = spawn(python.command, getBackendSpawnArgs(python), {
    cwd: dataRoot,
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      DANBOORU_DECK_DATA_DIR: dataRoot,
      DANBOORU_DECK_RESOURCE_DIR: isDev ? sourceRoot : ''
    }
  });

  crawlerProcess.stdout.on('data', chunk => {
    const lines = backendOutputLines(chunk.toString());
    crawlerStdout.push(...lines.slice(-20));
    crawlerStdout = crawlerStdout.slice(-200);
  });

  crawlerProcess.stderr.on('data', chunk => {
    const text = chunk.toString();
    const errors = backendErrorLines(text);
    if (errors.length) crawlerLastError = errors.join('\n');
    crawlerStdout.push(...backendOutputLines(text).slice(-20));
    crawlerStdout = crawlerStdout.slice(-200);
  });

  crawlerProcess.on('exit', code => {
    crawlerProcess = null;
    if (code !== 0) {
      crawlerLastError = crawlerLastError || `抓虫服务退出，代码 ${code}`;
    }
  });

  const ready = await waitForCrawlerReady();
  if (!ready) {
    const errText = crawlerLastError || '抓虫服务启动超时';
    // bind 失败 → 识别是不是我们之前留下的孤儿，是则杀之再重试 1 次
    if (crawlerBindRetries < 1 && /address already in use|Errno\s*10048|\[Errno\s*\d+\]\s+error while attempting to bind/i.test(errText)) {
      crawlerBindRetries += 1;
      const pids = await findPidsOnPort(8000);
      let killed = 0;
      for (const pid of pids) {
        const { ours } = await isOurOrphan(pid, crawlerSessionToken);
        if (ours) {
          console.log(`[crawler] killing previous orphan pid=${pid}`);
          await killCrawlerTree(pid);
          killed += 1;
        }
      }
      if (killed > 0) {
        // 抛出特殊错误让 ensureCrawlerService 走 retry 路径
        throw new Error('__RETRY_AFTER_ORPHAN_KILL__');
      }
    }
    throw new Error(errText);
  }
  crawlerBindRetries = 0;  // 成功就清零，让下次还能用 retry
}

async function ensureCrawlerService() {
  // 进程内 dedup：第一道关就检查，保证并发调用者都 await 同一个 in-flight 启动
  // 流程（探测 → 扫孤儿 → spawn → ready 检查），避免两个调用各起一个 python 抢 8000。
  if (crawlerStartPromise) {
    await crawlerStartPromise;
    return { ok: true, alreadyRunning: false };
  }
  // 本次启动流程的 retry 预算只算这次调用，下次 ensureCrawlerService 再重置。
  // 避免「上次 bind 失败过一次 → 这次同样 bind 失败却不重试」的不合理状态。
  crawlerBindRetries = 0;

  // 整个启动流程包成一个 promise，外面只用一处 await + catch + finally。
  crawlerStartPromise = (async () => {
    // 1) 探测：如果 8000 已经有监听者，先确认是不是「我们的」
    let probeOk = false;
    try { await apiFetchJson('/api/status'); probeOk = true; } catch {}

    if (probeOk) {
      const pids = await findPidsOnPort(8000);
      if (!pids.length) return { ok: true, alreadyRunning: true };  // 200 上了但 netstat 没拿到，理论不该发生
      let owned = false;
      for (const pid of pids) {
        const { ours } = await isOurOrphan(pid, crawlerSessionToken);
        if (ours) { owned = true; break; }
      }
      if (owned) return { ok: true, alreadyRunning: true };
      // 外来进程占着端口：拒绝使用，给用户明确错误（不静默蒙混）
      const infos = (await Promise.all(pids.map(p => getProcessInfo(p)))).filter(Boolean);
      const desc = infos.length
        ? infos.map((i, idx) => `${i.name || '?'} (PID ${pids[idx]})`).join('、')
        : `未知进程 (PID ${pids.join(', ')})`;
      const msg = `端口 8000 被其他进程占用：${desc}。`
                + '如需继续，请先关闭该进程（任务管理器 → 详细信息 → 结束进程，'
                + '或 `netstat -ano | findstr :8000` 找到 PID 后 `taskkill /F /PID <pid>`）。';
      crawlerLastError = msg;
      throw new Error(msg);
    }

    // 2) 端口空着，但万一上一轮留了「我们的孤儿」，防御性扫一次
    const stragglers = await findPidsOnPort(8000);
    for (const pid of stragglers) {
      const { ours } = await isOurOrphan(pid, crawlerSessionToken);
      if (ours) {
        console.log(`[crawler] sweep pre-spawn orphan pid=${pid}`);
        await killCrawlerTree(pid);
      }
    }

    // 3) 真正 spawn
    await spawnCrawlerProcess();
    return { ok: true, alreadyRunning: false };
  })();

  try {
    return await crawlerStartPromise;
  } catch (err) {
    if (err?.message === '__RETRY_AFTER_ORPHAN_KILL__') {
      // bounded retry：清掉当前 promise 再重入，这次会重新探测 + 走新的 spawn 流程
      crawlerStartPromise = null;
      return ensureCrawlerService();
    }
    throw err;
  } finally {
    // 成功 / 失败（非 retry）都清掉，dedup 才能让下一次调用重新触发
    crawlerStartPromise = null;
  }
}

function normalizeCrawlerImage(item) {
  return {
    ...item,
    local_path: toAbsolutePath(item.local_path || ''),
    score: item.score || 0,
    favCount: item.fav_count || 0
  };
}

ipcMain.handle('app:get-context', async () => ({
  repoRoot,
  hotPicDir,
  today: getTodayString()
}));

ipcMain.handle('gallery:get-by-date', async (_event, date) => buildGalleryByDate(date));

ipcMain.handle('gallery:open-local-file', async (_event, localPath) => {
  const resolvedPath = toAbsolutePath(localPath);
  if (!resolvedPath || !isWithinLibraryRoots(resolvedPath)) return { ok: false, message: '非法路径' };
  if (!fs.existsSync(resolvedPath)) return { ok: false, message: '文件不存在' };
  const result = await shell.openPath(resolvedPath);
  return { ok: result === '', message: result || '已尝试打开文件' };
});

ipcMain.handle('external:open', async (_event, url) => {
  if (!url) return false;
  await shell.openExternal(url);
  return true;
});

// 把 app 内部几个关键路径暴露给渲染层，教程弹窗会用它们展示
// 「开发版 / Portable 版各自的文件位置」并提供「打开所在目录」按钮。
// repoRoot 在 dev 下 = 源码根目录，portable 下 = %APPDATA%\Danbooru Deck\data
// hotPicDir 是下载图片实际落盘的目录
ipcMain.handle('app:get-paths', async () => ({
  isDev,
  repoRoot,
  hotPicDir,
  userDataPath: app.getPath('userData'),
}));

// 用 key 选择要打开的目录，目录不存在时按需创建。
// 用 shell.openPath 而不是 shell.openExternal + file://，避免 Windows 上
// 某些路径下 file:// 协议被路由到浏览器而非资源管理器。
ipcMain.handle('app:reveal-folder', async (_event, key) => {
  let target = '';
  if (key === 'repoRoot') target = repoRoot;
  else if (key === 'hotPicDir') target = hotPicDir;
  else if (key === 'userData') target = app.getPath('userData');
  else return { ok: false, message: '未知路径：' + String(key) };
  try {
    if (!fs.existsSync(target)) fs.mkdirSync(target, { recursive: true });
  } catch (e) {
    return { ok: false, message: '创建目录失败：' + (e.message || e) };
  }
  const result = await shell.openPath(target);
  return { ok: result === '', message: result || '已尝试打开目录' };
});

ipcMain.handle('dialog:select-image', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openFile'],
    filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'avif'] }]
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return result.filePaths[0];
});

ipcMain.handle('file:read-data-url', async (_event, targetPath) => {
  const resolvedPath = toAbsolutePath(targetPath);
  if (!resolvedPath || !fs.existsSync(resolvedPath)) return null;
  const buffer = fs.readFileSync(resolvedPath);
  return `data:${mimeFromFile(resolvedPath)};base64,${buffer.toString('base64')}`;
});

ipcMain.handle('file:to-file-url', async (_event, targetPath) => {
  const resolvedPath = toAbsolutePath(targetPath);
  return resolvedPath ? pathToFileURL(resolvedPath).toString() : '';
});

ipcMain.handle('file:to-local-url', async (_event, targetPath) => {
  const resolvedPath = toAbsolutePath(targetPath);
  return resolvedPath ? `local://${encodeURIComponent(resolvedPath)}` : '';
});

// 缩略图缓存目录（按 路径+mtime+尺寸 哈希，命中即复用）
function ensureThumbCacheDir() {
  if (!thumbCacheDir) thumbCacheDir = path.join(app.getPath('userData'), 'thumb-cache');
  if (!fs.existsSync(thumbCacheDir)) fs.mkdirSync(thumbCacheDir, { recursive: true });
  return thumbCacheDir;
}

// 生成（或复用缓存）指定长边的缩略图，返回 local:// URL。
// size=0 表示「原图」；动图/视频/不可解码格式一律回退原图，避免丢失动画或解码失败。
const THUMBABLE_EXTS = ['.jpg', '.jpeg', '.png', '.webp', '.bmp'];
ipcMain.handle('file:to-thumb-url', async (_event, payload) => {
  const { targetPath, size } = payload || {};
  const resolvedPath = toAbsolutePath(targetPath);
  if (!resolvedPath || !fs.existsSync(resolvedPath)) return '';
  const maxEdge = Number(size) || 0;
  const originalUrl = `local://${encodeURIComponent(resolvedPath)}`;
  const ext = path.extname(resolvedPath).toLowerCase();
  if (!maxEdge || !THUMBABLE_EXTS.includes(ext)) return originalUrl; // 原图档位 / 动图等
  try {
    const stat = fs.statSync(resolvedPath);
    const key = crypto.createHash('md5')
      .update(`${resolvedPath}|${stat.mtimeMs}|${stat.size}|${maxEdge}`)
      .digest('hex');
    const isPng = ext === '.png'; // PNG 保留透明通道，其余转 JPEG
    const cacheFile = path.join(ensureThumbCacheDir(), `${key}.${isPng ? 'png' : 'jpg'}`);
    if (!fs.existsSync(cacheFile)) {
      const img = nativeImage.createFromPath(resolvedPath);
      if (img.isEmpty()) return originalUrl; // 解码失败（部分 webp/avif）回退原图
      const { width, height } = img.getSize();
      let out = img;
      if (Math.max(width, height) > maxEdge) { // 仅缩小，不放大
        const opts = width >= height ? { width: maxEdge } : { height: maxEdge };
        out = img.resize({ ...opts, quality: 'good' });
      }
      fs.writeFileSync(cacheFile, isPng ? out.toPNG() : out.toJPEG(80));
    }
    return `local://${encodeURIComponent(cacheFile)}`;
  } catch {
    return originalUrl; // 任何异常都回退原图，保证缩略图始终能显示
  }
});

ipcMain.handle('file:exists', async (_event, targetPath) => {
  const resolvedPath = toAbsolutePath(targetPath);
  return resolvedPath ? fs.existsSync(resolvedPath) : false;
});

// 剪贴板读写 fallback：navigator.clipboard 在某些 Electron 版本/焦点条件下会抛错，
// 给 InputContextMenu.vue 的粘贴功能当主进程兜底。
ipcMain.handle('clipboard:read-text', () => {
  try { return clipboard.readText() || ''; } catch (e) { return ''; }
});
ipcMain.handle('clipboard:write-text', (_event, text) => {
  try { clipboard.writeText(String(text ?? '')); return true; } catch (e) { return false; }
});

ipcMain.handle('file:save-png', async (_event, payload) => {
  const { suggestedName, bytes } = payload || {};
  if (!bytes) return { ok: false, canceled: true };
  const result = await dialog.showSaveDialog({
    defaultPath: suggestedName || 'mosaic_export.png',
    filters: [{ name: 'PNG Image', extensions: ['png'] }]
  });
  if (result.canceled || !result.filePath) return { ok: false, canceled: true };
  fs.writeFileSync(result.filePath, Buffer.from(bytes));
  return { ok: true, canceled: false, filePath: result.filePath };
});

ipcMain.handle('file:copy-png', async (_event, payload) => {
  try {
    const { bytes } = payload || {};
    if (!bytes) return { ok: false };
    const buffer = Buffer.from(bytes);
    const image = nativeImage.createFromDataURL(`data:image/png;base64,${buffer.toString('base64')}`);
    if (image.isEmpty()) return { ok: false };
    clipboard.clear();
    clipboard.writeImage(image);
    clipboard.writeBuffer('image/png', buffer);
    const written = clipboard.readImage();
    return { ok: !written.isEmpty() };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

ipcMain.handle('preset:list', async () => listPresetFiles());

// ---------------- Caption (本地 caption.json 读写) ----------------
function captionJsonPathFor(imagePath) {
  const resolved = toAbsolutePath(imagePath);
  if (!resolved || !isWithinLibraryRoots(resolved)) return null;
  return path.join(path.dirname(resolved), 'caption.json');
}

function getBackendSpawnArgs(python) {
  return isDev ? [...python.args, ...getPythonSpawnArgs()] : python.args;
}

// === 端口 8000 孤儿探测 + 杀进程树 ===
// 背景：crawlerProcess.kill() 在 Windows 上只 TerminateProcess 当前子节点，
// 遇到 uvicorn 未来开 reload / 启子进程会漏；更糟的是 App 强杀（Task Manager / 断电）
// 整个 before-quit 都不触发，留下的孤儿下一次 bind 直接 [Errno 10048]。
// 这组 helper：探测端口、识别「是不是我们之前留下的」、是则 taskkill /F /T 整棵杀。
// 任何一步失败都 swallow，不向 renderer 抛额外错误。

function runCommand(command, args, { timeoutMs = 5000 } = {}) {
  return new Promise(resolve => {
    let stdout = '', stderr = '';
    let settled = false;
    const finish = (code) => {
      if (settled) return;
      settled = true;
      resolve({ code: code ?? -1, stdout, stderr });
    };
    let proc;
    try {
      proc = spawn(command, args, { windowsHide: true });
    } catch (err) {
      resolve({ code: -1, stdout, stderr: String(err) });
      return;
    }
    proc.stdout?.on('data', d => { stdout += d.toString(); });
    proc.stderr?.on('data', d => { stderr += d.toString(); });
    proc.on('error', () => finish(-1));
    proc.on('exit', code => finish(code));
    setTimeout(() => {
      try { proc.kill(); } catch {}
      finish(-2);
    }, timeoutMs);
  });
}

async function findPidsOnPort(port) {
  if (process.platform !== 'win32') {
    // POSIX 端暂不展开（lsof 不一定在用户机器上；当前 dev 都在 Windows）
    return [];
  }
  // netstat -ano 输出形如：
  //   TCP    127.0.0.1:8000    0.0.0.0:0    LISTENING    1234
  const { stdout } = await runCommand('netstat', ['-ano', '-p', 'TCP'], { timeoutMs: 4000 });
  const pids = new Set();
  const re = new RegExp(`\\s127\\.0\\.0\\.1:${port}\\s+\\S+\\s+LISTENING\\s+(\\d+)`, 'i');
  for (const line of stdout.split(/\r?\n/)) {
    const m = line.match(re);
    if (m) pids.add(Number(m[1]));
  }
  return Array.from(pids);
}

async function getProcessInfo(pid) {
  if (!pid || !Number.isFinite(pid)) return null;
  if (process.platform === 'win32') {
    const { stdout } = await runCommand(
      'wmic',
      ['process', 'where', `ProcessId=${pid}`, 'get', 'Name,CommandLine', '/format:list'],
      { timeoutMs: 4000 }
    );
    // 输出形如：Name=python.exe\r\nCommandLine=...\r\n
    const out = { name: '', commandLine: '' };
    for (const line of stdout.split(/\r?\n/)) {
      const idx = line.indexOf('=');
      if (idx < 0) continue;
      const k = line.slice(0, idx).trim();
      const v = line.slice(idx + 1).trim();
      if (k === 'Name') out.name = v;
      else if (k === 'CommandLine') out.commandLine = v;
    }
    return out.name ? out : null;
  }
  try {
    const buf = fs.readFileSync(`/proc/${pid}/cmdline`);
    return { name: '', commandLine: buf.toString('utf8').replace(/\0/g, ' ').trim() };
  } catch {
    return null;
  }
}

async function isOurOrphan(pid, token) {
  const info = await getProcessInfo(pid);
  if (!info) return { ours: false, info: null };
  const name = (info.name || '').toLowerCase();
  const cmd = info.commandLine || '';
  const isOurExe = name === 'python.exe' || name === 'python' || name === 'crawler-backend.exe';
  // token 是本次会话注入到 -c 字符串里的 sys._danbooru_deck_token='xxxx'，
  // 出现即代表「这是上一个 Danbooru Deck 启动的 uvicorn」。
  const hasToken = !!token && cmd.includes(`_danbooru_deck_token='${token}'`);
  return { ours: isOurExe && hasToken, info };
}

async function killCrawlerTree(pid) {
  if (!pid) return;
  if (process.platform === 'win32') {
    // /T 杀整棵树（覆盖未来 uvicorn 起的 reloader / 子进程）
    await runCommand('taskkill', ['/F', '/T', '/PID', String(pid)], { timeoutMs: 5000 });
  } else {
    try { process.kill(pid, 'SIGTERM'); } catch {}
    await delay(800);
    try { process.kill(pid, 'SIGKILL'); } catch {}
  }
  // 等端口真正空出来（最多 ~3s）
  for (let i = 0; i < 6; i += 1) {
    const still = await findPidsOnPort(8000);
    if (!still.length) return;
    await delay(500);
  }
}

function backendOutputLines(text) {
  return String(text || '')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .filter(line => !/^INFO:\s+/i.test(line));
}

// uvicorn 启动失败的 bind 错误形如：
//   [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000): only one usage of each socket address ...
// 它**不带** `ERROR:` / `CRITICAL:` 前缀，原正则直接漏掉，导致用户只看到「抓虫服务启动超时」而无任何线索。
// 另：capture Traceback 时把紧随的多行（File "..." / line N / xxxError: ...）一并捞进 crawlerLastError。
const BIND_ERROR_RE = /\[Errno\s*\d+\]\s+error while attempting to bind/i;
function backendErrorLines(text) {
  const lines = String(text || '').split(/\r?\n/).map(l => l.trim());
  const out = [];
  let inTraceback = false;
  for (const line of lines) {
    if (!line) {
      // 空行结束 traceback 块
      if (inTraceback) inTraceback = false;
      continue;
    }
    if (/^Traceback\b/i.test(line)) {
      inTraceback = true;
      out.push(line);
      continue;
    }
    if (/^(ERROR|CRITICAL):\s+/i.test(line)) {
      inTraceback = false;
      out.push(line);
      continue;
    }
    if (BIND_ERROR_RE.test(line)) {
      // 整个 bind 错误行收下来，紧接着 uvicorn 还会再印一行 "only one usage..." 描述，
      // 这一行没 `ERROR:` 前缀但和上一行是同一个错误，单独吸收
      out.push(line);
      const nextIdx = lines.indexOf(line) + 1;
      if (nextIdx < lines.length) {
        const nxt = lines[nextIdx];
        if (nxt && /^(only one usage|通常每个|每个套接字)/i.test(nxt)) {
          out.push(nxt);
        }
      }
      inTraceback = false;
      continue;
    }
    if (inTraceback) out.push(line);
  }
  return out;
}

ipcMain.handle('caption:read', async (_event, imagePath) => {
  const captionPath = captionJsonPathFor(imagePath);
  if (!captionPath || !fs.existsSync(captionPath)) return null;
  const store = safeReadJson(captionPath, {});
  const filename = path.basename(toAbsolutePath(imagePath));
  return store[filename] || null;
});

ipcMain.handle('caption:save', async (_event, payload) => {
  const { imagePath, entry } = payload || {};
  const captionPath = captionJsonPathFor(imagePath);
  if (!captionPath) return { ok: false, error: '非法路径' };
  const filename = path.basename(toAbsolutePath(imagePath));
  let store = {};
  if (fs.existsSync(captionPath)) store = safeReadJson(captionPath, {});
  store[filename] = { ...entry, updated_at: new Date().toISOString() };
  try {
    fs.writeFileSync(captionPath, JSON.stringify(store, null, 2), 'utf-8');
    return { ok: true, path: captionPath };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

// 列出某日期目录下 caption.json 中已生成的文件名集合（供画廊标记用）
// 只返回「有最终文本(caption 非空)」的条目：允许中途只保存 pipeline 步骤而不
// 把图片标成「已生成」（绿色角标语义保持 = 已有成文描述）。
ipcMain.handle('caption:list-for-date', async (_event, date) => {
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return [];
  const out = [];
  for (const root of loadLibraryRoots()) {
    const dateDir = path.join(root.path, date);
    const captionPath = path.join(dateDir, 'caption.json');
    if (!fs.existsSync(captionPath)) continue;
    const store = safeReadJson(captionPath, {});
    for (const name of Object.keys(store)) {
      const entry = store[name];
      if (entry && typeof entry.caption === 'string' && entry.caption.trim()) {
        out.push({ filename: name, localPath: path.resolve(path.join(dateDir, name)), libraryId: root.id });
      }
    }
  }
  return out;
});

// Caption 手动模式：把当前图片复制到系统剪贴板，方便用户粘贴到任意 chat LLM
// (Claude / ChatGPT / Gemini Web)。
// nativeImage.createFromPath 支持 PNG/JPG/BMP/TIFF；走 clipboard.writeImage 的话 GIF 只能塞首帧。
// 所以 GIF 单独走 clipboard.writeBuffer('image/gif', ...) 写原始字节，保留多帧动画。
// 不支持的格式（WebP/AVIF 看系统）返回 ok:false，由前端 fallback。
ipcMain.handle('caption:copy-image', async (_event, payload) => {
  const imagePath = typeof payload === 'string' ? payload : payload?.imagePath;
  const rawMaxEdge = typeof payload === 'object' ? Number(payload?.maxEdge) : 0;
  const maxEdge = Number.isFinite(rawMaxEdge) && rawMaxEdge > 0 ? Math.round(rawMaxEdge) : 0;
  const resolved = toAbsolutePath(imagePath);
  if (!resolved || !fs.existsSync(resolved)) {
    return { ok: false, error: '文件不存在' };
  }
  // 路径越权防护：只允许复制已接管图库下的图
  if (!isWithinLibraryRoots(resolved)) {
    return { ok: false, error: '非法路径' };
  }
  // .gif 单独走原始字节通道，否则会被 nativeImage 拍成首帧静图
  if (resolved.toLowerCase().endsWith('.gif')) {
    try {
      const buffer = fs.readFileSync(resolved);

      // 从 GIF 头解析画布尺寸（字节 6-7 = width LE, 8-9 = height LE）。
      // nativeImage.createFromPath 对动图常返回 empty（toast 一直显示 0×0），
      // 直接读头最稳。GIF89a/GIF87a 都一定有这段 magic。
      let headerW = 0;
      let headerH = 0;
      if (buffer.length >= 10
          && buffer[0] === 0x47 && buffer[1] === 0x49 && buffer[2] === 0x46) {
        headerW = buffer.readUInt16LE(6);
        headerH = buffer.readUInt16LE(8);
      }

      clipboard.clear();
      // 主通道：'image/gif' 走系统剪贴板的 GIF 通道，
      // 浏览器 / 聊天软件粘贴仍是动图。
      clipboard.writeBuffer('image/gif', buffer);
      const written = clipboard.readBuffer('image/gif');
      const animatedOk = written.length === buffer.length;

      // 兜底：很多桌面 app（画图 / Office / 部分 IM / 老编辑器）只读平台标准
      // 图像格式（Windows CF_DIB、macOS public.png、Linux image/png），对
      // 'image/gif' 通道直接无视——粘贴出来是空，误以为没复制成功。
      // 把首帧也写到平台标准通道，两个格式并存，target app 按自己支持的格式优先读。
      // 顺序：clear → writeBuffer(image/gif) → writeImage(...)，
      // writeImage 不会清掉已设的 image/gif。
      // nativeImage 对动图常返回 empty，写不进去时只保留 image/gif 主通道。
      let frameOk = false;
      const firstFrame = nativeImage.createFromPath(resolved);
      if (!firstFrame.isEmpty()) {
        clipboard.writeImage(firstFrame);
        const frameReadBack = clipboard.readImage();
        frameOk = !frameReadBack.isEmpty();
      }

      return {
        // 动图或首帧只要有一个真进剪贴板就算复制成功
        ok: animatedOk || frameOk,
        width: headerW,
        height: headerH,
        bytes: buffer.length,
        isGif: true,
        // 让前端区分 toast 文案：动图 vs 只塞了首帧 vs 完全失败
        animated: animatedOk,
        hasFirstFrame: frameOk,
      };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  try {
    let image = nativeImage.createFromPath(resolved);
    if (image.isEmpty()) {
      return {
        ok: false,
        error: '该图片格式无法转成剪贴板图像（可能是 WebP/AVIF），请直接把文件拖进 LLM 对话框'
      };
    }
    const originalSize = image.getSize();
    if (maxEdge > 0 && Math.max(originalSize.width, originalSize.height) > maxEdge) {
      const opts = originalSize.width >= originalSize.height ? { width: maxEdge } : { height: maxEdge };
      image = image.resize({ ...opts, quality: 'good' });
    }
    const copiedSize = image.getSize();
    clipboard.clear();
    clipboard.writeImage(image);
    // 再读回来校验真的写进去了（部分系统下 writeImage 静默失败）
    const written = clipboard.readImage();
    return {
      ok: !written.isEmpty(),
      width: copiedSize.width,
      height: copiedSize.height,
      originalWidth: originalSize.width,
      originalHeight: originalSize.height
    };
  } catch (error) {
    return { ok: false, error: error.message };
  }
});

// ---------------- Pose (本地 pose.json 读写) ----------------
const POSE_SCHEMA = 'anime_pose_v1';

function poseJsonPathFor(imagePath) {
  const resolved = toAbsolutePath(imagePath);
  if (!resolved || !isWithinLibraryRoots(resolved)) return null;
  return path.join(path.dirname(resolved), 'pose.json');
}

function normalizePoseStore(raw) {
  if (raw && typeof raw === 'object' && raw.images && typeof raw.images === 'object') {
    return {
      schema: raw.schema || POSE_SCHEMA,
      images: raw.images
    };
  }
  if (raw && typeof raw === 'object') {
    return {
      schema: POSE_SCHEMA,
      images: raw
    };
  }
  return { schema: POSE_SCHEMA, images: {} };
}

ipcMain.handle('pose:read', async (_event, imagePath) => {
  const posePath = poseJsonPathFor(imagePath);
  if (!posePath || !fs.existsSync(posePath)) return null;
  const filename = path.basename(toAbsolutePath(imagePath));
  const store = normalizePoseStore(safeReadJson(posePath, null));
  return store.images[filename] || null;
});

ipcMain.handle('pose:save', async (_event, payload) => {
  const { imagePath, entry } = payload || {};
  const posePath = poseJsonPathFor(imagePath);
  if (!posePath) return { ok: false, error: '非法路径' };
  const filename = path.basename(toAbsolutePath(imagePath));
  const existing = fs.existsSync(posePath) ? safeReadJson(posePath, null) : null;
  const store = normalizePoseStore(existing);
  store.schema = store.schema || POSE_SCHEMA;
  store.images[filename] = {
    ...(entry && typeof entry === 'object' ? entry : {}),
    updated_at: new Date().toISOString()
  };
  try {
    fs.writeFileSync(posePath, JSON.stringify(store, null, 2), 'utf-8');
    return { ok: true, path: posePath, filename };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle('pose:list-for-date', async (_event, date) => {
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return [];
  const out = [];
  for (const root of loadLibraryRoots()) {
    const dateDir = path.join(root.path, date);
    const posePath = path.join(dateDir, 'pose.json');
    if (!fs.existsSync(posePath)) continue;
    const store = normalizePoseStore(safeReadJson(posePath, null));
    for (const [filename, annotation] of Object.entries(store.images || {})) {
      out.push({
        filename,
        localPath: path.resolve(path.join(dateDir, filename)),
        libraryId: root.id,
        libraryLabel: root.label,
        annotation
      });
    }
  }
  return out;
});

ipcMain.handle('crawler:ensure-service', async () => ensureCrawlerService());
ipcMain.handle('crawler:start', async (_event, payload) => {
  await ensureCrawlerService();
  return apiFetchJson('/api/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
});
ipcMain.handle('crawler:pause', async () => {
  await ensureCrawlerService();
  return apiFetchJson('/api/pause', { method: 'POST' });
});
ipcMain.handle('crawler:resume', async () => {
  await ensureCrawlerService();
  return apiFetchJson('/api/resume', { method: 'POST' });
});
ipcMain.handle('crawler:stop', async () => {
  await ensureCrawlerService();
  return apiFetchJson('/api/stop', { method: 'POST' });
});
ipcMain.handle('crawler:status', async () => {
  await ensureCrawlerService();
  const data = await apiFetchJson('/api/status');
  return {
    ...data,
    new_images: (data.new_images || []).map(normalizeCrawlerImage),
    backendLogs: crawlerStdout.slice(-80),
    backendError: crawlerLastError
  };
});
ipcMain.handle('crawler:set-safe-mode', async (_event, safe) => {
  await ensureCrawlerService();
  return apiFetchJson('/api/set_safe_mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ safe: !!safe })
  });
});

// 旧 Portable 版（exe 隣の Danbooru Deck Data）に溜まっていたデータを、
// 新しい固定先 userData（%APPDATA%\Danbooru Deck）へ初回だけ移行する。
// - dataRoot（%APPDATA%\Danbooru Deck\data）がまだ無く、かつ exe 隣に旧フォルダがある時だけ実行（冪等）。
// - 旧 app-state（localStorage / window-state 等）は userData 直下へ、それ以外は data/ 下へコピー。
// - 旧データは削除せず残す（安全側）。移行に失敗しても致命ではないので握りつぶす。
function migrateLegacyPortableData() {
  if (isDev || !portableRoot) return;
  const legacyRoot = path.join(portableRoot, 'Danbooru Deck Data');
  // 新しい固定先が既にあるなら移行済み。exe 隣に旧フォルダが無いなら移行対象なし。
  if (fs.existsSync(dataRoot) || !fs.existsSync(legacyRoot)) return;
  // 旧フォルダが偶然 userData 配下（＝既に固定先）なら二重コピーになるので回避。
  if (isWithin(app.getPath('userData'), legacyRoot)) return;
  try {
    fs.mkdirSync(dataRoot, { recursive: true });
    for (const name of fs.readdirSync(legacyRoot)) {
      const src = path.join(legacyRoot, name);
      // app-state（Chromium プロファイル）だけ userData 直下、他は data/ 下へ。
      const dst = name === 'app-state'
        ? app.getPath('userData')
        : path.join(dataRoot, name);
      fs.cpSync(src, dst, { recursive: true, force: false, errorOnExist: false });
    }
    console.log(`[migrate] legacy portable data copied from ${legacyRoot} -> ${dataRoot}`);
  } catch (err) {
    console.error('[migrate] legacy portable data migration failed:', err);
  }
}

// === 单实例锁：避免两个 Electron 同时跑、各起一个 python 抢 8000 ===
// dev + prod 并存也跑不起来（两者都抢 8000），所以这个锁是真无副作用。
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  // 另一个 Danbooru Deck 已在运行；本次启动立刻退出，避免两实例争抢 8000。
  // 用 app.exit 而不是 app.quit：前者跳过 lifecycle hook，更适合「还没初始化就退」。
  app.exit(0);
} else {
  // 第二个实例启动时聚焦第一个实例的窗口（Electron 标准 pattern）
  app.on('second-instance', () => {
    const wins = BrowserWindow.getAllWindows();
    const w = wins[0];
    if (!w) return;
    if (w.isMinimized()) w.restore();
    w.show();
    w.focus();
  });
}

app.whenReady().then(() => {
  if (!gotSingleInstanceLock) return;  // 双保险：拿不到锁就不继续初始化
  migrateLegacyPortableData();
  fs.mkdirSync(hotPicDir, { recursive: true });
  // 本次会话 token：用于「正向上匹配」8000 上的监听者是不是我们之前留下的孤儿。
  // 8 位 hex = 32 bit 熵，碰撞概率 ~1/2^32，可接受（不是安全边界）。
  crawlerSessionToken = crypto.randomBytes(4).toString('hex');
  Menu.setApplicationMenu(null);
  thumbCacheDir = path.join(app.getPath('userData'), 'thumb-cache');
  protocol.handle('local', (request) => {
    const url = request.url.replace(/^local:\/\//, '');
    const filePath = decodeURIComponent(url);
    const allowedRoots = [...allowedLibraryRoots(), ...presetDirs, thumbCacheDir].filter(Boolean);
    if (!allowedRoots.some(root => isWithin(root, filePath))) {
      return new Response('Access denied', { status: 403 });
    }
    return net.fetch(pathToFileURL(filePath).toString());
  });
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// === crawler 进程清理：挂多个 lifecycle hook 防孤儿 ===
// 旧版本只在 before-quit 里 .kill()，但 Task Manager 强杀 / Electron 崩溃 / 断电
// 都不走 before-quit，留下 python 占着 8000。补 will-quit / quit / 进程信号，
// Windows 上用 taskkill /F /T 杀整棵树（包括未来 uvicorn 起的 reloader 等子进程）。
function killCrawlerTreeSync() {
  const proc = crawlerProcess;
  if (!proc || proc.killed) return;
  const pid = proc.pid;
  if (pid) {
    if (process.platform === 'win32') {
      // taskkill 是独立进程，fire-and-forget；父进程可以接着退出
      try { spawn('taskkill', ['/F', '/T', '/PID', String(pid)], { windowsHide: true, detached: true }); } catch {}
    } else {
      try { process.kill(pid, 'SIGTERM'); } catch {}
    }
  }
  // 兜底：也调用 Node 的 .kill() 走 TerminateProcess
  try { proc.kill(); } catch {}
}
for (const ev of ['before-quit', 'will-quit', 'quit']) {
  app.on(ev, killCrawlerTreeSync);
}
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP', 'SIGBREAK']) {
  process.on(sig, () => {
    killCrawlerTreeSync();
    // 不要再调 app.quit()，避免递归；让进程自然退出
  });
}
// 最后一道防线：进程退出前再尝试一次
process.on('exit', killCrawlerTreeSync);
