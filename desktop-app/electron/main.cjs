const { app, BrowserWindow, Menu, ipcMain, dialog, shell, clipboard, nativeImage, protocol, net } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');

const isDev = !app.isPackaged;
const repoRoot = path.resolve(__dirname, '..', '..');
const hotPicDir = path.join(repoRoot, 'hot_pic');
const presetDirs = [
  path.join(repoRoot, 'pic_web', 'present'),
  path.join(repoRoot, 'mosaic_qt', 'present')
];
const crawlerApiBase = 'http://127.0.0.1:8000';

let crawlerProcess = null;
let crawlerStartPromise = null;
let crawlerStdout = [];
let crawlerLastError = '';
let thumbCacheDir = '';

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
    roots.push({
      id,
      label: (typeof entry === 'object' && (entry.label || entry.name)) || path.basename(resolved) || id,
      path: resolved,
      isDefault: false
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
function loadLocalCustomDict() {
  const dictPath = path.join(repoRoot, 'custom_translation.json');
  if (!localCustomDict && fs.existsSync(dictPath)) {
    try {
      localCustomDict = JSON.parse(fs.readFileSync(dictPath, 'utf-8'));
    } catch (e) {
      localCustomDict = {};
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
  const win = new BrowserWindow({
    width: 1540,
    height: 980,
    minWidth: 1240,
    minHeight: 760,
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
  // 默认最大化：填满屏幕工作区，保留标题栏/任务栏，可正常还原。
  // 上面的 width/height 作为用户点「还原」后的窗口尺寸。
  win.maximize();

  // 关闭拦截：右上角 × / Alt+F4 / 系统菜单 关闭时弹出异步确认对话框，
  // 避免后台抓图任务被误关打断。用户确认后再真正关闭。
  let confirmingClose = false;
  win.on('close', (event) => {
    if (win.__forceClose) return;
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

function listDateFolders() {
  const dates = new Set();
  for (const root of loadLibraryRoots()) {
    if (!fs.existsSync(root.path)) continue;
    for (const item of fs.readdirSync(root.path, { withFileTypes: true })) {
      if (item.isDirectory() && isDateFolder(item.name)) dates.add(item.name);
    }
  }
  return Array.from(dates).sort((a, b) => b.localeCompare(a));
}

function resolveDate(requestedDate) {
  const availableDates = listDateFolders();
  const today = getTodayString();
  if (requestedDate && availableDates.includes(requestedDate)) {
    return { selectedDate: requestedDate, availableDates, today };
  }
  if (availableDates.includes(today)) {
    return { selectedDate: today, availableDates, today };
  }
  return { selectedDate: availableDates[0] || today, availableDates, today };
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
  const { selectedDate, availableDates, today } = resolveDate(requestedDate);
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

  return { selectedDate, availableDates, today, libraryRoots: loadLibraryRoots(), images };
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
  return [
    '-u',
    '-c',
    "import uvicorn; uvicorn.run('main:app', host='127.0.0.1', port=8000, reload=False, access_log=False, log_level='warning')"
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

async function ensureCrawlerService() {
  try {
    await apiFetchJson('/api/status');
    return { ok: true, alreadyRunning: true };
  } catch {
    // continue
  }

  if (crawlerStartPromise) {
    await crawlerStartPromise;
    return { ok: true, alreadyRunning: false };
  }

  crawlerStartPromise = (async () => {
    const python = getPythonCommand();
    if (!python) {
      throw new Error('未找到可用的 Python 解释器');
    }

    crawlerLastError = '';
    crawlerStdout = [];
    crawlerProcess = spawn(python.command, [...python.args, ...getPythonSpawnArgs()], {
      cwd: repoRoot,
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8'
      }
    });

    crawlerProcess.stdout.on('data', chunk => {
      const text = chunk.toString();
      crawlerStdout.push(...text.split(/\r?\n/).filter(Boolean).slice(-20));
      crawlerStdout = crawlerStdout.slice(-200);
    });

    crawlerProcess.stderr.on('data', chunk => {
      const text = chunk.toString();
      crawlerLastError = text.trim();
      crawlerStdout.push(...text.split(/\r?\n/).filter(Boolean).slice(-20));
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
      throw new Error(crawlerLastError || '抓虫服务启动超时');
    }
  })();

  try {
    await crawlerStartPromise;
    return { ok: true, alreadyRunning: false };
  } finally {
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
// nativeImage.createFromPath 支持 PNG/JPG/BMP/TIFF；GIF 取首帧；WebP/AVIF 看系统支持。
// 不支持的格式（动图 zip / avif 在某些 Windows 上）返回 ok:false，由前端 fallback。
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
  try {
    let image = nativeImage.createFromPath(resolved);
    if (image.isEmpty()) {
      return {
        ok: false,
        error: '该图片格式无法转成剪贴板图像（可能是 GIF/WebP/AVIF），请直接把文件拖进 LLM 对话框'
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

app.whenReady().then(() => {
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

app.on('before-quit', () => {
  if (crawlerProcess && !crawlerProcess.killed) {
    crawlerProcess.kill();
  }
});
