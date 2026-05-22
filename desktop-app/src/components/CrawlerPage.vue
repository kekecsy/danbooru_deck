<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue';
import GalleryCalendar from './GalleryCalendar.vue';

const emit = defineEmits(['edit-image']);

const savedHabitsStr = localStorage.getItem('crawlerHabits') || '{}';
const habits = JSON.parse(savedHabitsStr);

// SFW 开关：默认 true（走 safebooru，过滤 R-18）。toggle 立即推到后端 + 写盘
const safeMode = ref(habits.safeMode !== false);
async function syncSafeModeToBackend() {
  try { await window.desktopAPI.crawler.setSafeMode(safeMode.value); }
  catch (e) { /* ensureService 还没完成时静默，onMounted 会重试一次 */ }
}
async function toggleSafeMode() {
  safeMode.value = !safeMode.value;
  habits.safeMode = safeMode.value;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
  await syncSafeModeToBackend();
  showToast(safeMode.value
    ? '已切到 SFW：所有请求走 safebooru.donmai.us（无 R-18）'
    : '已切到全部内容：请求走 danbooru.donmai.us（含 NSFW）',
    safeMode.value ? 'success' : 'warning');
}

const form = ref({
  startPage: habits.rank_start || 1,
  endPage: habits.rank_end || 16,
  // 用 typeof 判断而不是 || ：用户可能故意把过滤标签清空（=不过滤任何 tag），
  // 那种情况下应保留空串而不是回退到默认 "furry, futanari"
  tags: typeof habits.tags === 'string' ? habits.tags : 'furry, futanari',
  mode: habits.mode || 'rank',
  targetDate: '',
  startDate: '',
  endDate: '',
  idsText: ''
});

watch(() => form.value.mode, (newMode) => {
  if (['rank', 'collect_ids'].includes(newMode)) {
    form.value.startPage = habits[`${newMode}_start`] || 1;
    form.value.endPage = habits[`${newMode}_end`] || 16;
  } else if (['popular', 'popular_range'].includes(newMode)) {
    form.value.startPage = habits[`${newMode}_start`] || 1;
    form.value.endPage = habits[`${newMode}_end`] || 35;
  }
});

// 过滤标签单独写一个 watcher，确保即使用户清空成 "" 也立刻写盘
watch(() => form.value.tags, (v) => {
  habits.tags = typeof v === 'string' ? v : '';
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
});

watch(form, (newForm) => {
  habits.mode = newForm.mode;
  habits.tags = newForm.tags;
  habits[`${newForm.mode}_start`] = newForm.startPage;
  habits[`${newForm.mode}_end`] = newForm.endPage;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
}, { deep: true });

const gallery = ref({
  selectedDate: '',
  availableDates: [],
  today: '',
  images: [],
  search: '',
  filterFormat: 'all',
  sortBy: habits.sortBy || 'default',
  hotOnly: false,
  hotThreshold: habits.hotThreshold || 50,
  // 固定 15/页：30/60/120 会同时渲染太多卡片，缩略图 IPC + 网格布局都会卡
  pageSize: 15,
  cardSize: habits.cardSize || 150,
  page: 1
});

watch(() => [gallery.value.sortBy, gallery.value.hotThreshold, gallery.value.cardSize], () => {
  habits.sortBy = gallery.value.sortBy;
  habits.hotThreshold = gallery.value.hotThreshold;
  habits.cardSize = gallery.value.cardSize;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
});

const task = ref({
  isRunning: false,
  isPaused: false,
  logs: ['桌面端已启动。'],
  totalLogCount: 1,
  backendError: '',
  backendErrorExpanded: false,
  backendTail: [],
  showLogs: false,
  maximized: false,
  hideSuccess: true,
  expandedLogIdx: -1
});
const viewer = ref({
  open: false,
  index: 0,
  imageUrl: '',
  zoom: 1,
  fitMode: habits.viewerFitMode === 'actual' ? 'actual' : 'fit',
  toolbarPinned: habits.viewerToolbarPinned === true,
  toolbarHovered: false
});

const viewerToolbarVisible = computed(() => viewer.value.toolbarPinned || viewer.value.toolbarHovered);

function toggleViewerFitMode() {
  viewer.value.fitMode = viewer.value.fitMode === 'fit' ? 'actual' : 'fit';
  viewer.value.zoom = 1;
  habits.viewerFitMode = viewer.value.fitMode;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
}

function toggleViewerToolbarPin() {
  viewer.value.toolbarPinned = !viewer.value.toolbarPinned;
  habits.viewerToolbarPinned = viewer.value.toolbarPinned;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
}

function onViewerMouseMove(event) {
  if (viewer.value.toolbarPinned) return;
  viewer.value.toolbarHovered = event.clientY < 160;
}

// ---------------- 多选/分享 ----------------
const SELECTION_KEY = 'crawlerSelection';
function loadSelection() {
  try {
    const s = JSON.parse(localStorage.getItem(SELECTION_KEY) || '{}');
    return {
      enabled: !!s.enabled,
      ids: new Set(Array.isArray(s.ids) ? s.ids.map(String) : [])
    };
  } catch { return { enabled: false, ids: new Set() }; }
}
const _initSel = loadSelection();
const selection = ref({
  enabled: _initSel.enabled,
  ids: _initSel.ids
});
function persistSelection() {
  try {
    localStorage.setItem(SELECTION_KEY, JSON.stringify({
      enabled: selection.value.enabled,
      ids: Array.from(selection.value.ids)
    }));
  } catch { /* noop */ }
}
function extractPostId(item) {
  if (!item) return '';
  const url = item.postUrl || '';
  const m = url.match(/\/posts\/(\d+)/);
  return m ? m[1] : '';
}
function isItemSelected(item) {
  const id = extractPostId(item);
  return !!id && selection.value.ids.has(id);
}
function toggleItemSelection(item) {
  const id = extractPostId(item);
  if (!id) { showToast('该图片没有 Post ID，无法加入选择', 'warning'); return; }
  if (selection.value.ids.has(id)) selection.value.ids.delete(id);
  else selection.value.ids.add(id);
  persistSelection();
}
function setSelectionEnabled(v) {
  selection.value.enabled = !!v;
  persistSelection();
}
function clearSelection() {
  selection.value.ids.clear();
  persistSelection();
}
// 压缩 IDs：排序后保存"首 ID + 后续 delta"，每个数转 base36，点号分隔
// 例：[11429753, 11430253] → "dbids:6tewdt.dw"（~25% 收益，多 ID 时 65%+）
function compressIds(idStrings) {
  const nums = Array.from(idStrings).map(s => Number(s)).filter(n => Number.isFinite(n) && n > 0);
  if (!nums.length) return '';
  nums.sort((a, b) => a - b);
  const dedup = [];
  let last = -1;
  for (const n of nums) {
    if (n !== last) { dedup.push(n); last = n; }
  }
  const parts = [dedup[0].toString(36)];
  for (let i = 1; i < dedup.length; i++) {
    parts.push((dedup[i] - dedup[i - 1]).toString(36));
  }
  return 'dbids:' + parts.join('.');
}
function decompressIds(text) {
  // 返回 string[] 或 null（不是压缩格式时）
  const m = String(text || '').match(/dbids:([0-9a-z.]+)/i);
  if (!m) return null;
  const parts = m[1].split('.').filter(Boolean);
  if (!parts.length) return null;
  const result = [];
  let cur = 0;
  for (let i = 0; i < parts.length; i++) {
    const v = parseInt(parts[i], 36);
    if (!Number.isFinite(v) || v < 0) return null;
    cur = i === 0 ? v : cur + v;
    if (cur <= 0) return null;
    result.push(String(cur));
  }
  return result;
}

async function copySelectedIds() {
  const ids = Array.from(selection.value.ids);
  if (!ids.length) { showToast('还没有勾选任何图片', 'warning'); return; }
  const text = ids.join(',');
  try {
    await navigator.clipboard.writeText(text);
    showToast(`已复制 ${ids.length} 个 ID（${text.length} 字符）`, 'success');
  } catch (e) {
    showToast(`复制失败: ${e.message}`, 'error');
  }
}

// ID 压缩工具（独立面板）：把任意 IDs 文本压成 dbids:... ，或反向解出明文
const cryptoTool = ref({ open: false, input: '', output: '' });
function openCryptoTool() {
  cryptoTool.value.open = true;
}
function closeCryptoTool() {
  cryptoTool.value.open = false;
}
function loadSelectionToCryptoInput() {
  const ids = Array.from(selection.value.ids);
  if (!ids.length) { showToast('当前没有已选图片', 'warning'); return; }
  cryptoTool.value.input = ids.join(',');
}
function cryptoEncrypt() {
  const ids = parsePastedIds(cryptoTool.value.input);
  if (!ids.length) { showToast('没解析到任何 ID', 'warning'); return; }
  const out = compressIds(ids);
  cryptoTool.value.output = out;
  const savedPct = cryptoTool.value.input.length > 0
    ? Math.round((1 - out.length / cryptoTool.value.input.length) * 100)
    : 0;
  showToast(`加密完成 · ${ids.length} 个 ID · ${out.length} 字符（比输入省 ${savedPct >= 0 ? savedPct : 0}%）`, 'success');
}
function cryptoDecrypt() {
  const decoded = decompressIds(cryptoTool.value.input);
  if (!decoded || !decoded.length) {
    // 不是压缩格式：尝试明文解析，让用户也能用来"规范化/去重"
    const fallback = parsePastedIds(cryptoTool.value.input);
    if (!fallback.length) { showToast('没解析到任何 ID', 'warning'); return; }
    cryptoTool.value.output = fallback.join(',');
    showToast(`输入是明文，已规范化为 ${fallback.length} 个 ID（${cryptoTool.value.output.length} 字符）`, 'info');
    return;
  }
  cryptoTool.value.output = decoded.join(',');
  showToast(`解密完成 · ${decoded.length} 个 ID（${cryptoTool.value.output.length} 字符）`, 'success');
}
async function copyCryptoOutput() {
  if (!cryptoTool.value.output) { showToast('输出框是空的', 'warning'); return; }
  try {
    await navigator.clipboard.writeText(cryptoTool.value.output);
    showToast(`已复制输出（${cryptoTool.value.output.length} 字符）`, 'success');
  } catch (e) {
    showToast(`复制失败: ${e.message}`, 'error');
  }
}
function swapCryptoIO() {
  const tmp = cryptoTool.value.input;
  cryptoTool.value.input = cryptoTool.value.output;
  cryptoTool.value.output = tmp;
}
function parsePastedIds(text) {
  if (!text) return [];
  // 优先识别压缩格式
  const decompressed = decompressIds(text);
  if (decompressed && decompressed.length) return decompressed;
  // 回退：从任意文本中抠出 3 位以上数字（兼容旧的逗号/空格/换行/URL 混合）
  const matches = String(text).match(/\d{3,}/g) || [];
  return Array.from(new Set(matches));
}
const parsedPastedIds = computed(() => parsePastedIds(form.value.idsText));
const isPastedCompressed = computed(() => /dbids:[0-9a-z.]+/i.test(form.value.idsText || ''));
function onThumbClick(event, item) {
  if (event.ctrlKey || event.metaKey) {
    if (!selection.value.enabled) setSelectionEnabled(true);
    toggleItemSelection(item);
    return;
  }
  openViewer(item);
}

// 只看已选 / 已选清单 / 翻页选择器
const showOnlySelected = ref(false);
const selectionListOpen = ref(false);
const pagePicker = ref({ open: false });

// 已选清零时自动关掉「只看已选」，否则界面会一张图都不剩且按钮被禁用导致卡死
watch(() => selection.value.ids.size, (size) => {
  if (!size && showOnlySelected.value) showOnlySelected.value = false;
});

function removeFromSelection(id) {
  selection.value.ids.delete(id);
  persistSelection();
}

// 当前日期里能找到的已选 ID → 第几页 & item 映射
const selectionIndex = computed(() => {
  const map = new Map();
  const list = filteredLocalImages.value;
  const ps = gallery.value.pageSize || 1;
  for (let i = 0; i < list.length; i++) {
    const id = extractPostId(list[i]);
    if (id && selection.value.ids.has(id)) {
      map.set(id, { item: list[i], page: Math.floor(i / ps) + 1, indexInFiltered: i });
    }
  }
  return map;
});
const selectionInCurrentDate = computed(() => {
  const idx = selectionIndex.value;
  return Array.from(selection.value.ids).filter(id => idx.has(id)).map(id => ({
    id,
    ...idx.get(id)
  }));
});
const selectionOtherDates = computed(() => {
  const idx = selectionIndex.value;
  return Array.from(selection.value.ids).filter(id => !idx.has(id));
});

function jumpToSelected(id) {
  const found = selectionIndex.value.get(id);
  if (!found) return;
  gallery.value.page = found.page;
  selectionListOpen.value = false;
}

const refresh = ref({
  isRunning: false,
  done: 0,
  total: 0,
  dateStr: ''
});

// 按 score / 收藏数 排序时锁定排序的快照：
// 用户点开一张卡片会触发 refreshSinglePost 更新该卡片的 score/favCount，
// 没有快照时 computed 会立刻重排，把刚点的卡片挤到别处。
// 解决方案：把"当前的排序序号"按 filename 记下来，computed 优先用快照的位置，
// 不在快照里的（例如新下载的图）排到末尾，等用户主动点"重新排序"才更新快照。
const sortSnapshot = ref({ key: '', positions: new Map() });

function rebuildSortSnapshot() {
  const sortBy = gallery.value.sortBy;
  if (sortBy !== 'score' && sortBy !== 'fav') {
    sortSnapshot.value = { key: '', positions: new Map() };
    return;
  }
  const sorted = [...gallery.value.images];
  if (sortBy === 'score') {
    sorted.sort((a, b) => (b.score || 0) - (a.score || 0));
  } else {
    sorted.sort((a, b) => (b.favCount || 0) - (a.favCount || 0));
  }
  const positions = new Map();
  sorted.forEach((item, idx) => {
    if (item.filename) positions.set(item.filename, idx);
  });
  sortSnapshot.value = { key: sortBy, positions };
}

const toast = ref({
  show: false,
  msg: '',
  type: 'info'
});

function showToast(msg, type = 'info') {
  toast.value = { show: true, msg, type };
  setTimeout(() => { toast.value.show = false; }, 3000);
}

const loadingGallery = ref(false);
let pollTimer = null;
const logBodyRef = ref(null);

watch(() => task.value.totalLogCount, async () => {
  if (task.value.showLogs && logBodyRef.value) {
    await nextTick();
    logBodyRef.value.scrollTop = logBodyRef.value.scrollHeight;
  }
});

const filteredLocalImages = computed(() => {
  const keyword = gallery.value.search.trim().toLowerCase();
  const format = gallery.value.filterFormat;
  const hotOnly = gallery.value.hotOnly;
  const threshold = gallery.value.hotThreshold;
  const source = gallery.value.images;

  let result = source.filter(item => {
    if (format !== 'all') {
      const ext = (item.filename || '').split('.').pop().toLowerCase();
      if (format === 'zip' && !['zip', 'gif'].includes(ext)) return false;
      if (format === 'video' && !['mp4', 'webm', 'avi', 'mov', 'mkv'].includes(ext)) return false;
      if (format === 'image' && !['jpg', 'jpeg', 'png', 'webp', 'bmp', 'avif'].includes(ext)) return false;
    }

    if (hotOnly && (item.score || 0) < threshold) return false;

    if (showOnlySelected.value) {
      const id = extractPostId(item);
      if (!id || !selection.value.ids.has(id)) return false;
    }

    if (!keyword) return true;
    const artistMatch = (item.artist || '').toLowerCase().includes(keyword);
    let charMatch = false;
    if (Array.isArray(item.characters)) {
      charMatch = item.characters.some(c => String(c).toLowerCase().includes(keyword));
    } else {
      charMatch = String(item.characters || '').toLowerCase().includes(keyword);
    }
    return artistMatch || charMatch;
  });

  const sortBy = gallery.value.sortBy;
  if (sortBy === 'score' || sortBy === 'fav') {
    const snap = sortSnapshot.value;
    if (snap.key === sortBy && snap.positions.size > 0) {
      // 使用快照顺序，确保点开卡片刷新热度后排序位置不会乱跳
      result = [...result].sort((a, b) => {
        const pa = snap.positions.has(a.filename) ? snap.positions.get(a.filename) : Number.MAX_SAFE_INTEGER;
        const pb = snap.positions.has(b.filename) ? snap.positions.get(b.filename) : Number.MAX_SAFE_INTEGER;
        return pa - pb;
      });
    } else if (sortBy === 'score') {
      result = [...result].sort((a, b) => (b.score || 0) - (a.score || 0));
    } else {
      result = [...result].sort((a, b) => (b.favCount || 0) - (a.favCount || 0));
    }
  }
  return result;
});

const localTotalPages = computed(() => Math.max(1, Math.ceil(filteredLocalImages.value.length / gallery.value.pageSize)));

const pagedLocalImages = computed(() => {
  const page = Math.min(gallery.value.page, localTotalPages.value);
  const start = (page - 1) * gallery.value.pageSize;
  return filteredLocalImages.value.slice(start, start + gallery.value.pageSize);
});

const activeItems = computed(() => pagedLocalImages.value);
const activeCount = computed(() => filteredLocalImages.value.length);
const activeTotalPages = computed(() => localTotalPages.value);
const viewerItems = computed(() => filteredLocalImages.value);
const viewerItem = computed(() => viewerItems.value[viewer.value.index] || null);
const VIDEO_EXTS = ['mp4', 'webm', 'avi', 'mov', 'mkv'];
function extOf(filename) {
  return (filename || '').split('.').pop().toLowerCase();
}
const viewerIsVideo = computed(() => VIDEO_EXTS.includes(extOf(viewerItem.value?.filename)));
const activePage = computed({
  get() {
    return gallery.value.page;
  },
  set(value) {
    gallery.value.page = value;
  }
});

const pageNumbers = computed(() => {
  const total = activeTotalPages.value;
  const cur = activePage.value;
  if (total <= 9) return Array.from({ length: total }, (_, i) => i + 1);
  // 中间窗口默认覆盖 cur±2 共 5 个；贴边时把窗口往里挪，保证至少看到 5 个连号
  let start = Math.max(2, cur - 2);
  let end = Math.min(total - 1, cur + 2);
  if (cur <= 4) { start = 2; end = 6; }
  else if (cur >= total - 3) { start = total - 5; end = total - 1; }
  const out = [1];
  if (start > 2) out.push('…');
  for (let i = start; i <= end; i++) out.push(i);
  if (end < total - 1) out.push('…');
  out.push(total);
  return out;
});

const galleryStats = computed(() => {
  const all = gallery.value.images;
  const filtered = filteredLocalImages.value;
  const scores = filtered.map(i => i.score || 0).filter(s => s > 0);
  const avg = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : 0;
  const sorted = [...scores].sort((a, b) => a - b);
  const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
  return { total: all.length, filtered: filtered.length, avg, median };
});

const jumpInput = ref(1);
function doJump() {
  const n = Math.max(1, Math.min(activeTotalPages.value, jumpInput.value || 1));
  activePage.value = n;
  jumpInput.value = n;
}
function gotoPage(n) {
  if (typeof n !== 'number') return;
  activePage.value = Math.max(1, Math.min(activeTotalPages.value, n));
}

function appendLog(message) {
  if (!message) return;
  task.value.logs.push(message);
  task.value.totalLogCount += 1;
  // 保留最近 500 条用于渲染，避免 DOM 节点过多导致卡顿；
  // totalLogCount 单独记录运行至今的全量数量，避免“计数停在 320”的错觉
  if (task.value.logs.length > 500) {
    task.value.logs = task.value.logs.slice(-500);
  }
}

function clearLogs() {
  task.value.logs = [];
  task.value.totalLogCount = 0;
  task.value.expandedLogIdx = -1;
}

function toggleLogExpand(idx) {
  task.value.expandedLogIdx = task.value.expandedLogIdx === idx ? -1 : idx;
}

function dismissBackendError() {
  task.value.backendError = '';
  task.value.backendErrorExpanded = false;
}

function toggleLogMaximize() {
  task.value.maximized = !task.value.maximized;
  // 放大时强制展开，否则只看到一个空的 header 没意义
  if (task.value.maximized) task.value.showLogs = true;
}

function toggleLogHeader() {
  // 放大态下不允许通过 header 折叠 —— 折叠会让人误以为日志消失
  if (task.value.maximized) return;
  task.value.showLogs = !task.value.showLogs;
}

// 按行匹配“下载完成 / 文件已存在 / 正在下载 ...”这类逐张日志，
// 用户表示更关心异常和概要，因此默认收起这些噪音。
const SUCCESS_NOISE_PATTERNS = [
  /^正在下载[:：]/,
  /^下载完成[:：]/,
  /^文件已存在[:：]/
];

function isSuccessNoise(line) {
  if (!line) return false;
  return SUCCESS_NOISE_PATTERNS.some(re => re.test(line));
}

const visibleLogs = computed(() => {
  if (!task.value.hideSuccess) {
    return task.value.logs.map((line, idx) => ({ line, idx }));
  }
  const out = [];
  for (let i = 0; i < task.value.logs.length; i += 1) {
    const line = task.value.logs[i];
    if (isSuccessNoise(line)) continue;
    out.push({ line, idx: i });
  }
  return out;
});

const hiddenSuccessCount = computed(() => {
  if (!task.value.hideSuccess) return 0;
  return task.value.logs.filter(isSuccessNoise).length;
});

function splitTags(value) {
  return String(value || '').split(' ').map(item => item.trim()).filter(Boolean);
}

function mergeBackendLogs(lines) {
  const normalized = (lines || []).filter(Boolean);
  if (!normalized.length) return;
  const prev = task.value.backendTail;
  let start = 0;

  if (prev.length) {
    const maxOverlap = Math.min(prev.length, normalized.length);
    for (let overlap = maxOverlap; overlap > 0; overlap -= 1) {
      const prevSlice = prev.slice(prev.length - overlap).join('\n');
      const nextSlice = normalized.slice(0, overlap).join('\n');
      if (prevSlice === nextSlice) {
        start = overlap;
        break;
      }
    }
  }

  normalized.slice(start).forEach(appendLog);
  task.value.backendTail = normalized.slice(-80);
}

function generateFormatPlaceholder(ext) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300" viewBox="0 0 200 300">
    <rect width="200" height="300" fill="#2c2e33"/>
    <text x="100" y="150" font-family="sans-serif" font-size="36" font-weight="bold" fill="#6c707a" text-anchor="middle" dominant-baseline="middle">${ext.toUpperCase()}</text>
  </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

async function hydrateThumbs(items) {
  await Promise.all(items.map(async item => {
    if (item.thumbUrl) return;
    
    const ext = (item.filename || '').split('.').pop().toLowerCase();
    
    if (ext === 'zip' && item.localPath) {
      const gifPath = item.localPath.replace(/\.zip$/i, '.gif');
      if (await window.desktopAPI.file.exists(gifPath)) {
        item.thumbUrl = await window.desktopAPI.file.toLocalUrl(gifPath);
        return;
      }
    }

    const isImage = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'avif'].includes(ext);

    if (!isImage) {
      item.thumbUrl = generateFormatPlaceholder(ext);
      return;
    }

    if (item.localPath) {
      item.thumbUrl = await window.desktopAPI.file.toLocalUrl(item.localPath);
      return;
    }

    const webUrl = item.web_url || item.webUrl;
    if (webUrl) {
      item.thumbUrl = `http://127.0.0.1:8000${webUrl}`;
      return;
    }
  }));
}

async function loadGallery(date, silent = false) {
  if (!silent) loadingGallery.value = true;
  try {
    const data = await window.desktopAPI.gallery.getByDate(date || gallery.value.selectedDate);
    const normalizedImages = data.images.map(item => ({
      ...item,
      thumbUrl: '',
      artistTokens: splitTags(item.artist),
      characterTokens: Array.isArray(item.characters) ? item.characters : splitTags(item.characters)
    }));
    gallery.value.selectedDate = data.selectedDate;
    gallery.value.availableDates = data.availableDates;
    gallery.value.today = data.today;
    gallery.value.images = normalizedImages;
    gallery.value.page = 1;
    rebuildSortSnapshot();
    await hydrateThumbs(pagedLocalImages.value);
  } finally {
    if (!silent) loadingGallery.value = false;
  }
}

async function syncStatus() {
  try {
    const status = await window.desktopAPI.crawler.status();
    const wasRunning = task.value.isRunning;
    
    task.value.isRunning = !!status.is_running;
    task.value.isPaused = !!status.is_paused;
    task.value.backendError = status.backendError || '';

    mergeBackendLogs(status.backendLogs);
    (status.new_logs || []).forEach(appendLog);

    if (status.new_images?.length) {
      if (gallery.value.selectedDate === gallery.value.today) {
        const appended = status.new_images.map(item => ({
          ...item,
          thumbUrl: '',
          artistTokens: splitTags(item.artist),
          characterTokens: Array.isArray(item.characters) ? item.characters : splitTags(item.characters)
        }));
        // Add new images to the top to prevent wiping DOM and scroll state
        gallery.value.images.unshift(...appended);
        
        // Remove duplicates just in case
        const seen = new Set();
        gallery.value.images = gallery.value.images.filter(img => {
          const key = img.localPath || img.filename;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });

        await hydrateThumbs(pagedLocalImages.value);
      }
    }

    if (wasRunning && !task.value.isRunning) {
      if (status.backendError || task.value.backendError) {
        showToast("抓取任务异常停止！", "error");
      } else {
        showToast("抓取任务已完成！", "success");
      }
      await loadGallery(gallery.value.selectedDate);
    }
  } catch (error) {
    task.value.backendError = error.message;
    appendLog(`状态同步失败: ${error.message}`);
  }
}

async function ensureService() {
  try {
    const result = await window.desktopAPI.crawler.ensureService();
    appendLog(result.alreadyRunning ? '抓虫服务已连接。' : '抓虫服务已启动。');
  } catch (error) {
    task.value.backendError = error.message;
    appendLog(`抓虫服务启动失败: ${error.message}`);
  }
}

async function startTask() {
  try {
    const payload = {
      start_page: Number(form.value.startPage) || 1,
      end_page: Number(form.value.endPage) || 1,
      tags: form.value.tags || '',
      mode: form.value.mode || 'rank',
      target_date: form.value.targetDate || '',
      start_date: form.value.startDate || '',
      end_date: form.value.endDate || ''
    };
    if (payload.mode === 'download_ids') {
      const ids = parsePastedIds(form.value.idsText);
      if (ids.length) payload.ids = ids;
    }
    const result = await window.desktopAPI.crawler.start(payload);
    appendLog(result.msg || '已发送启动抓图请求。');
    await syncStatus();
  } catch (error) {
    task.value.backendError = error.message;
    appendLog(`启动失败: ${error.message}`);
  }
}

async function pauseTask() {
  try {
    const result = await window.desktopAPI.crawler.pause();
    appendLog(result.msg || '已发送暂停请求。');
    await syncStatus();
  } catch (error) {
    appendLog(`暂停失败: ${error.message}`);
  }
}

async function resumeTask() {
  try {
    const result = await window.desktopAPI.crawler.resume();
    appendLog(result.msg || '已发送继续请求。');
    await syncStatus();
  } catch (error) {
    appendLog(`继续失败: ${error.message}`);
  }
}

async function stopTask() {
  try {
    const result = await window.desktopAPI.crawler.stop();
    appendLog(result.msg || '已发送停止请求。');
    await syncStatus();
  } catch (error) {
    appendLog(`停止失败: ${error.message}`);
  }
}

async function openLocal(item) {
  await window.desktopAPI.gallery.openLocalFile(item.localPath);
}

async function openOriginal(item) {
  if (!item.postUrl) return;
  await window.desktopAPI.external.open(item.postUrl);
}

async function convertGif(item) {
  if (!item.localPath) {
    showToast("找不到本地路径", "error");
    return;
  }
  showToast("正在转换为 GIF...", "info");
  try {
    const res = await fetch('http://127.0.0.1:8000/api/convert_local_zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ local_path: item.localPath })
    });
    const result = await res.json();
    if (result.ok) {
      showToast("转换成功，正在打开...", "success");
      await window.desktopAPI.gallery.openLocalFile(result.gif_path);
    } else {
      showToast("转换失败: " + result.msg, "error");
    }
  } catch (err) {
    showToast("请求失败: " + err.message, "error");
  }
}

async function startRefreshScores() {
  // 刷新「当前展示页」而不是整日 —— 一次只调 15~30 张，避免被风控；
  // 同时这条路径会把孤立文件（之前下载到本地但没有 viewer_data 条目的图）
  // 通过后端的 log.json 反查补全 artist / 热度信息。
  const date = gallery.value.selectedDate;
  if (!date) {
    showToast('请先选择日期', 'error');
    return;
  }
  if (refresh.value.isRunning) {
    showToast('已有刷新任务在运行', 'info');
    return;
  }
  const pageItems = pagedLocalImages.value;
  if (!pageItems.length) {
    showToast('当前页没有图片', 'info');
    return;
  }
  const filenames = pageItems.map(it => it.filename).filter(Boolean);

  refresh.value.isRunning = true;
  refresh.value.dateStr = date;
  refresh.value.total = filenames.length;
  refresh.value.done = 0;
  showToast(`正在刷新当前页 ${filenames.length} 张...`, 'info');

  try {
    const res = await fetch('http://127.0.0.1:8000/api/refresh_visible', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, filenames })
    });
    const result = await res.json();
    if (!result.ok) {
      showToast(result.msg || '刷新失败', 'error');
      return;
    }
    let okCount = 0;
    let failCount = 0;
    for (const u of result.updates || []) {
      if (!u.ok) { failCount += 1; continue; }
      const target = gallery.value.images.find(img => img.filename === u.filename);
      if (target) {
        applyRefreshUpdate(target, u);
        okCount += 1;
      }
      refresh.value.done = okCount + failCount;
    }
    if (failCount > 0) {
      showToast(`已刷新 ${okCount} 张，${failCount} 张失败`, okCount > 0 ? 'info' : 'error');
    } else {
      showToast(`已刷新 ${okCount} 张`, 'success');
    }
  } catch (err) {
    showToast(`请求失败: ${err.message}`, 'error');
  } finally {
    refresh.value.isRunning = false;
  }
}

function applyRefreshUpdate(target, u) {
  target.score = u.score;
  target.favCount = u.fav_count;
  if (u.artist) {
    target.artist = u.artist;
    target.artistTokens = splitTags(u.artist);
  }
  if (u.post_url) target.postUrl = u.post_url;
  if (Array.isArray(u.characters)) {
    target.characters = u.characters;
    target.characterTokens = u.characters;
  }
  if (u.tags) target.tags = { ...(target.tags || {}), ...u.tags };
}

// ---------------- 刷新指定范围页的热度 ----------------
const rangeRefresh = ref({
  open: false,
  startPage: 1,
  endPage: 1,
});

function openRangeRefreshDialog() {
  if (!gallery.value.selectedDate) {
    showToast('请先选择日期', 'error');
    return;
  }
  if (refresh.value.isRunning) {
    showToast('已有刷新任务在运行', 'info');
    return;
  }
  rangeRefresh.value.open = true;
  // 默认从当前页开始，向后多刷几页（不超过总页数）
  const total = activeTotalPages.value;
  const cur = activePage.value;
  rangeRefresh.value.startPage = Math.max(1, Math.min(total, cur));
  rangeRefresh.value.endPage = Math.max(rangeRefresh.value.startPage, Math.min(total, cur + 4));
}

function closeRangeRefreshDialog() {
  rangeRefresh.value.open = false;
}

function refreshAllPages() {
  if (!gallery.value.selectedDate) { showToast('请先选择日期', 'error'); return; }
  if (refresh.value.isRunning) { showToast('已有刷新任务在运行', 'info'); return; }
  const total = activeTotalPages.value;
  if (!total) { showToast('当前没有可刷新的图片', 'info'); return; }
  // 直接把范围拉满走节流路径（>5 页自动每 4 页休 40s）
  rangeRefresh.value.startPage = 1;
  rangeRefresh.value.endPage = total;
  startRefreshScoresRange();
}

const rangeRefreshCount = computed(() => {
  const total = activeTotalPages.value;
  if (!total) return 0;
  const start = Math.max(1, Math.min(total, rangeRefresh.value.startPage || 1));
  const end = Math.max(start, Math.min(total, rangeRefresh.value.endPage || start));
  const ps = gallery.value.pageSize;
  return filteredLocalImages.value.slice((start - 1) * ps, end * ps).length;
});

async function startRefreshScoresRange() {
  const date = gallery.value.selectedDate;
  if (!date) { showToast('请先选择日期', 'error'); return; }
  if (refresh.value.isRunning) { showToast('已有刷新任务在运行', 'info'); return; }

  const total = activeTotalPages.value;
  const start = Math.max(1, Math.min(total, rangeRefresh.value.startPage || 1));
  const end = Math.max(start, Math.min(total, rangeRefresh.value.endPage || start));
  const pageCount = end - start + 1;
  const ps = gallery.value.pageSize;
  const items = filteredLocalImages.value.slice((start - 1) * ps, end * ps);
  const filenames = items.map(it => it.filename).filter(Boolean);
  if (!filenames.length) { showToast('范围内没有图片', 'info'); return; }

  closeRangeRefreshDialog();
  refresh.value.isRunning = true;
  refresh.value.dateStr = date;
  refresh.value.total = filenames.length;
  refresh.value.done = 0;

  // 页数超过 5 时，按每 4 页一批切片，批间休息 40s 防止 Danbooru 风控
  const THROTTLE_THRESHOLD = 5;
  const BATCH_PAGES = 4;
  const REST_MS = 40000;
  const throttled = pageCount > THROTTLE_THRESHOLD;

  const batches = [];
  if (throttled) {
    for (let p = start; p <= end; p += BATCH_PAGES) {
      const pEnd = Math.min(end, p + BATCH_PAGES - 1);
      const sliceItems = filteredLocalImages.value.slice((p - 1) * ps, pEnd * ps);
      const bf = sliceItems.map(it => it.filename).filter(Boolean);
      if (bf.length) batches.push({ pStart: p, pEnd, filenames: bf });
    }
  } else {
    batches.push({ pStart: start, pEnd: end, filenames });
  }

  let okCount = 0;
  let failCount = 0;

  async function runBatch(b, label) {
    if (!b.filenames.length) return;
    if (label) showToast(label, 'info');
    const res = await fetch('http://127.0.0.1:8000/api/refresh_visible', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, filenames: b.filenames }),
    });
    const result = await res.json();
    if (!result.ok) {
      showToast(result.msg || '刷新失败', 'error');
      return;
    }
    for (const u of result.updates || []) {
      if (!u.ok) { failCount += 1; continue; }
      const target = gallery.value.images.find(img => img.filename === u.filename);
      if (target) { applyRefreshUpdate(target, u); okCount += 1; }
      refresh.value.done = okCount + failCount;
    }
  }

  async function sleepCancellable(ms) {
    const step = 500;
    let waited = 0;
    while (waited < ms) {
      if (!refresh.value.isRunning) return;
      await new Promise(r => setTimeout(r, Math.min(step, ms - waited)));
      waited += step;
    }
  }

  try {
    if (throttled) {
      showToast(`共 ${pageCount} 页，将分 ${batches.length} 批刷新（每 ${BATCH_PAGES} 页一批，批间休息 ${REST_MS / 1000}s 防风控）`, 'info');
    } else {
      showToast(`正在刷新第 ${start}-${end} 页共 ${filenames.length} 张...`, 'info');
    }
    for (let bi = 0; bi < batches.length; bi += 1) {
      if (!refresh.value.isRunning) break;
      const b = batches[bi];
      const label = throttled
        ? `批次 ${bi + 1}/${batches.length} · 第 ${b.pStart}-${b.pEnd} 页（${b.filenames.length} 张）`
        : '';
      await runBatch(b, label);
      if (throttled && bi < batches.length - 1 && refresh.value.isRunning) {
        showToast(`已完成 ${okCount}/${refresh.value.total}，休息 ${REST_MS / 1000}s 防风控…`, 'info');
        await sleepCancellable(REST_MS);
      }
    }
    if (failCount > 0) {
      showToast(`已刷新 ${okCount} 张，${failCount} 张失败`, okCount > 0 ? 'info' : 'error');
    } else if (refresh.value.isRunning || okCount) {
      showToast(`已刷新 ${okCount} 张`, 'success');
    } else {
      showToast('已停止刷新', 'info');
    }
  } catch (err) {
    showToast(`请求失败: ${err.message}`, 'error');
  } finally {
    refresh.value.isRunning = false;
  }
}

async function stopRefreshScores() {
  // 现在使用同步的 /api/refresh_visible，没有后台线程可停 —— 保留按钮但只做兜底
  refresh.value.isRunning = false;
  try {
    await fetch('http://127.0.0.1:8000/api/refresh_scores_stop', { method: 'POST' });
  } catch (_) { /* noop */ }
}

// 「刷新热度」下拉菜单：把原先的 3 个按钮（本页/范围/全部）合并到一个入口
const refreshMenu = ref({ open: false });
function toggleRefreshMenu() {
  if (refresh.value.isRunning) {
    stopRefreshScores();
    return;
  }
  if (!gallery.value.selectedDate) {
    showToast('请先选择日期', 'error');
    return;
  }
  refreshMenu.value.open = !refreshMenu.value.open;
}
function onRefreshChoice(scope) {
  refreshMenu.value.open = false;
  if (scope === 'page') startRefreshScores();
  else if (scope === 'range') openRangeRefreshDialog();
  else if (scope === 'all') refreshAllPages();
}
function onDocClickForRefreshMenu(e) {
  if (!refreshMenu.value.open) return;
  const dropdown = document.querySelector('.refresh-dropdown');
  if (dropdown && !dropdown.contains(e.target)) {
    refreshMenu.value.open = false;
  }
}

// 「翻译」下拉菜单：把「翻译角色」和「导入翻译字典」合并成一个入口，
// 这两个都是低频操作，原先两个独立按钮加起来太宽导致工具栏换行
const translateMenu = ref({ open: false });
function toggleTranslateMenu() {
  if (!gallery.value.selectedDate) {
    // 没选日期时直接走文件导入也是合理的，但翻译角色需要日期，
    // 这里先打开菜单让用户选择，菜单里翻译角色项会被 disable
  }
  translateMenu.value.open = !translateMenu.value.open;
}
function onTranslateChoice(action) {
  translateMenu.value.open = false;
  if (action === 'character') openTranslationModal();
  else if (action === 'import') importTranslationFile();
}
function onDocClickForTranslateMenu(e) {
  if (!translateMenu.value.open) return;
  const dropdown = document.querySelector('.translate-dropdown');
  if (dropdown && !dropdown.contains(e.target)) {
    translateMenu.value.open = false;
  }
}
function onDocClickForPagePicker(e) {
  if (!pagePicker.value.open) return;
  const host = document.querySelector('.pg-picker-host');
  if (host && !host.contains(e.target)) {
    pagePicker.value.open = false;
  }
}

async function refreshSinglePost(item) {
  if (!item?.filename) return;
  const date = gallery.value.selectedDate || '';
  if (!date) return;
  try {
    const res = await fetch('http://127.0.0.1:8000/api/refresh_visible', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, filenames: [item.filename] })
    });
    const result = await res.json();
    if (!result.ok) return;
    const u = (result.updates || [])[0];
    if (u && u.ok) applyRefreshUpdate(item, u);
  } catch (_) { /* 静默失败 */ }
}

const hostsModal = ref({ open: false });

function openHostsHint() {
  hostsModal.value.open = true;
}

async function openHostsFolder() {
  await window.desktopAPI.external.open('file:///C:/Windows/System32/drivers/etc/');
}

// ---------------- 角色增量翻译 ----------------
const translationModal = ref({
  open: false,
  loading: false,
  list: [],          // [{tag, post_count, fallback_name}]
  search: '',
  importing: false,
});

const translateDetail = ref({
  open: false,
  tag: '',
  fallbackName: '',
  source: { description: '', other_names: [], exists: false },
  manualPrompt: '',
  mode: 'api',       // 'api' | 'manual'
  apiBusy: false,
  apiError: '',
  apiRaw: '',        // API 失败时原文，灌入 pasteText 让用户修
  pasteText: '',
  parseError: '',
  saving: false,
  fetchBusy: false,
  fetchMsg: '',
  form: {
    has_chinese: true,
    chinese_name: '',
    source_hint: '',
    translated_description_zh: '',
  },
});

const filteredUntranslated = computed(() => {
  const keyword = translationModal.value.search.trim().toLowerCase();
  if (!keyword) return translationModal.value.list;
  return translationModal.value.list.filter(item =>
    item.tag.toLowerCase().includes(keyword) ||
    (item.fallback_name || '').toLowerCase().includes(keyword)
  );
});

async function openTranslationModal() {
  if (!gallery.value.selectedDate) {
    showToast('请先选择日期', 'error');
    return;
  }
  translationModal.value.open = true;
  translationModal.value.loading = true;
  translationModal.value.search = '';
  try {
    const res = await fetch(`http://127.0.0.1:8000/api/untranslated_characters?date=${encodeURIComponent(gallery.value.selectedDate)}`);
    const data = await res.json();
    translationModal.value.list = data.tags || [];
  } catch (err) {
    showToast('加载未翻译列表失败: ' + err.message, 'error');
    translationModal.value.list = [];
  } finally {
    translationModal.value.loading = false;
  }
}

function closeTranslationModal() {
  translationModal.value.open = false;
}

function resetTranslateDetail() {
  translateDetail.value.tag = '';
  translateDetail.value.fallbackName = '';
  translateDetail.value.source = { description: '', other_names: [], exists: false };
  translateDetail.value.manualPrompt = '';
  translateDetail.value.mode = 'api';
  translateDetail.value.apiBusy = false;
  translateDetail.value.apiError = '';
  translateDetail.value.apiRaw = '';
  translateDetail.value.pasteText = '';
  translateDetail.value.parseError = '';
  translateDetail.value.saving = false;
  translateDetail.value.fetchBusy = false;
  translateDetail.value.fetchMsg = '';
  translateDetail.value.form = {
    has_chinese: true,
    chinese_name: '',
    source_hint: '',
    translated_description_zh: '',
  };
}

async function openTranslateDetail(item) {
  resetTranslateDetail();
  translateDetail.value.open = true;
  translateDetail.value.tag = item.tag;
  translateDetail.value.fallbackName = item.fallback_name || item.tag;
  try {
    const res = await fetch(`http://127.0.0.1:8000/api/character_source/${encodeURIComponent(item.tag)}`);
    const data = await res.json();
    translateDetail.value.source = {
      description: data.description || '',
      other_names: data.other_names || [],
      exists: !!data.exists,
    };
    translateDetail.value.manualPrompt = data.manual_prompt || '';
    if (data.fallback_name) translateDetail.value.fallbackName = data.fallback_name;
  } catch (err) {
    showToast('加载角色描述失败: ' + err.message, 'error');
  }
}

function closeTranslateDetail() {
  translateDetail.value.open = false;
}

async function fetchCharacterSource() {
  // 当 character.json 里没有该 tag 时，调用 Danbooru wiki API 在线拉描述
  translateDetail.value.fetchBusy = true;
  translateDetail.value.fetchMsg = '';
  try {
    const res = await fetch('http://127.0.0.1:8000/api/fetch_character_source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag: translateDetail.value.tag }),
    });
    const data = await res.json();
    if (!data.ok) {
      translateDetail.value.fetchMsg = data.msg || 'Danbooru wiki 没有这个 tag';
      showToast(translateDetail.value.fetchMsg, 'error');
      return;
    }
    translateDetail.value.source = {
      description: data.description || '',
      other_names: data.other_names || [],
      exists: true,
    };
    translateDetail.value.manualPrompt = data.manual_prompt || '';
    showToast('已从 Danbooru wiki 拉到描述', 'success');
  } catch (err) {
    translateDetail.value.fetchMsg = err.message;
    showToast('在线拉描述失败: ' + err.message, 'error');
  } finally {
    translateDetail.value.fetchBusy = false;
  }
}

async function runApiTranslation() {
  translateDetail.value.apiBusy = true;
  translateDetail.value.apiError = '';
  translateDetail.value.apiRaw = '';
  try {
    const res = await fetch('http://127.0.0.1:8000/api/translate_character', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag: translateDetail.value.tag }),
    });
    const data = await res.json();
    if (!data.ok) {
      translateDetail.value.apiError = data.error || data.msg || 'API 调用失败';
      // 把原始内容灌进手动模式的粘贴框，方便用户人工修复后重新解析
      const raw = data.raw || '';
      if (raw) {
        translateDetail.value.apiRaw = raw;
        translateDetail.value.pasteText = raw;
        translateDetail.value.mode = 'manual';
        translateDetail.value.parseError = '';
        showToast('API 解析失败，已切换到手动模式，请修复 JSON 后点「解析填表」', 'error');
      } else {
        showToast(translateDetail.value.apiError, 'error');
      }
      return;
    }
    const entry = data.entry || {};
    translateDetail.value.form = {
      has_chinese: !!entry.has_chinese,
      chinese_name: entry.chinese_name || '',
      source_hint: entry.source_hint || '',
      translated_description_zh: entry.translated_description_zh || '',
    };
    showToast('API 翻译完成，请校对后保存', 'success');
  } catch (err) {
    translateDetail.value.apiError = '网络/请求异常: ' + err.message;
    showToast(translateDetail.value.apiError, 'error');
  } finally {
    translateDetail.value.apiBusy = false;
  }
}

async function copyManualPrompt() {
  try {
    await navigator.clipboard.writeText(translateDetail.value.manualPrompt || '');
    showToast('Prompt 已复制到剪贴板', 'success');
  } catch (err) {
    showToast('复制失败: ' + err.message, 'error');
  }
}

function parsePastedJson() {
  const raw = (translateDetail.value.pasteText || '').trim();
  translateDetail.value.parseError = '';
  if (!raw) {
    translateDetail.value.parseError = '请先粘贴大模型返回的 JSON';
    return;
  }
  let text = raw;
  // 兼容大模型偶尔输出的 ```json ``` 包裹
  if (text.startsWith('```json')) text = text.slice(7);
  if (text.startsWith('```')) text = text.slice(3);
  if (text.endsWith('```')) text = text.slice(0, -3);
  text = text.trim();
  try {
    const obj = JSON.parse(text);
    translateDetail.value.form = {
      has_chinese: !!obj.has_chinese,
      chinese_name: String(obj.chinese_name || ''),
      source_hint: String(obj.source_hint || '').toLowerCase(),
      translated_description_zh: String(obj.translated_description_zh || ''),
    };
    showToast('已解析填表', 'success');
  } catch (err) {
    translateDetail.value.parseError = 'JSON 解析失败: ' + err.message + '，可手动改下方字段';
  }
}

async function saveTranslation() {
  const form = translateDetail.value.form;
  if (form.has_chinese && !form.chinese_name.trim()) {
    showToast('请填写中文名，或取消勾选「有中文名」', 'error');
    return;
  }
  translateDetail.value.saving = true;
  try {
    const res = await fetch('http://127.0.0.1:8000/api/save_character_translation', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tag: translateDetail.value.tag,
        has_chinese: form.has_chinese,
        chinese_name: form.chinese_name.trim(),
        source_hint: form.source_hint.trim().toLowerCase(),
        translated_description_zh: form.translated_description_zh.trim(),
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast('保存失败: ' + data.msg, 'error');
      return;
    }
    showToast('已保存', 'success');
    // 从主弹窗列表里移除该 tag
    const tag = translateDetail.value.tag;
    translationModal.value.list = translationModal.value.list.filter(it => it.tag !== tag);
    closeTranslateDetail();
  } catch (err) {
    showToast('保存失败: ' + err.message, 'error');
  } finally {
    translateDetail.value.saving = false;
  }
}

async function importTranslationDict() {
  translationModal.value.importing = true;
  try {
    const res = await fetch('http://127.0.0.1:8000/api/import_character_chinese_search', {
      method: 'POST',
    });
    const data = await res.json();
    if (!data.ok) {
      showToast('导入失败: ' + data.msg, 'error');
      return;
    }
    showToast(`已导入 ${data.imported} 条到画廊`, 'success');
    closeTranslationModal();
    await loadGallery(gallery.value.selectedDate);
  } catch (err) {
    showToast('导入失败: ' + err.message, 'error');
  } finally {
    translationModal.value.importing = false;
  }
}

// ---------------- 画师收藏：从画廊 chip 的 ★ 加入分组 ----------------
const favoriteDialog = ref({
  open: false,
  artist: '',
  loading: false,
  saving: false,
  groups: {},              // {name: [artist,...]}，从后端拉到的全集
  selectedGroups: [],
  newGroupName: '',
});

async function openFavoriteDialog(artist) {
  favoriteDialog.value.open = true;
  favoriteDialog.value.artist = artist;
  favoriteDialog.value.loading = true;
  favoriteDialog.value.newGroupName = '';
  try {
    const res = await fetch('http://127.0.0.1:8000/api/artist_favorites');
    const data = await res.json();
    favoriteDialog.value.groups = data.groups || {};
    // 默认勾上该 artist 已经在的分组（方便看到当前归属、也支持取消勾选移除）
    favoriteDialog.value.selectedGroups = Object.entries(favoriteDialog.value.groups)
      .filter(([, arts]) => arts.includes(artist))
      .map(([name]) => name);
  } catch (err) {
    showToast('加载分组失败: ' + err.message, 'error');
  } finally {
    favoriteDialog.value.loading = false;
  }
}

function closeFavoriteDialog() {
  favoriteDialog.value.open = false;
}

function toggleFavGroup(name) {
  const idx = favoriteDialog.value.selectedGroups.indexOf(name);
  if (idx >= 0) favoriteDialog.value.selectedGroups.splice(idx, 1);
  else favoriteDialog.value.selectedGroups.push(name);
}

function createFavGroupInline() {
  const name = favoriteDialog.value.newGroupName.trim();
  if (!name) { showToast('分组名不能为空', 'error'); return; }
  if (favoriteDialog.value.groups[name]) { showToast('分组已存在', 'error'); return; }
  favoriteDialog.value.groups = { ...favoriteDialog.value.groups, [name]: [] };
  favoriteDialog.value.selectedGroups.push(name);
  favoriteDialog.value.newGroupName = '';
}

async function saveFavoriteDialog() {
  if (!favoriteDialog.value.selectedGroups.length) {
    showToast('请至少勾选一个分组', 'error');
    return;
  }
  favoriteDialog.value.saving = true;
  const artist = favoriteDialog.value.artist;
  const target = new Set(favoriteDialog.value.selectedGroups);
  // 同步：勾选的加入 / 未勾选的从该 artist 移除（在已知 groups 范围内）
  const next = {};
  for (const [g, arr] of Object.entries(favoriteDialog.value.groups)) {
    const has = arr.includes(artist);
    if (target.has(g) && !has) next[g] = [...arr, artist];
    else if (!target.has(g) && has) next[g] = arr.filter(a => a !== artist);
    else next[g] = arr;
  }
  try {
    const res = await fetch('http://127.0.0.1:8000/api/artist_favorites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groups: next }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast('保存失败: ' + (data.msg || ''), 'error');
      return;
    }
    showToast(`已收藏「${artist}」`, 'success');
    favSnapshot.value.artists = data.groups || next;
    closeFavoriteDialog();
  } catch (err) {
    showToast('保存失败: ' + err.message, 'error');
  } finally {
    favoriteDialog.value.saving = false;
  }
}

const favGroupList = computed(() => {
  return Object.entries(favoriteDialog.value.groups)
    .map(([name, arts]) => ({ name, count: arts.length }))
    .sort((a, b) => a.name.localeCompare(b.name));
});

// ---------------- 已收藏画师/角色/图片：用于卡片异色高亮 ----------------
// favorites 全局快照，挂载时拉一次；保存收藏后再同步刷新一次
const favSnapshot = ref({
  artists: {},       // {group: [artist,...]}
  characters: {},    // {group: [character_token,...]}
  imageKeys: [],     // ["date/filename", ...]
});

const favoritedArtistSet = computed(() => {
  const s = new Set();
  for (const arts of Object.values(favSnapshot.value.artists || {})) {
    for (const a of arts) s.add(a);
  }
  return s;
});

const favoritedCharacterSet = computed(() => {
  const s = new Set();
  for (const chars of Object.values(favSnapshot.value.characters || {})) {
    for (const c of chars) s.add(c);
  }
  return s;
});

const favoritedImageKeySet = computed(() => new Set(favSnapshot.value.imageKeys || []));

function imageFavKey(item) {
  return `${gallery.value.selectedDate || ''}/${item?.filename || ''}`;
}

function isImageFavorited(item) {
  return favoritedImageKeySet.value.has(imageFavKey(item));
}

function isCardFavorited(item) {
  if (isImageFavorited(item)) return true;
  const arts = item.artistTokens || [];
  for (const a of arts) {
    if (a && a !== '未知' && favoritedArtistSet.value.has(a)) return true;
  }
  const chars = item.characterTokens || [];
  for (const c of chars) {
    if (c && favoritedCharacterSet.value.has(c)) return true;
  }
  return false;
}

async function loadFavSnapshot() {
  try {
    const [aRes, cRes, iRes] = await Promise.all([
      fetch('http://127.0.0.1:8000/api/artist_favorites').then(r => r.json()),
      fetch('http://127.0.0.1:8000/api/character_favorites').then(r => r.json()),
      fetch('http://127.0.0.1:8000/api/image_favorites').then(r => r.json()),
    ]);
    favSnapshot.value.artists = aRes.groups || {};
    favSnapshot.value.characters = cRes.groups || {};
    favSnapshot.value.imageKeys = iRes.keys || [];
  } catch (_) { /* 静默失败，卡片只是不高亮而已 */ }
}

async function toggleImageFavorite(item) {
  if (!item?.filename || !gallery.value.selectedDate) {
    showToast('缺少日期或文件名，无法收藏', 'error');
    return;
  }
  const payload = {
    date: gallery.value.selectedDate,
    filename: item.filename,
    artist: item.artist || '',
    characters: Array.isArray(item.characters) ? item.characters : [],
    score: item.score || 0,
    fav_count: item.favCount || 0,
    local_path: item.localPath || '',
    post_url: item.postUrl || '',
    web_url: item.web_url || item.webUrl || '',
  };
  try {
    const res = await fetch('http://127.0.0.1:8000/api/image_favorites/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item: payload }),
    });
    const data = await res.json();
    if (!data.ok) { showToast(data.msg || '收藏失败', 'error'); return; }
    const key = data.key;
    const set = new Set(favSnapshot.value.imageKeys);
    if (data.favorited) set.add(key); else set.delete(key);
    favSnapshot.value.imageKeys = Array.from(set);
    showToast(data.favorited ? '已加入图片收藏' : '已取消图片收藏', data.favorited ? 'success' : 'info');
  } catch (err) {
    showToast('收藏失败: ' + err.message, 'error');
  }
}

// ---------------- 角色收藏：从画廊 chip 的 ★ 加入分组 ----------------
// token 形如 "初音未来 [vocaloid]"，按 [source_hint] 提取出处用作默认分组名
function extractSourceHint(token) {
  if (!token) return '';
  const m = String(token).match(/\[([^\[\]]+)\]/);
  return m ? m[1].trim() : '';
}

const charFavoriteDialog = ref({
  open: false,
  character: '',     // 完整 token，例如 "初音未来 [vocaloid]"
  sourceHint: '',    // 解析出的 source_hint
  loading: false,
  saving: false,
  groups: {},        // {group: [character_token,...]}
  selectedGroups: [],
  newGroupName: '',
});

async function openCharacterFavoriteDialog(token) {
  charFavoriteDialog.value.open = true;
  charFavoriteDialog.value.character = token;
  charFavoriteDialog.value.sourceHint = extractSourceHint(token);
  charFavoriteDialog.value.loading = true;
  charFavoriteDialog.value.newGroupName = '';
  try {
    const res = await fetch('http://127.0.0.1:8000/api/character_favorites');
    const data = await res.json();
    charFavoriteDialog.value.groups = data.groups || {};
    // 已在的分组默认勾上
    charFavoriteDialog.value.selectedGroups = Object.entries(charFavoriteDialog.value.groups)
      .filter(([, arr]) => arr.includes(token))
      .map(([name]) => name);
    // 「按 source_hint 合并」：如果还没勾任何分组、且字典里已有同名 source_hint 分组，预勾上；
    // 否则把 source_hint 填到「新建分组」框，方便一键创建
    const hint = charFavoriteDialog.value.sourceHint;
    if (hint) {
      if (charFavoriteDialog.value.groups[hint]) {
        if (!charFavoriteDialog.value.selectedGroups.includes(hint)) {
          charFavoriteDialog.value.selectedGroups.push(hint);
        }
      } else {
        charFavoriteDialog.value.newGroupName = hint;
      }
    }
  } catch (err) {
    showToast('加载角色收藏分组失败: ' + err.message, 'error');
  } finally {
    charFavoriteDialog.value.loading = false;
  }
}

function closeCharacterFavoriteDialog() {
  charFavoriteDialog.value.open = false;
}

function toggleCharFavGroup(name) {
  const idx = charFavoriteDialog.value.selectedGroups.indexOf(name);
  if (idx >= 0) charFavoriteDialog.value.selectedGroups.splice(idx, 1);
  else charFavoriteDialog.value.selectedGroups.push(name);
}

function createCharFavGroupInline() {
  const name = charFavoriteDialog.value.newGroupName.trim();
  if (!name) { showToast('分组名不能为空', 'error'); return; }
  if (charFavoriteDialog.value.groups[name]) { showToast('分组已存在', 'error'); return; }
  charFavoriteDialog.value.groups = { ...charFavoriteDialog.value.groups, [name]: [] };
  charFavoriteDialog.value.selectedGroups.push(name);
  charFavoriteDialog.value.newGroupName = '';
}

async function saveCharacterFavoriteDialog() {
  if (!charFavoriteDialog.value.selectedGroups.length) {
    showToast('请至少勾选一个分组', 'error');
    return;
  }
  charFavoriteDialog.value.saving = true;
  const token = charFavoriteDialog.value.character;
  const target = new Set(charFavoriteDialog.value.selectedGroups);
  const next = {};
  for (const [g, arr] of Object.entries(charFavoriteDialog.value.groups)) {
    const has = arr.includes(token);
    if (target.has(g) && !has) next[g] = [...arr, token];
    else if (!target.has(g) && has) next[g] = arr.filter(a => a !== token);
    else next[g] = arr;
  }
  try {
    const res = await fetch('http://127.0.0.1:8000/api/character_favorites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groups: next }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast('保存失败: ' + (data.msg || ''), 'error');
      return;
    }
    showToast(`已收藏「${token}」`, 'success');
    favSnapshot.value.characters = data.groups || next;
    closeCharacterFavoriteDialog();
  } catch (err) {
    showToast('保存失败: ' + err.message, 'error');
  } finally {
    charFavoriteDialog.value.saving = false;
  }
}

const charFavGroupList = computed(() => {
  return Object.entries(charFavoriteDialog.value.groups)
    .map(([name, arr]) => ({ name, count: arr.length }))
    .sort((a, b) => a.name.localeCompare(b.name));
});

function editItem(item) {
  emit('edit-image', item);
}

function applySearch(keyword) {
  gallery.value.search = keyword || '';
}

const translationFileInput = ref(null);

function importTranslationFile() {
  translationFileInput.value?.click();
}

function onTranslationFileSelected(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      const data = JSON.parse(e.target.result);
      const res = await fetch('http://127.0.0.1:8000/api/import_translation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ translations: data })
      });
      const json = await res.json();
      if (json.ok) {
        showToast("翻译包导入成功，重新加载中...", "success");
        await loadGallery(gallery.value.selectedDate);
      } else {
        showToast("翻译包导入失败: " + json.msg, "error");
      }
    } catch (err) {
      showToast("解析JSON或请求失败: " + err.message, "error");
      console.error("Import error:", err);
    } finally {
      event.target.value = '';
    }
  };
  reader.readAsText(file);
}

async function openViewer(item) {
  const items = filteredLocalImages.value;
  const index = items.findIndex(candidate => (candidate.localPath || candidate.filename) === (item.localPath || item.filename));
  viewer.value.open = true;
  viewer.value.index = Math.max(0, index);
  viewer.value.zoom = 1;
  viewer.value.imageUrl = '';

  refreshSinglePost(item);

  const ext = (item.filename || '').split('.').pop().toLowerCase();
  // 视频走 FastAPI 静态服务，自带 byte-range 支持，避开 local:// 协议的媒体限制
  if (VIDEO_EXTS.includes(ext) && gallery.value.selectedDate && item.filename) {
    viewer.value.imageUrl = `http://127.0.0.1:8000/images/${gallery.value.selectedDate}/${encodeURIComponent(item.filename)}`;
    return;
  }

  if (item?.localPath) {
    if (ext === 'zip') {
      const gifPath = item.localPath.replace(/\.zip$/i, '.gif');
      if (await window.desktopAPI.file.exists(gifPath)) {
        viewer.value.imageUrl = await window.desktopAPI.file.toLocalUrl(gifPath);
        return;
      }
    }
    viewer.value.imageUrl = await window.desktopAPI.file.toLocalUrl(item.localPath);
    return;
  }

  const webUrl = item?.web_url || item?.webUrl;
  if (webUrl) {
    viewer.value.imageUrl = `http://127.0.0.1:8000${webUrl}`;
    return;
  }
}

async function syncViewerImage() {
  viewer.value.zoom = 1;
  viewer.value.imageUrl = '';
  if (!viewerItem.value) return;

  const ext = (viewerItem.value.filename || '').split('.').pop().toLowerCase();
  if (VIDEO_EXTS.includes(ext) && gallery.value.selectedDate && viewerItem.value.filename) {
    viewer.value.imageUrl = `http://127.0.0.1:8000/images/${gallery.value.selectedDate}/${encodeURIComponent(viewerItem.value.filename)}`;
    return;
  }

  if (viewerItem.value.localPath) {
    if (ext === 'zip') {
      const gifPath = viewerItem.value.localPath.replace(/\.zip$/i, '.gif');
      if (await window.desktopAPI.file.exists(gifPath)) {
        viewer.value.imageUrl = await window.desktopAPI.file.toLocalUrl(gifPath);
        return;
      }
    }
    viewer.value.imageUrl = await window.desktopAPI.file.toLocalUrl(viewerItem.value.localPath);
    return;
  }

  const webUrl = viewerItem.value.web_url || viewerItem.value.webUrl;
  if (webUrl) {
    viewer.value.imageUrl = `http://127.0.0.1:8000${webUrl}`;
    return;
  }
}

function closeViewer() {
  viewer.value.open = false;
  viewer.value.imageUrl = '';
  viewer.value.zoom = 1;
}

function getLogType(line) {
  if (!line) return 'log-info';

  // 「成功 X / 跳过 Y / 失败 Z」这种页结概要：按真实失败数判定，
  // Z=0 时不应该挂红 ×，否则永远是错误样式
  const summary = line.match(/成功\s+(\d+)\s*\/\s*跳过\s+(\d+)\s*\/\s*失败\s+(\d+)/);
  if (summary) {
    const failed = parseInt(summary[3], 10);
    return failed > 0 ? 'log-error' : 'log-success';
  }

  const text = line.toLowerCase();
  if (text.includes('失败') || text.includes('错误') || text.includes('error') || text.includes('异常')) return 'log-error';
  if (text.includes('成功') || text.includes('完成') || text.includes('finish')) return 'log-success';
  if (text.includes('跳过') || text.includes('skip')) return 'log-warn';
  return 'log-info';
}

function getLogIcon(line) {
  const type = getLogType(line);
  if (type === 'log-error') return '×';
  if (type === 'log-success') return '✓';
  if (type === 'log-warn') return '！';
  return '›';
}

async function stepViewer(offset) {
  if (!viewerItems.value.length) return;
  const next = Math.min(Math.max(0, viewer.value.index + offset), viewerItems.value.length - 1);
  if (next === viewer.value.index) return;
  viewer.value.index = next;
  await syncViewerImage();
  refreshSinglePost(viewerItem.value);
}

function onViewerWheel(event) {
  if (!event.ctrlKey) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.1 : 0.9;
  viewer.value.zoom = Math.min(8, Math.max(0.2, viewer.value.zoom * factor));
}

async function onKeyDown(event) {
  if (viewer.value.open && event.key === 'Escape') {
    closeViewer();
    return;
  }

  const tag = event.target?.tagName?.toLowerCase();
  if (['input', 'textarea', 'select', 'video'].includes(tag)) return;

  if (viewer.value.open) {
    if (event.key === 'ArrowLeft') {
      await stepViewer(-1);
    } else if (event.key === 'ArrowRight') {
      await stepViewer(1);
    }
    return;
  }

  if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
    if (activePage.value > 1) activePage.value -= 1;
  } else if (event.key === 'ArrowRight' || event.key === 'PageDown') {
    if (activePage.value < activeTotalPages.value) activePage.value += 1;
  } else if (event.key === 'Home') {
    activePage.value = 1;
  } else if (event.key === 'End') {
    activePage.value = activeTotalPages.value;
  }
}

async function onViewerJump(event) {
  const n = parseInt(event.target.value, 10);
  if (Number.isNaN(n)) return;
  const idx = Math.max(0, Math.min(viewerItems.value.length - 1, n - 1));
  if (idx === viewer.value.index) return;
  viewer.value.index = idx;
  await syncViewerImage();
}

watch(() => gallery.value.search, () => { gallery.value.page = 1; });
watch(() => gallery.value.filterFormat, () => { gallery.value.page = 1; });
watch(() => gallery.value.sortBy, (newSort) => {
  gallery.value.page = 1;
  if (newSort === 'score' || newSort === 'fav') {
    rebuildSortSnapshot();
  }
});
watch(() => gallery.value.hotOnly, () => { gallery.value.page = 1; });

watch(activePage, n => { jumpInput.value = n; });

watch(localTotalPages, total => {
  if (gallery.value.page > total) gallery.value.page = total;
});

watch(pagedLocalImages, async items => {
  await hydrateThumbs(items);
});

onMounted(async () => {
  await loadGallery();
  await ensureService();
  // Python 后端就绪后立刻把本地保存的 SFW 偏好推过去，确保即使是当前会话第一次请求也用对 host
  await syncSafeModeToBackend();
  // 静默加载：Python 后端就绪后在后台刷新翻译，不再显示”正在读取”遮罩，消除闪烁
  await loadGallery(gallery.value.selectedDate, true);
  await syncStatus();
  loadFavSnapshot();
  pollTimer = window.setInterval(syncStatus, 1200);
  window.addEventListener('keydown', onKeyDown);
  document.addEventListener('click', onDocClickForRefreshMenu);
  document.addEventListener('click', onDocClickForTranslateMenu);
  document.addEventListener('click', onDocClickForPagePicker);
});

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer);
  window.removeEventListener('keydown', onKeyDown);
  document.removeEventListener('click', onDocClickForRefreshMenu);
  document.removeEventListener('click', onDocClickForTranslateMenu);
  document.removeEventListener('click', onDocClickForPagePicker);
});

const modeDescription = computed(() => {
  switch (form.value.mode) {
    case 'rank': return '获取当日 Danbooru 排行榜最受欢迎的图片并自动下载。';
    case 'popular': return '根据指定日期，获取 Explore 页面当天的热门图片。';
    case 'popular_range': return '设定起始与结束日期，批量抓取这段时间内所有的热门图片。';
    case 'collect_ids': return '网络状况不佳时的极速模式：仅拉取列表和元数据，不下载图片本体。';
    case 'download_ids': return '从已收集的 ID 列表中进行批量下载，常用于断点续传或集中补全。';
    default: return '选择模式后配置参数，点击启动开始执行。';
  }
});
</script>

<template>
  <div class="crawler-layout">
    <section class="panel card control-panel">
      <div class="panel-head compact-head">
        <div>
          <h2>抓图任务</h2>
          <p class="inline-note">{{ modeDescription }}</p>
        </div>
        <button class="ghost" @click="openHostsHint" style="margin-left: auto; color: #ff9800; font-size: 12px; padding: 4px 10px; white-space: nowrap;" title="无法连接 Danbooru / Safebooru 时，按此教程改 hosts">🛠 Hosts</button>
        <button
          class="ghost safe-mode-btn"
          :class="{ 'is-safe': safeMode, 'is-unsafe': !safeMode }"
          @click="toggleSafeMode"
          :title="safeMode ? '当前走 safebooru.donmai.us（无 R-18）。点击切换为完整 danbooru' : '当前走 danbooru.donmai.us（含 NSFW）。点击切回 SFW'"
        >{{ safeMode ? '🛡 SFW' : '🌶 全部内容' }}</button>
      </div>

      <div class="mode-selector">
        <button class="mode-chip" :class="{ active: form.mode === 'rank' }" @click="form.mode = 'rank'"
          title="按 Danbooru 排行榜抓取并下载图片">排行榜</button>
        <button class="mode-chip" :class="{ active: form.mode === 'popular' }" @click="form.mode = 'popular'"
          title="按指定日期获取热门帖子并下载">日期热门</button>
        <button class="mode-chip" :class="{ active: form.mode === 'popular_range' }" @click="form.mode = 'popular_range'"
          title="按指定日期范围获取热门帖子并下载">日期范围热门</button>
        <button class="mode-chip" :class="{ active: form.mode === 'collect_ids' }" @click="form.mode = 'collect_ids'"
          title="网不好时只收集 ID，不下载图片">仅收集ID</button>
        <button class="mode-chip" :class="{ active: form.mode === 'download_ids' }" @click="form.mode = 'download_ids'"
          title="从已收集的 ID 列表批量下载">按ID下载</button>
      </div>

      <div class="field-grid" v-if="form.mode !== 'download_ids'">
        <label>
          <span>起始页</span>
          <input v-model.number="form.startPage" type="number" min="1" />
        </label>
        <label>
          <span>结束页</span>
          <input v-model.number="form.endPage" type="number" min="1" />
        </label>
      </div>
      <label class="field-full" v-if="['popular', 'download_ids'].includes(form.mode)">
        <span>目标日期 <span class="muted compact-text">(留空则用今天)</span></span>
        <input v-model="form.targetDate" type="date" />
      </label>
      <label class="field-full" v-if="form.mode === 'download_ids'">
        <span>
          粘贴 ID 列表
          <span class="muted compact-text">
            (支持压缩格式 dbids:… / 逗号 / 空格 / 换行 / URL 混合粘贴；留空则使用已收集的 ids_data.json)
          </span>
        </span>
        <textarea
          v-model="form.idsText"
          rows="4"
          placeholder="支持两种格式：&#10;1) 压缩：dbids:6tewdt.dw （别人复制按钮生成的）&#10;2) 明文：8123456,8456789,8987654 或一行一个 或直接粘贴 URL"
          style="font-family: Consolas, monospace; font-size: 12px; resize: vertical; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line); background: rgba(255,255,255,0.6);"
        />
        <div class="muted compact-text" style="margin-top: 4px;">
          已解析到 <strong>{{ parsedPastedIds.length }}</strong> 个 ID
          <span v-if="isPastedCompressed"> · 🗜 识别为压缩格式</span>
          <span v-if="parsedPastedIds.length"> · 将下载到{{ form.targetDate || '今天' }}的图库</span>
        </div>
      </label>
      <div class="field-grid" v-if="form.mode === 'popular_range'">
        <label>
          <span>起始日期</span>
          <input v-model="form.startDate" type="date" />
        </label>
        <label>
          <span>结束日期</span>
          <input v-model="form.endDate" type="date" />
        </label>
      </div>
      <label class="field-full">
        <span>过滤标签</span>
        <input v-model="form.tags" type="text" />
      </label>

      <div class="button-row">
        <button @click="startTask" :disabled="task.isRunning">启动</button>
        <button class="secondary" @click="pauseTask" :disabled="!task.isRunning || task.isPaused">暂停</button>
        <button class="secondary" @click="resumeTask" :disabled="!task.isRunning || !task.isPaused">继续</button>
        <button class="ghost" @click="stopTask" :disabled="!task.isRunning">停止</button>
      </div>

      <div class="status-pills">
        <span class="pill" :class="{ active: task.isRunning }">运行中: {{ task.isRunning ? '是' : '否' }}</span>
        <span class="pill" :class="{ warning: task.isPaused }">已暂停: {{ task.isPaused ? '是' : '否' }}</span>
      </div>

      <div v-if="task.backendError" class="error-banner" :class="{ 'is-expanded': task.backendErrorExpanded }">
        <div class="error-banner-head">
          <span class="error-banner-icon">×</span>
          <span class="error-banner-text">{{ task.backendError.split('\n')[0] }}</span>
          <div class="error-banner-actions">
            <button class="error-banner-btn" @click="task.backendErrorExpanded = !task.backendErrorExpanded">
              {{ task.backendErrorExpanded ? '收起' : '查看详情' }}
            </button>
            <button class="error-banner-btn" @click="dismissBackendError">关闭</button>
          </div>
        </div>
        <pre v-if="task.backendErrorExpanded" class="error-banner-body">{{ task.backendError }}</pre>
      </div>

      <div class="modern-log-wrapper" :class="{ 'is-expanded': task.showLogs, 'is-maximized': task.maximized }">
        <div class="modern-log-header" @click="toggleLogHeader">
          <div class="log-header-left">
            <span class="status-dot" :class="{ 'is-active': task.isRunning }"></span>
            <span class="log-title">运行动态</span>
          </div>
          <div class="log-header-right" @click.stop>
            <button
              class="log-toolbar-btn"
              :class="{ active: task.hideSuccess }"
              @click="task.hideSuccess = !task.hideSuccess"
              :title="task.hideSuccess ? '当前隐藏单张下载/已存在日志，点击切换为显示全部' : '当前显示全部日志，点击只看异常与概要'"
            >{{ task.hideSuccess ? '只看异常+概要' : '显示全部' }}</button>
            <button class="log-toolbar-btn" @click="clearLogs" title="清空当前日志">清空</button>
            <button
              class="log-icon-btn"
              @click="toggleLogMaximize"
              :title="task.maximized ? '缩小日志面板' : '放大日志面板（覆盖抓图任务卡片）'"
            >
              <svg v-if="!task.maximized" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-label="放大">
                <polyline points="15 3 21 3 21 9"></polyline>
                <polyline points="9 21 3 21 3 15"></polyline>
                <line x1="21" y1="3" x2="14" y2="10"></line>
                <line x1="3" y1="21" x2="10" y2="14"></line>
              </svg>
              <svg v-else viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-label="缩小">
                <polyline points="4 14 10 14 10 20"></polyline>
                <polyline points="20 10 14 10 14 4"></polyline>
                <line x1="14" y1="10" x2="21" y2="3"></line>
                <line x1="3" y1="21" x2="10" y2="14"></line>
              </svg>
            </button>
          </div>
        </div>
        <transition name="log-expand">
          <div class="modern-log-body" v-show="task.showLogs" ref="logBodyRef">
            <div v-if="task.hideSuccess && hiddenSuccessCount" class="modern-log-hint">
              已折叠 {{ hiddenSuccessCount }} 条单张下载/已存在日志（点击右上「显示全部」可查看）
            </div>
            <div
              class="modern-log-line"
              v-for="entry in visibleLogs"
              :key="entry.idx"
              :class="[getLogType(entry.line), { 'is-expanded': task.expandedLogIdx === entry.idx }]"
              @click="toggleLogExpand(entry.idx)"
              :title="task.expandedLogIdx === entry.idx ? '点击收起' : '点击展开完整内容'"
            >
              <span class="log-icon">{{ getLogIcon(entry.line) }}</span>
              <span class="log-text">{{ entry.line }}</span>
            </div>
          </div>
        </transition>
      </div>
    </section>

    <section class="panel card gallery-panel">
      <div class="gallery-head">
        <div class="gallery-title-row">
          <h2>{{ gallery.selectedDate || '本地图库' }}</h2>
          <span class="gallery-stats-inline">
            共 {{ galleryStats.total }} 张<span v-if="galleryStats.filtered !== galleryStats.total"> · 已筛选 {{ galleryStats.filtered }} 张</span><span v-if="galleryStats.avg > 0"> · 平均 ★ {{ galleryStats.avg }} · 中位 ★ {{ galleryStats.median }}</span>
          </span>
        </div>
        <div class="gallery-tools">
          <button
            class="secondary tool-btn select-mode-btn"
            :class="{ active: selection.enabled }"
            @click="setSelectionEnabled(!selection.enabled)"
            :title="selection.enabled ? '退出选择模式（已选记录会保留）' : '进入选择模式：可勾选多张图片分享；也可按 Ctrl+点击图片快速选择'"
          >{{ selection.enabled ? `✓ 选择中 (${selection.ids.size})` : (selection.ids.size ? `☐ 选择模式 (${selection.ids.size})` : '☐ 选择模式') }}</button>
          <select v-model="gallery.sortBy" class="search-input gallery-sort-select" style="width: auto;" title="排序方式">
            <option value="default">默认抓取顺序</option>
            <option value="score">按 score 排序</option>
            <option value="fav">按收藏数排序</option>
          </select>
          <button
            v-if="gallery.sortBy === 'score' || gallery.sortBy === 'fav'"
            class="secondary tool-btn"
            @click="rebuildSortSnapshot"
            title="按当前最新 score / 收藏数 重新排序（默认锁定排序，避免点开卡片刷新热度时位置乱跳）"
          >🔃 重新排序</button>
          <select v-model="gallery.filterFormat" class="search-input" style="width: auto;">
            <option value="all">全部格式</option>
            <option value="image">图片</option>
            <option value="video">视频</option>
            <option value="zip">动图ZIP</option>
          </select>
          <select v-model.number="gallery.cardSize" class="search-input" style="width: auto;" title="卡片大小">
            <option :value="120">紧凑</option>
            <option :value="150">小</option>
            <option :value="180">默认</option>
            <option :value="220">大</option>
          </select>
          <button
            :class="['hot-toggle', { active: gallery.hotOnly }]"
            @click="gallery.hotOnly = !gallery.hotOnly"
            :title="`只看 score ≥ ${gallery.hotThreshold}`"
          >🔥 只看高分</button>
          <div class="refresh-dropdown">
            <button
              :class="['refresh-btn', { active: refresh.isRunning, 'menu-open': refreshMenu.open }]"
              @click.stop="toggleRefreshMenu"
              :disabled="!gallery.selectedDate && !refresh.isRunning"
              :title="refresh.isRunning ? '点击停止刷新' : '选择刷新范围（本页 / 指定范围 / 全部）'"
            >
              <span v-if="!refresh.isRunning">🔄 刷新热度 ▾</span>
              <span v-else>⏸ {{ refresh.done }}/{{ refresh.total }}</span>
            </button>
            <div v-if="refreshMenu.open && !refresh.isRunning" class="refresh-menu" @click.stop>
              <button class="refresh-menu-item" @click="onRefreshChoice('page')">
                <span class="refresh-menu-label">本页</span>
                <span class="refresh-menu-meta">{{ pagedLocalImages.length }} 张</span>
              </button>
              <button class="refresh-menu-item" @click="onRefreshChoice('range')">
                <span class="refresh-menu-label">指定页码范围…</span>
                <span class="refresh-menu-meta">自定义</span>
              </button>
              <button class="refresh-menu-item" @click="onRefreshChoice('all')">
                <span class="refresh-menu-label">全部</span>
                <span class="refresh-menu-meta">{{ activeTotalPages }} 页</span>
              </button>
            </div>
          </div>
          <span class="search-input-wrap">
            <input v-model="gallery.search" class="search-input search-input-with-clear" type="text" placeholder="搜索作者 / 角色" />
            <button
              v-if="gallery.search"
              class="search-clear-btn"
              @click="gallery.search = ''"
              title="清空搜索"
              type="button"
            >×</button>
          </span>
          <div class="translate-dropdown">
            <button
              class="secondary translate-trigger tool-btn"
              :class="{ 'menu-open': translateMenu.open }"
              @click.stop="toggleTranslateMenu"
              title="翻译角色 / 导入翻译字典"
            >🌐 翻译 ▾</button>
            <div v-if="translateMenu.open" class="translate-menu" @click.stop>
              <button
                class="translate-menu-item"
                :disabled="!gallery.selectedDate"
                @click="onTranslateChoice('character')"
              >
                <span class="translate-menu-label">翻译角色</span>
                <span class="translate-menu-meta">{{ gallery.selectedDate ? '调用 LLM 或手动粘贴' : '先选日期' }}</span>
              </button>
              <button
                class="translate-menu-item"
                @click="onTranslateChoice('import')"
              >
                <span class="translate-menu-label">导入翻译字典</span>
                <span class="translate-menu-meta">从 JSON 文件合并</span>
              </button>
            </div>
          </div>
          <input type="file" ref="translationFileInput" style="display: none" accept=".json" @change="onTranslationFileSelected" />
        </div>
      </div>

      <GalleryCalendar
        :available-dates="gallery.availableDates"
        :selected-date="gallery.selectedDate"
        :today="gallery.today"
        @select="loadGallery"
      />

      <div v-if="selection.enabled" class="selection-bar inline-bar">
        <span class="selection-count">已选 <strong>{{ selection.ids.size }}</strong> 张</span>
        <button
          class="secondary"
          :class="{ active: showOnlySelected }"
          @click="showOnlySelected = !showOnlySelected"
          :disabled="!showOnlySelected && !selection.ids.size"
          title="切换：只显示当前日期里已选的图片"
        >{{ showOnlySelected ? '✓ 只看已选' : '👁 只看已选' }}</button>
        <button
          class="secondary"
          @click="selectionListOpen = true"
          :disabled="!selection.ids.size"
          title="查看所有已选 ID（可逐个跳转/移除）"
        >📋 已选清单</button>
        <button class="secondary" @click="copySelectedIds" :disabled="!selection.ids.size" title="复制选中图片的 IDs 到剪贴板（明文逗号分隔）">📤 复制 IDs</button>
        <button class="secondary" @click="openCryptoTool" title="打开加密工具：把任意 IDs 文本压缩成短字符串方便分享">🗜 加密工具</button>
        <button class="ghost" @click="clearSelection" :disabled="!selection.ids.size">清空</button>
        <button class="ghost" @click="setSelectionEnabled(false)" title="退出选择模式（已选记录会保留）">退出</button>
      </div>

      <div v-if="loadingGallery" class="gallery-empty">正在读取图库...</div>
      <div v-else-if="!activeItems.length" class="gallery-empty">
        {{ showOnlySelected ? '当前日期没有已选图片，切换日期试试' : '当前日期没有图片' }}
      </div>
      <div v-else class="gallery-grid" :style="`--card-min-w: ${gallery.cardSize}px`">
        <article v-for="item in activeItems" :key="item.localPath || item.filename" class="image-card" :class="{ 'is-favorited': isCardFavorited(item), 'is-img-favorited': isImageFavorited(item), 'is-selected': isItemSelected(item) }">
          <div class="thumb-wrap">
            <img class="thumb clickable-thumb" :src="item.thumbUrl" :alt="item.filename" loading="lazy" decoding="async" @click="onThumbClick($event, item)" />
            <button
              v-if="selection.enabled"
              class="img-select-toggle"
              :class="{ active: isItemSelected(item) }"
              @click.stop="toggleItemSelection(item)"
              :title="isItemSelected(item) ? '取消选择' : '加入选择'"
            >{{ isItemSelected(item) ? '✓' : '' }}</button>
          </div>
          <button
            class="img-fav-toggle"
            :class="{ active: isImageFavorited(item) }"
            @click.stop="toggleImageFavorite(item)"
            :title="isImageFavorited(item) ? '取消图片收藏' : '加入图片收藏'"
          >{{ isImageFavorited(item) ? '♥' : '♡' }}</button>
          <div v-if="(item.score || 0) > 0 || (item.favCount || 0) > 0" class="score-badge">
            <span><span class="score-star">★</span> {{ item.score || 0 }}</span>
            <span><span class="score-heart">♥</span> {{ item.favCount || 0 }}</span>
          </div>
          <div class="button-row compact card-actions">
            <button class="secondary" @click="openOriginal(item)" :disabled="!item.postUrl" title="打开 Danbooru 原帖">原帖</button>
            <button class="secondary" @click="openLocal(item)" :disabled="!item.localPath" title="打开本地文件">本地</button>
            <button @click="editItem(item)" title="编辑打码">编辑</button>
            <button v-if="item.filename?.toLowerCase().endsWith('.zip')" class="secondary" @click="convertGif(item)" style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;" title="ZIP 动画转 GIF">转GIF</button>
          </div>
        </article>
      </div>

      <div class="pagination-bar" v-if="activeCount">
        <button class="ghost pg-btn" @click="gotoPage(1)" :disabled="activePage <= 1" title="首页">«</button>
        <button class="ghost pg-btn" @click="gotoPage(activePage - 1)" :disabled="activePage <= 1" title="上一页 (←)">‹</button>
        <button
          v-for="(n, i) in pageNumbers"
          :key="`pg-${i}-${n}`"
          class="pg-num"
          :class="{ active: n === activePage, ellipsis: n === '…' }"
          :disabled="n === '…'"
          @click="gotoPage(n)"
        >{{ n }}</button>
        <button class="ghost pg-btn" @click="gotoPage(activePage + 1)" :disabled="activePage >= activeTotalPages" title="下一页 (→)">›</button>
        <button class="ghost pg-btn" @click="gotoPage(activeTotalPages)" :disabled="activePage >= activeTotalPages" title="末页">»</button>
        <span class="pg-jump pg-picker-host">
          <button class="pg-jump-btn" @click="doJump" title="跳转到输入的页码">跳转</button>
          <input type="number" min="1" :max="activeTotalPages" v-model.number="jumpInput" @keyup.enter="doJump" />
          <button
            class="pg-jump-btn pg-jump-go"
            :class="{ active: pagePicker.open }"
            @click.stop="pagePicker.open = !pagePicker.open"
            :title="pagePicker.open ? '关闭页码列表' : '展开页码列表（10列/行）'"
          >👆</button>
          / {{ activeTotalPages }}
          <div v-if="pagePicker.open" class="pg-picker-panel" @click.stop>
            <div class="pg-picker-head">
              <span>共 {{ activeTotalPages }} 页 · 点击跳转</span>
              <button class="ghost" @click="pagePicker.open = false">×</button>
            </div>
            <div class="pg-picker-grid">
              <button
                v-for="n in activeTotalPages"
                :key="`picker-${n}`"
                class="pg-picker-cell"
                :class="{ active: n === activePage }"
                @click="gotoPage(n); pagePicker.open = false"
              >{{ n }}</button>
            </div>
          </div>
        </span>
      </div>
    </section>

    <div v-if="selectionListOpen" class="viewer-overlay" @click.self="selectionListOpen = false" style="z-index: 10000; display: flex; justify-content: center; align-items: center; padding: 24px;">
      <div class="selection-list-card">
        <div class="selection-list-head">
          <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">已选清单 · {{ selection.ids.size }} 个</h3>
          <button class="ghost" @click="selectionListOpen = false" style="color: var(--muted);">×</button>
        </div>
        <div v-if="!selection.ids.size" class="gallery-empty" style="min-height: 120px;">还没有选择任何图片</div>
        <div v-else class="selection-list-body">
          <div v-if="selectionInCurrentDate.length" class="selection-list-section">
            <div class="selection-list-section-title">本日期可定位 · {{ selectionInCurrentDate.length }} 个</div>
            <div class="selection-list-grid">
              <div v-for="entry in selectionInCurrentDate" :key="`cur-${entry.id}`" class="selection-list-item">
                <img class="selection-list-thumb" :src="entry.item.thumbUrl" :alt="entry.id" loading="lazy" />
                <div class="selection-list-item-info">
                  <span class="selection-list-id">#{{ entry.id }}</span>
                  <span class="muted compact-text">第 {{ entry.page }} 页</span>
                </div>
                <div class="selection-list-item-actions">
                  <button class="secondary" @click="jumpToSelected(entry.id)" title="跳转到该图所在页">跳转</button>
                  <button class="ghost" @click="removeFromSelection(entry.id)" title="从选择中移除">移除</button>
                </div>
              </div>
            </div>
          </div>
          <div v-if="selectionOtherDates.length" class="selection-list-section">
            <div class="selection-list-section-title">其他日期 / 当前过滤外 · {{ selectionOtherDates.length }} 个</div>
            <div class="selection-list-other">
              <span v-for="id in selectionOtherDates" :key="`oth-${id}`" class="selection-chip">
                #{{ id }}
                <button @click="removeFromSelection(id)" title="移除">×</button>
              </span>
            </div>
            <p class="muted compact-text" style="margin: 8px 0 0;">提示：这些 ID 在当前日期 / 过滤条件下找不到。切换日期或关掉「只看高分」「格式过滤」可能能看到。</p>
          </div>
        </div>
        <div class="selection-list-foot">
          <button class="ghost" @click="selectionListOpen = false">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="cryptoTool.open" class="viewer-overlay" @click.self="closeCryptoTool" style="z-index: 10000; display: flex; justify-content: center; align-items: center; padding: 24px;">
      <div class="crypto-tool-card">
        <div class="crypto-tool-head">
          <div>
            <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">🗜 ID 加密 / 解密工具</h3>
            <p class="muted compact-text" style="margin: 4px 0 0;">把长长的 ID 列表压成短字符串方便分享；也能反向还原。</p>
          </div>
          <button class="ghost" @click="closeCryptoTool" style="color: var(--muted);">×</button>
        </div>

        <div class="crypto-tool-row">
          <span class="crypto-tool-label">输入</span>
          <span class="muted compact-text">{{ cryptoTool.input.length }} 字符</span>
          <button class="ghost crypto-tool-mini" @click="loadSelectionToCryptoInput" :disabled="!selection.ids.size" title="把当前选择的所有 IDs 填到输入框">⬇ 载入当前选择 ({{ selection.ids.size }})</button>
          <button class="ghost crypto-tool-mini" @click="cryptoTool.input = ''" :disabled="!cryptoTool.input">清空</button>
        </div>
        <textarea
          v-model="cryptoTool.input"
          class="crypto-tool-textarea"
          rows="4"
          placeholder="粘贴你要加密的明文 IDs（逗号 / 空格 / 换行 / URL 都行），或粘贴压缩格式 dbids:... 用于解密"
        />

        <div class="crypto-tool-actions">
          <button class="secondary" @click="cryptoEncrypt" :disabled="!cryptoTool.input.trim()">🗜 加密（压缩）</button>
          <button class="secondary" @click="cryptoDecrypt" :disabled="!cryptoTool.input.trim()">🔓 解密（还原）</button>
          <button class="ghost" @click="swapCryptoIO" :disabled="!cryptoTool.output" title="把输出搬回输入，方便再次加/解密">⇅ 交换</button>
        </div>

        <div class="crypto-tool-row">
          <span class="crypto-tool-label">输出</span>
          <span class="muted compact-text">{{ cryptoTool.output.length }} 字符</span>
        </div>
        <textarea
          v-model="cryptoTool.output"
          class="crypto-tool-textarea"
          rows="4"
          readonly
          placeholder="结果会出现在这里"
        />

        <div class="crypto-tool-foot">
          <span class="muted compact-text">压缩格式 = base36 增量编码，100+ 个 ID 通常省 60%+</span>
          <button class="ghost" @click="closeCryptoTool">关闭</button>
          <button @click="copyCryptoOutput" :disabled="!cryptoTool.output">📋 复制输出</button>
        </div>
      </div>
    </div>

    <div v-if="viewer.open" class="viewer-overlay" @click.self="closeViewer" @mousemove="onViewerMouseMove" @mouseleave="viewer.toolbarHovered = false">
      <div class="viewer-toolbar" :class="{ 'is-hidden': !viewerToolbarVisible }">
        <div class="viewer-toolbar-info">
          <div class="viewer-meta-block">
            <span class="viewer-meta-label">画师</span>
            <template v-for="token in (viewerItem?.artistTokens?.length ? viewerItem.artistTokens : ['未知'])" :key="`v-artist-${token}`">
              <button
                class="meta-link author-link token-chip viewer-token-chip"
                :class="{ 'is-favorited-chip': favoritedArtistSet.has(token) }"
                @click="applySearch(token); closeViewer();"
                :title="`搜索同画师作品：${token}`"
              >{{ token }}</button>
              <button
                v-if="token !== '未知'"
                class="meta-link author-fav-btn viewer-fav-star"
                @click.stop="openFavoriteDialog(token)"
                title="加入画师收藏"
              >★</button>
            </template>
          </div>
          <div v-if="viewerItem?.characterTokens?.length" class="viewer-meta-block">
            <span class="viewer-meta-label">角色</span>
            <template v-for="token in viewerItem.characterTokens" :key="`v-char-${token}`">
              <button
                class="meta-link token-chip viewer-token-chip"
                :class="{ 'is-favorited-chip': favoritedCharacterSet.has(token) }"
                @click="applySearch(token); closeViewer();"
                :title="`搜索同角色作品：${token}`"
              >{{ token.includes(' [') ? token.split(' [')[0] : token }}</button>
              <button
                class="meta-link author-fav-btn viewer-fav-star"
                @click.stop="openCharacterFavoriteDialog(token)"
                title="加入角色收藏（按 source_hint 合并分组）"
              >★</button>
            </template>
          </div>
          <div class="viewer-meta-block viewer-counter-block">
            <span class="muted compact-text" style="color: #ccc;">
              第
              <input
                class="viewer-jump-input"
                type="number"
                min="1"
                :max="viewerItems.length"
                :value="viewer.index + 1"
                @keyup.enter="onViewerJump($event)"
                @change="onViewerJump($event)"
              />
              / {{ viewerItems.length }} 张
            </span>
            <span v-if="(viewerItem?.score || 0) > 0" class="viewer-score">★ {{ viewerItem.score }}</span>
            <span v-if="(viewerItem?.favCount || 0) > 0" class="viewer-fav">♥ {{ viewerItem.favCount }}</span>
          </div>
        </div>
        <div class="button-row compact viewer-actions">
          <button class="secondary" @click="stepViewer(-1)" :disabled="viewer.index <= 0">上一张</button>
          <button class="secondary" @click="stepViewer(1)" :disabled="viewer.index >= viewerItems.length - 1">下一张</button>
          <button
            class="secondary"
            @click="toggleViewerFitMode"
            :title="viewer.fitMode === 'fit' ? '当前：适应窗口，点击切换为原始大小' : '当前：原始大小，点击切换为适应窗口'"
          >{{ viewer.fitMode === 'fit' ? '⛶ 原始大小' : '▣ 适应窗口' }}</button>
          <button
            class="secondary"
            :class="{ 'pin-active': viewer.toolbarPinned }"
            @click="toggleViewerToolbarPin"
            :title="viewer.toolbarPinned ? '已固定信息栏，点击取消固定（恢复鼠标悬浮显示）' : '固定信息栏（默认悬浮显示）'"
          >{{ viewer.toolbarPinned ? '📌 已固定' : '📌 固定' }}</button>
          <button
            class="viewer-fav-btn"
            :class="{ active: viewerItem && isImageFavorited(viewerItem) }"
            @click="viewerItem && toggleImageFavorite(viewerItem)"
            :disabled="!viewerItem"
            :title="viewerItem && isImageFavorited(viewerItem) ? '取消图片收藏' : '加入图片收藏'"
          >{{ viewerItem && isImageFavorited(viewerItem) ? '♥ 已收藏' : '♡ 收藏' }}</button>
          <button v-if="viewerItem?.filename?.toLowerCase().endsWith('.zip')" class="secondary" @click="convertGif(viewerItem)" style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;">转GIF</button>
          <button @click="editItem(viewerItem)" style="background: linear-gradient(135deg, var(--accent), var(--accent-deep)); border: none; color: white;">编辑图片</button>
          <button class="ghost" @click="closeViewer" style="color: #fff; border: 1px solid rgba(255,255,255,0.2);">关闭</button>
        </div>
      </div>
      <div class="viewer-stage" :class="{ 'is-fit': viewer.fitMode === 'fit' }" @wheel="onViewerWheel" @click.self="closeViewer">
        <div class="viewer-image-wrap" :class="{ 'is-fit': viewer.fitMode === 'fit' }" :style="{ zoom: viewer.zoom }">
          <video
            v-if="viewer.imageUrl && viewerIsVideo"
            class="viewer-image"
            :src="viewer.imageUrl"
            controls
            autoplay
            preload="metadata"
          />
          <img
            v-else-if="viewer.imageUrl"
            class="viewer-image"
            :src="viewer.imageUrl"
            :alt="viewerItem?.filename || 'preview'"
          />
        </div>
      </div>
    </div>

    <!-- Toast Notification -->
    <div v-if="toast.show" class="toast-overlay" :class="toast.type">
      {{ toast.msg }}
    </div>

    <!-- Hosts Modal -->
    <div v-if="hostsModal.open" class="viewer-overlay" @click.self="hostsModal.open = false" style="z-index: 10000; display: flex; justify-content: center; align-items: center;">
      <div class="card panel" style="width: 480px; max-width: 90vw; background: rgba(255, 255, 255, 0.95); box-shadow: 0 20px 50px rgba(0,0,0,0.3); display: flex; flex-direction: column; gap: 14px;">
        <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">修复连接问题 (修改 Hosts)</h3>
        <p style="margin: 0; font-size: 13px;">请在记事本中打开以下路径的文件：</p>
        <code style="background: rgba(0,0,0,0.05); padding: 6px 10px; border-radius: 6px; font-size: 12px; user-select: all;">C:\Windows\System32\drivers\etc\hosts</code>
        <p style="margin: 0; font-size: 13px;">并在文件最末尾添加以下内容（可直接全选复制）：</p>
        <textarea readonly style="width: 100%; height: 60px; font-family: Consolas, monospace; font-size: 13px; resize: none; background: rgba(0,0,0,0.03); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: 10px; outline: none; cursor: text;" onfocus="this.select()">{{ safeMode ? '104.26.11.39 safebooru.donmai.us' : '104.26.11.39 danbooru.donmai.us' }}</textarea>
        <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 10px;">
          <button @click="openHostsFolder" class="secondary">打开目录</button>
          <button @click="hostsModal.open = false" style="min-width: 80px;">确定</button>
        </div>
      </div>
    </div>

    <!-- Translation Modal (list of untranslated characters) -->
    <div v-if="translationModal.open" class="viewer-overlay translation-overlay" @click.self="closeTranslationModal">
      <div class="translation-card">
        <div class="translation-head">
          <div>
            <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">未翻译角色 · {{ gallery.selectedDate || '' }}</h3>
            <p class="muted compact-text" style="margin: 4px 0 0;">
              共 {{ translationModal.list.length }} 个 · 已筛选 {{ filteredUntranslated.length }} 个
            </p>
          </div>
          <button class="ghost" @click="closeTranslationModal" style="color: var(--muted);">×</button>
        </div>

        <input
          v-model="translationModal.search"
          class="search-input"
          type="text"
          placeholder="搜索 tag 或回退名"
          style="width: 100%; margin-bottom: 10px;"
        />

        <div class="translation-list">
          <div v-if="translationModal.loading" class="gallery-empty" style="min-height: 120px;">正在加载...</div>
          <div v-else-if="!filteredUntranslated.length" class="gallery-empty" style="min-height: 120px;">
            {{ translationModal.list.length ? '没有匹配的角色' : '当前日期没有未翻译的角色 🎉' }}
          </div>
          <div
            v-else
            v-for="item in filteredUntranslated"
            :key="item.tag"
            class="translation-row"
            @click="openTranslateDetail(item)"
          >
            <div class="translation-row-main">
              <span class="translation-row-tag">{{ item.tag }}</span>
              <span class="translation-row-fallback">{{ item.fallback_name }}</span>
            </div>
            <span class="translation-row-count">出现 {{ item.post_count }} 次</span>
          </div>
        </div>

        <div class="translation-foot">
          <span class="muted compact-text">
            完成后点「导入到画廊」把 character_chinese_search.json 合并进 custom_translation.json
          </span>
          <div style="display: flex; gap: 8px;">
            <button class="ghost" @click="closeTranslationModal" style="color: var(--accent-deep);">关闭</button>
            <button
              @click="importTranslationDict"
              :disabled="translationModal.importing"
              style="min-width: 130px;"
            >{{ translationModal.importing ? '导入中...' : '导入到画廊' }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Translate Detail Modal (single character) -->
    <div v-if="translateDetail.open" class="viewer-overlay translation-overlay" @click.self="closeTranslateDetail" style="z-index: 10010;">
      <div class="translation-card translation-detail-card">
        <div class="translation-head">
          <div style="min-width: 0;">
            <h3 style="margin: 0; color: var(--accent-deep); font-size: 17px; word-break: break-all;">{{ translateDetail.tag }}</h3>
            <p class="muted compact-text" style="margin: 4px 0 0;">回退名: {{ translateDetail.fallbackName }}</p>
          </div>
          <button class="ghost" @click="closeTranslateDetail" style="color: var(--muted);">×</button>
        </div>

        <div v-if="!translateDetail.source.exists" class="translation-fetch-banner">
          <span style="flex: 1; min-width: 0;">
            ⚠ character.json 中没有这条记录。可以点右侧按钮调用 Danbooru wiki API 在线拉描述（会写入 character_supplement.json）。
            <span v-if="translateDetail.fetchMsg" style="display: block; color: #9d2c2c; margin-top: 4px;">{{ translateDetail.fetchMsg }}</span>
          </span>
          <button
            class="secondary"
            @click="fetchCharacterSource"
            :disabled="translateDetail.fetchBusy"
            style="flex-shrink: 0; min-width: 130px;"
          >{{ translateDetail.fetchBusy ? '拉取中...' : '🔎 在线拉描述' }}</button>
        </div>

        <!-- 描述区固定在头部下方，不进 scrolling body，保证切换 tab / 滚动表单时一直可见 -->
        <div class="translation-detail-section translation-description-pinned">
          <div class="translation-detail-section-head static">
            <span>英文描述与候选名（{{ translateDetail.source.other_names.length }} 个候选）</span>
          </div>
          <div class="translation-detail-section-body">
            <div v-if="translateDetail.source.other_names.length" style="margin-bottom: 8px;">
              <strong style="font-size: 12px;">候选名: </strong>
              <span class="muted compact-text">{{ translateDetail.source.other_names.slice(0, 30).join(' / ') }}</span>
            </div>
            <pre class="translation-desc">{{ translateDetail.source.description || '(无描述，可点上方「在线拉描述」)' }}</pre>
          </div>
        </div>

        <div class="translation-detail-body">
          <div class="translation-mode-tabs">
            <button
              class="mode-chip"
              :class="{ active: translateDetail.mode === 'api' }"
              @click="translateDetail.mode = 'api'"
            >API 翻译</button>
            <button
              class="mode-chip"
              :class="{ active: translateDetail.mode === 'manual' }"
              @click="translateDetail.mode = 'manual'"
            >手动翻译（复制 Prompt）</button>
          </div>

          <div v-if="translateDetail.mode === 'api'" class="translation-mode-body">
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
              <button
                @click="runApiTranslation"
                :disabled="translateDetail.apiBusy"
                style="min-width: 130px;"
              >{{ translateDetail.apiBusy ? '调用中...' : '立即调用 API' }}</button>
              <span class="muted compact-text">
                {{ translateDetail.apiBusy ? '正在请求 openrouter，可能需要几十秒' : (translateDetail.source.exists ? '复用 .env 中的 openrouter_api_key（含描述）' : '没有本地描述，建议先点上方「在线拉描述」；也可直接调用 API 仅凭 tag 名翻译') }}
              </span>
            </div>
            <div v-if="translateDetail.apiError" class="translation-api-error">
              <strong>API 失败：</strong>{{ translateDetail.apiError }}
              <div v-if="translateDetail.apiRaw" class="muted compact-text" style="margin-top: 6px;">
                已把原始 LLM 输出灌进下方「手动翻译」标签的文本框，请修复 JSON 后点「解析填表」。
              </div>
            </div>
          </div>

          <div v-else class="translation-mode-body">
            <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 8px; flex-wrap: wrap;">
              <button class="secondary" @click="copyManualPrompt" style="min-width: 130px;">复制 Prompt</button>
              <span class="muted compact-text">粘贴到你的大模型，把返回的 JSON 贴到下方</span>
            </div>
            <textarea
              v-model="translateDetail.pasteText"
              placeholder='把大模型返回的 JSON 粘贴到这里，例如：{"has_chinese": true, "chinese_name": "...", ...}'
              class="translation-paste"
            ></textarea>
            <div style="display: flex; gap: 10px; align-items: center; margin-top: 6px; flex-wrap: wrap;">
              <button class="secondary" @click="parsePastedJson" style="min-width: 130px;">解析填表</button>
              <span v-if="translateDetail.parseError" class="error-text" style="margin: 0;">{{ translateDetail.parseError }}</span>
            </div>
          </div>

          <div class="translation-form">
            <label class="translation-form-row">
              <input type="checkbox" v-model="translateDetail.form.has_chinese" />
              <span>有中文名</span>
            </label>
            <label class="translation-form-field">
              <span>中文名</span>
              <input v-model="translateDetail.form.chinese_name" type="text" placeholder="例如：初音未来" />
            </label>
            <label class="translation-form-field">
              <span>source_hint（小写英文，例如 vocaloid / touhou）</span>
              <input v-model="translateDetail.form.source_hint" type="text" placeholder="例如：touhou" />
            </label>
            <label class="translation-form-field">
              <span>中文简介</span>
              <textarea
                v-model="translateDetail.form.translated_description_zh"
                placeholder="可选：角色的中文简介，会显示在画廊详情里"
                class="translation-desc-input"
              ></textarea>
            </label>
          </div>
        </div>

        <div class="translation-foot">
          <span class="muted compact-text">保存后会写入 character_chinese_search.json</span>
          <div style="display: flex; gap: 8px;">
            <button class="ghost" @click="closeTranslateDetail" style="color: var(--accent-deep);">取消</button>
            <button
              @click="saveTranslation"
              :disabled="translateDetail.saving"
              style="min-width: 110px;"
            >{{ translateDetail.saving ? '保存中...' : '保存到字典' }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 刷新范围 Modal -->
    <div v-if="rangeRefresh.open" class="viewer-overlay" @click.self="closeRangeRefreshDialog" style="z-index: 10020; display: flex; justify-content: center; align-items: center; padding: 24px;">
      <div class="range-refresh-modal">
        <div class="range-refresh-head">
          <h3 style="margin: 0; color: var(--accent-deep); font-size: 17px;">刷新指定范围页的热度</h3>
          <button class="ghost" @click="closeRangeRefreshDialog" style="color: var(--muted);">×</button>
        </div>
        <p class="muted compact-text" style="margin: 0;">
          当前共 {{ activeTotalPages }} 页 · 每页 {{ gallery.pageSize }} 张 · 当前所在第 {{ activePage }} 页
        </p>
        <div class="field-grid">
          <label>
            <span>起始页</span>
            <input v-model.number="rangeRefresh.startPage" type="number" min="1" :max="activeTotalPages" />
          </label>
          <label>
            <span>结束页</span>
            <input v-model.number="rangeRefresh.endPage" type="number" min="1" :max="activeTotalPages" />
          </label>
        </div>
        <p class="muted compact-text" style="margin: 0;">
          将刷新 <strong style="color: var(--accent-deep);">{{ rangeRefreshCount }}</strong> 张图片的 score / 收藏数 / 画师（孤立文件会反查补全）
        </p>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <button class="ghost" @click="closeRangeRefreshDialog" style="color: var(--accent-deep);">取消</button>
          <button @click="startRefreshScoresRange" :disabled="!rangeRefreshCount || refresh.isRunning" style="min-width: 100px;">
            {{ refresh.isRunning ? '刷新中...' : '确定刷新' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 加入画师收藏 Modal -->
    <div v-if="favoriteDialog.open" class="viewer-overlay" @click.self="closeFavoriteDialog" style="z-index: 10020; display: flex; justify-content: center; align-items: center; padding: 24px;">      <div class="fav-add-modal">
        <div class="fav-add-head">
          <div>
            <h3 style="margin: 0; color: var(--accent-deep); font-size: 17px;">加入画师收藏</h3>
            <p class="muted compact-text" style="margin: 4px 0 0;">画师：<strong style="color: var(--ink); font-family: Consolas, monospace;">{{ favoriteDialog.artist }}</strong></p>
          </div>
          <button class="ghost" @click="closeFavoriteDialog" style="color: var(--muted);">×</button>
        </div>

        <div v-if="favoriteDialog.loading" class="muted compact-text" style="text-align: center; padding: 20px;">加载分组中...</div>
        <template v-else>
          <div class="fav-add-list">
            <label v-for="g in favGroupList" :key="g.name" class="fav-add-row">
              <input
                type="checkbox"
                :checked="favoriteDialog.selectedGroups.includes(g.name)"
                @change="toggleFavGroup(g.name)"
              />
              <span class="fav-add-name">{{ g.name }}</span>
              <span class="fav-add-count">{{ g.count }}</span>
            </label>
            <div v-if="!favGroupList.length" class="muted compact-text" style="text-align: center; padding: 12px;">
              还没有分组，请在下方创建一个
            </div>
          </div>

          <div class="fav-add-new">
            <input
              v-model="favoriteDialog.newGroupName"
              type="text"
              placeholder="新分组名称，例如：厚涂大佬"
              @keyup.enter="createFavGroupInline"
            />
            <button class="secondary" @click="createFavGroupInline" style="white-space: nowrap;">+ 新建</button>
          </div>
        </template>

        <div class="fav-add-foot">
          <span class="muted compact-text">已勾选 {{ favoriteDialog.selectedGroups.length }} 个分组</span>
          <div style="display: flex; gap: 8px;">
            <button class="ghost" @click="closeFavoriteDialog" style="color: var(--accent-deep);">取消</button>
            <button @click="saveFavoriteDialog" :disabled="favoriteDialog.saving" style="min-width: 90px;">
              {{ favoriteDialog.saving ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 加入角色收藏 Modal -->
    <div v-if="charFavoriteDialog.open" class="viewer-overlay" @click.self="closeCharacterFavoriteDialog" style="z-index: 10020; display: flex; justify-content: center; align-items: center; padding: 24px;">
      <div class="fav-add-modal">
        <div class="fav-add-head">
          <div>
            <h3 style="margin: 0; color: var(--accent-deep); font-size: 17px;">加入角色收藏</h3>
            <p class="muted compact-text" style="margin: 4px 0 0;">
              角色：<strong style="color: var(--ink); font-family: Consolas, monospace;">{{ charFavoriteDialog.character }}</strong>
            </p>
            <p v-if="charFavoriteDialog.sourceHint" class="muted compact-text" style="margin: 2px 0 0;">
              出处 (source_hint)：<strong style="color: var(--accent-deep);">{{ charFavoriteDialog.sourceHint }}</strong>
              <span class="muted compact-text">· 同出处的角色会自动合并到同名分组</span>
            </p>
          </div>
          <button class="ghost" @click="closeCharacterFavoriteDialog" style="color: var(--muted);">×</button>
        </div>

        <div v-if="charFavoriteDialog.loading" class="muted compact-text" style="text-align: center; padding: 20px;">加载分组中...</div>
        <template v-else>
          <div class="fav-add-list">
            <label v-for="g in charFavGroupList" :key="g.name" class="fav-add-row">
              <input
                type="checkbox"
                :checked="charFavoriteDialog.selectedGroups.includes(g.name)"
                @change="toggleCharFavGroup(g.name)"
              />
              <span class="fav-add-name">{{ g.name }}</span>
              <span class="fav-add-count">{{ g.count }}</span>
            </label>
            <div v-if="!charFavGroupList.length" class="muted compact-text" style="text-align: center; padding: 12px;">
              还没有分组，下方已为你预填 source_hint 作为新分组名
            </div>
          </div>

          <div class="fav-add-new">
            <input
              v-model="charFavoriteDialog.newGroupName"
              type="text"
              placeholder="新分组名（建议 = source_hint，例如 vocaloid / touhou）"
              @keyup.enter="createCharFavGroupInline"
            />
            <button class="secondary" @click="createCharFavGroupInline" style="white-space: nowrap;">+ 新建</button>
          </div>
        </template>

        <div class="fav-add-foot">
          <span class="muted compact-text">已勾选 {{ charFavoriteDialog.selectedGroups.length }} 个分组</span>
          <div style="display: flex; gap: 8px;">
            <button class="ghost" @click="closeCharacterFavoriteDialog" style="color: var(--accent-deep);">取消</button>
            <button @click="saveCharacterFavoriteDialog" :disabled="charFavoriteDialog.saving" style="min-width: 90px;">
              {{ charFavoriteDialog.saving ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 左栏由全局 340px 缩到 300px，把多出来的 40px 让给右侧图片网格 */
.crawler-layout {
  grid-template-columns: 300px minmax(0, 1fr);
}
@media (max-width: 1400px) {
  .crawler-layout {
    grid-template-columns: 270px minmax(0, 1fr);
  }
}

/* 控制面板设为 relative，给日志面板的「放大」态做绝对定位锚点 */
.control-panel {
  position: relative;
  /* 全局 .control-panel 给的是 position: sticky + top: 16px。把 position 改成 relative
     后，top: 16px 会从「sticky 阈值」变成「真实位移」，把整个左栏往下推 16px。
     必须显式 top: 0 把它消掉，否则左栏会比右栏低 16px。 */
  top: 0;
  /* 钉死成视口高度：
     1. 与右侧 gallery-panel 同高，不再需要 align-items: stretch 兜底
     2. 日志「放大」态用 position: absolute + inset:0 时有完整面板可铺满，
        不会出现"放大后左栏反而变矮"的诡异（之前日志 absolute 后脱离流，
        左栏只剩控件高度，导致 absolute 的 inset:0 也只铺这点高度） */
  height: calc(100vh - 32px);
  max-height: calc(100vh - 32px);
}

/* 右侧本地图库栏：同样视口高度。
   不用 position: sticky（之前用，但跟左栏的 relative 不一致会让两栏顶部出现
   微妙的偏移；用 relative 保持一致，反正两栏都钉死了视口高度也不需要吸顶）。 */
.gallery-panel {
  position: relative;
  height: calc(100vh - 32px);
  max-height: calc(100vh - 32px);
}

/* 关键：两栏 h2 都显式 margin: 0，确保都从卡片内边距 (Y=14) 开始绘制。
   不依赖浏览器默认 margin + flex/block 容器之间的 margin collapse 规则差异
   ——左栏 panel-head 里 h2 是 block 容器的子元素（margin 会向上 collapse 到父级），
   右栏 gallery-title-row 是 flex 容器（flex item 的 margin 不 collapse），
   两种容器对 h2 margin 的处理不同会导致一边比另一边低 20px。
   都归零最稳定。 */
.control-panel .panel-head h2,
.gallery-title-row h2 {
  margin: 0;
}
.gallery-panel .gallery-grid {
  /* flex 占满除 head/calendar/pagination 之外的剩余空间；
     overflow: auto 由全局给好，行数多时网格内部滚动。

     关键：必须显式 align-content: start + grid-auto-rows: max-content，
     否则某些浏览器/场景下 grid 会把多余的垂直空间均分给行，
     导致只有 1-2 行卡片时被拉到面板那么高（用户报告的"卡片拉长到整页一样大"），
     或 3+ 行装不下时压扁行高把按钮裁掉（用户报告的"按钮显示不出来"）。
     start 把多余空间放到网格末端，max-content 锁死行高 = 卡片自然高度。 */
  flex: 1 1 auto;
  min-height: 0;
  align-content: start;
  grid-auto-rows: max-content;
}

/* 覆盖全局 .image-card { contain-intrinsic-size: 320px } 的单值占位。
   现在卡片移除了 card-meta，自然高度只剩 thumb(=width) + 按钮行(40) + padding(16)。
   按当前 cardSize 默认 150 算大约 206px；给 220 留点余量。
   同时强制 align-self: start，杜绝 align-items: stretch 把卡片拉到行高（即使
   行高被 align-content: start 锁死，再加一层保险） */
.image-card {
  contain-intrinsic-size: 200px 220px;
  align-self: start;
}

/* 卡片最小宽度由 gallery.cardSize 通过 inline --card-min-w 注入，
   覆盖全局 .gallery-grid 的 minmax(180px, 1fr)。 */
.gallery-grid {
  grid-template-columns: repeat(auto-fill, minmax(var(--card-min-w, 180px), 1fr));
}

/* 卡片内的「原帖 / 本地 / 编辑 / 转GIF」按钮：
   全局 button padding 8px 12px + font 13px，在 120-150px 窄卡片里被 flex:1 平分
   后会出现"一字一行"。这里只在卡片范围内压低 padding 和字号，让 2 字按钮在
   最窄 120px 卡片下也能横排不换行 */
.image-card .button-row.compact button {
  padding: 4px 6px;
  font-size: 11px;
  border-radius: 8px;
  min-width: 0;
  white-space: nowrap;
}

/* 卡片移除了 .card-meta 后，按钮行直接贴在缩略图下方。
   给行本身加点 padding，避免按钮蹭到卡片圆角边缘 */
.image-card .card-actions {
  padding: 8px;
  margin-top: auto;  /* 卡片内剩余空间挤到顶部，让按钮总是贴底，缩略图保持在上 */
}

/* gallery-head 改成两行竖排：
   第 1 行 = 标题行（日期 h2 在左，统计文本贴右上角）
   第 2 行 = 工具栏（所有筛选/排序/刷新/搜索/翻译） */
.gallery-head {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}
.gallery-title-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
/* 不要覆写 h2 margin！其他页（EditorPage/FavoritesPage）都用 h2 默认 margin。
   覆写成 margin:0 会让右栏 h2 比左栏 h2 高出约 20px（左栏 h2 有默认 margin-top），
   导致"抓图任务"和右栏日期标题不对齐。让 h2 保持默认 margin 即可与左栏对齐。 */
.gallery-stats-inline {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 工具栏紧凑化：尽量一行装下所有控件 */
.gallery-tools {
  gap: 6px;
  justify-content: flex-start;
  font-size: 11.5px;
}
.gallery-tools select.search-input,
.gallery-tools .search-input-with-clear,
.gallery-tools .tool-btn,
.gallery-tools .hot-toggle,
.gallery-tools .refresh-btn {
  font-size: 11.5px;
  padding: 4px 8px;
  height: 28px;
  white-space: nowrap;
}
.gallery-tools .gallery-sort-select {
  font-weight: 600;
}

/* 搜索框默认紧凑 130，focus 拉宽以便看清完整输入 */
.gallery-tools .search-input-wrap .search-input {
  width: 130px;
  transition: width 0.18s ease;
}
.gallery-tools .search-input-wrap .search-input:focus {
  width: 180px;
}

/* ---------------- 查看器顶部画师/角色 meta（从卡片挪过来的） ----------------
   全局 .viewer-toolbar 是水平 pill。在挤进很多角色 chip 后会把右侧的
   上一张/下一张/收藏 等按钮挤窄到「一字三行」。
   这里把工具栏改成 column 布局：第 1 行 = 画师/角色/计数 chip 区（可换行）；
   第 2 行 = 操作按钮（单行不换行），各占据全宽。 */
.viewer-toolbar {
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
  max-width: 92vw;
  min-width: 480px;
}
.viewer-toolbar-info {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  flex: 1 1 auto;
  min-width: 0;
}
.viewer-meta-block {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  max-width: 100%;
}
.viewer-meta-label {
  font-size: 11px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.55);
  letter-spacing: 0.5px;
  margin-right: 2px;
}
/* 查看器在深色蒙层上，chip 默认浅色背景对比好但 hover/收藏态需要点反白 */
.viewer-token-chip {
  background: rgba(255, 255, 255, 0.92);
  color: var(--ink);
  font-size: 12px;
}
.viewer-token-chip:hover {
  background: #fff;
  color: var(--accent-deep);
}
.viewer-token-chip.is-favorited-chip {
  background: linear-gradient(135deg, rgba(255, 188, 86, 0.95), rgba(212, 143, 47, 0.9));
  color: #4a2e08;
  border-color: rgba(212, 143, 47, 0.7);
  font-weight: 700;
}
.viewer-fav-star {
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: #ffd699;
  padding: 2px 6px;
  font-size: 11px;
}
.viewer-fav-star:hover {
  background: rgba(255, 188, 86, 0.4);
  color: #fff;
  border-color: rgba(255, 188, 86, 0.7);
}
.viewer-counter-block {
  margin-left: auto;
  gap: 8px;
}

/* 操作按钮行：第 2 行，居中横排，绝不换行 */
.viewer-actions {
  flex-wrap: nowrap !important;
  justify-content: center;
  gap: 8px;
}
.viewer-actions button {
  flex: 0 0 auto;
  white-space: nowrap;
}

/* Modern Log UI */
.modern-log-wrapper {
  margin-top: 12px;
  background: rgba(26, 20, 15, 0.96);
  border-radius: 16px;
  border: 1px solid rgba(0, 0, 0, 0.1);
  box-shadow: 0 8px 32px rgba(87, 58, 25, 0.15);
  display: flex;
  flex-direction: column;
  /* 固定日志面板最大高度。之前 control-panel 解除了 max-height 后，
     日志会随条数不停拉高把整栏带着无限增长。锁到 360px 后多余日志在
     .modern-log-body 内部滚动，整栏高度可预期。放大态会单独覆盖此限制。 */
  max-height: 360px;
  transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
}
.modern-log-wrapper.is-expanded {
  flex: 1;
  min-height: 0;
}

/* 放大态：脱离正常流，铺满整个 control-panel 卡片 */
.modern-log-wrapper.is-maximized {
  position: absolute;
  inset: 0;
  margin-top: 0;
  z-index: 20;
  border-radius: 18px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.45);
  min-height: 0;
  /* 放大态需要覆盖默认的 360px 上限 */
  max-height: none;
  flex: none;
}
.modern-log-wrapper.is-maximized .modern-log-header {
  cursor: default;
  border-radius: 18px 18px 0 0;
}
.modern-log-wrapper.is-maximized .modern-log-body {
  flex: 1 1 auto;
  min-height: 0;
}

.modern-log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  cursor: pointer;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0) 100%);
  user-select: none;
  border-radius: 16px;
  transition: background 0.2s;
}
.modern-log-wrapper.is-expanded .modern-log-header {
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
}
.modern-log-header:hover {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0.02) 100%);
}

.log-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #666;
  box-shadow: 0 0 0 2px rgba(102, 102, 102, 0.2);
}
.status-dot.is-active {
  background: #51cf66;
  box-shadow: 0 0 0 2px rgba(81, 207, 102, 0.2);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(81, 207, 102, 0.4); }
  70% { box-shadow: 0 0 0 6px rgba(81, 207, 102, 0); }
  100% { box-shadow: 0 0 0 0 rgba(81, 207, 102, 0); }
}

.log-title {
  color: #fff;
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.log-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.log-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: rgba(255, 255, 255, 0.85);
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s, color 0.2s;
}
.log-icon-btn:hover {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.3);
  color: #fff;
}

.modern-log-body {
  padding: 0 16px 16px 16px;
  overflow-y: auto;
  flex: 1 1 auto;
  min-height: 0;
}

.modern-log-body::-webkit-scrollbar {
  width: 6px;
}
.modern-log-body::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 3px;
}

.modern-log-line {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.4;
  word-break: break-all;
  animation: slideIn 0.2s ease-out forwards;
  cursor: pointer;
  transition: background 0.15s ease;
}
.modern-log-line:hover {
  background: rgba(255, 255, 255, 0.04);
}
.modern-log-line .log-text {
  flex: 1;
  min-width: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
}
.modern-log-line.is-expanded {
  background: rgba(255, 255, 255, 0.06);
}
.modern-log-line.is-expanded .log-text {
  -webkit-line-clamp: unset;
  line-clamp: unset;
  overflow: visible;
  white-space: pre-wrap;
  user-select: text;
}
.modern-log-line:last-child {
  border-bottom: none;
}

.modern-log-hint {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.55);
  padding: 6px 0;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.1);
  margin-bottom: 4px;
  font-style: italic;
}

.log-toolbar-btn {
  font-size: 11px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: rgba(255, 255, 255, 0.85);
  padding: 3px 10px;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
  font-family: inherit;
}
.log-toolbar-btn:hover {
  background: rgba(255, 255, 255, 0.16);
  border-color: rgba(255, 255, 255, 0.3);
}
.log-toolbar-btn.active {
  background: rgba(255, 188, 86, 0.25);
  border-color: rgba(255, 188, 86, 0.5);
  color: #ffd699;
}

.error-banner {
  margin-top: 10px;
  background: rgba(120, 30, 30, 0.18);
  border: 1px solid rgba(255, 90, 90, 0.45);
  border-radius: 12px;
  color: #ffb4b4;
  font-size: 12px;
  overflow: hidden;
}
.error-banner-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
}
.error-banner-icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 80, 80, 0.3);
  color: #fff;
  border-radius: 50%;
  font-weight: bold;
  font-size: 12px;
}
.error-banner-text {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
}
.error-banner-actions {
  flex-shrink: 0;
  display: flex;
  gap: 6px;
}
.error-banner-btn {
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #ffd5d5;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  cursor: pointer;
  font-family: inherit;
}
.error-banner-btn:hover {
  background: rgba(255, 255, 255, 0.18);
}
.error-banner-body {
  margin: 0;
  padding: 6px 12px 12px 12px;
  max-height: 160px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 11px;
  color: #ffd5d5;
  background: rgba(0, 0, 0, 0.25);
  border-top: 1px solid rgba(255, 90, 90, 0.25);
  user-select: text;
}

.log-icon {
  flex-shrink: 0;
  width: 14px;
  text-align: center;
  font-weight: bold;
}

.log-info { color: #d4d4d4; }
.log-info .log-icon { color: #4dabf7; }

.log-success { color: #d4d4d4; }
.log-success .log-text { color: #8ce99a; font-weight: bold; }
.log-success .log-icon { color: #51cf66; }

.log-error { color: #ffa8a8; font-weight: bold; }
.log-error .log-icon { color: #ff6b6b; }

.log-warn { color: #ffec99; }
.log-warn .log-icon { color: #fcc419; }

/* Transitions */
.log-expand-enter-active, .log-expand-leave-active {
  transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  overflow: hidden;
}
.log-expand-enter-from, .log-expand-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
.log-expand-enter-to, .log-expand-leave-from {
  opacity: 1;
  transform: translateY(0);
}

@keyframes slideIn {
  from { opacity: 0; transform: translateX(-5px); }
  to { opacity: 1; transform: translateX(0); }
}

.toast-overlay {
  position: fixed;
  top: 24px;
  left: 50%;
  transform: translateX(-50%);
  padding: 12px 24px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 14px;
  z-index: 10000;
  box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  animation: fadeInDown 0.3s ease-out;
  pointer-events: none;
}
.toast-overlay.success { background: rgba(212, 237, 218, 0.95); color: #155724; border: 1px solid #c3e6cb; }
.toast-overlay.error { background: rgba(248, 215, 218, 0.95); color: #721c24; border: 1px solid #f5c6cb; }
.toast-overlay.info { background: rgba(209, 236, 241, 0.95); color: #0c5460; border: 1px solid #bee5eb; }

@keyframes fadeInDown {
  from { opacity: 0; transform: translate(-50%, -15px); }
  to { opacity: 1; transform: translate(-50%, 0); }
}

/* ---------------- 角色增量翻译弹窗 ---------------- */
.translation-overlay {
  z-index: 10000;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 24px;
}
.translation-card {
  width: 720px;
  max-width: 92vw;
  max-height: 86vh;
  background: rgba(255, 250, 243, 0.98);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}
.translation-detail-card {
  width: 760px;
  max-height: 90vh;
}
.translation-detail-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 4px;
}
.translation-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}
.translation-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.55);
}
.translation-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px dashed rgba(74, 53, 25, 0.12);
  cursor: pointer;
  transition: background 0.15s ease;
}
.translation-row:hover {
  background: rgba(243, 223, 212, 0.5);
}
.translation-row:last-child {
  border-bottom: none;
}
.translation-row-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.translation-row-tag {
  font-family: Consolas, monospace;
  font-size: 13px;
  color: var(--ink);
  word-break: break-all;
}
.translation-row-fallback {
  font-size: 12px;
  color: var(--muted);
}
.translation-row-count {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--accent-deep);
  background: var(--soft);
  padding: 3px 10px;
  border-radius: 999px;
  font-weight: 600;
}
.translation-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
  flex-wrap: wrap;
}
.translation-detail-section {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.55);
  overflow: hidden;
}
.translation-description-pinned {
  flex: 0 0 auto;
}
.translation-detail-section-head {
  padding: 8px 12px;
  background: rgba(243, 223, 212, 0.5);
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  user-select: none;
}
.translation-detail-section-head.static {
  cursor: default;
}
.translation-detail-section-body {
  padding: 10px 12px;
  max-height: 150px;
  overflow-y: auto;
}
.translation-desc {
  margin: 0;
  font-family: Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ink);
}
.translation-mode-tabs {
  display: flex;
  gap: 8px;
}
.translation-mode-body {
  padding: 10px 12px;
  background: rgba(243, 223, 212, 0.25);
  border-radius: 12px;
  border: 1px dashed rgba(74, 53, 25, 0.18);
}
.translation-paste {
  width: 100%;
  height: 64px;
  padding: 8px 10px;
  font-family: Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  color: var(--ink);
  resize: vertical;
  margin-top: 6px;
}
.translation-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.translation-form-row {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--ink);
}
.translation-form-row input[type="checkbox"] {
  width: auto;
  margin: 0;
}
.translation-form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.translation-desc-input {
  width: 100%;
  height: 90px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.55;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  color: var(--ink);
  resize: vertical;
}
.translation-fetch-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: rgba(212, 143, 47, 0.12);
  border: 1px solid rgba(212, 143, 47, 0.35);
  border-radius: 10px;
  color: #8a5a14;
  font-size: 12px;
  line-height: 1.5;
}
.translation-api-error {
  margin-top: 8px;
  padding: 10px 12px;
  background: rgba(157, 44, 44, 0.08);
  border: 1px solid rgba(157, 44, 44, 0.35);
  border-radius: 10px;
  color: #9d2c2c;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
  max-height: 160px;
  overflow-y: auto;
}

/* ---------------- 画师 chip ★ 按钮 + 加入收藏弹窗 ---------------- */
.author-fav-btn {
  padding: 2px 6px;
  font-size: 11px;
  color: #b46e16;
  background: rgba(243, 223, 212, 0.45);
  border: 1px solid rgba(212, 143, 47, 0.35);
}
.author-fav-btn:hover {
  background: rgba(255, 188, 86, 0.3);
  color: #8a5a14;
  border-color: rgba(212, 143, 47, 0.55);
}

.fav-add-modal {
  width: 440px;
  max-width: 92vw;
  background: rgba(255, 250, 243, 0.98);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.fav-add-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
}
.fav-add-list {
  max-height: 260px;
  overflow-y: auto;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid var(--line);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.fav-add-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--ink);
}
.fav-add-row:hover { background: rgba(243, 223, 212, 0.55); }
.fav-add-row input[type="checkbox"] { width: auto; margin: 0; }
.fav-add-name { flex: 1; word-break: break-all; }
.fav-add-count {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--soft);
  color: var(--accent-deep);
}
.fav-add-new {
  display: flex;
  gap: 8px;
  align-items: center;
}
.fav-add-new input { flex: 1; }
.fav-add-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

/* ---------------- 刷新热度下拉菜单（合并原本 3 个按钮） ---------------- */
.refresh-dropdown {
  position: relative;
  display: inline-block;
}
.refresh-btn.menu-open {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
}

/* ---------------- 翻译下拉菜单（合并「翻译角色」与「导入翻译字典」） ---------------- */
.translate-dropdown {
  position: relative;
  display: inline-block;
}
.translate-trigger.menu-open {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
}
.translate-menu {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 200;
  min-width: 220px;
  padding: 4px;
  background: rgba(255, 250, 243, 0.98);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  gap: 2px;
  animation: refresh-menu-in 0.12s ease-out;
}
.translate-menu-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  font-size: 12px;
  text-align: left;
  background: transparent;
  color: var(--ink);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
  font-family: inherit;
  font-weight: 600;
}
.translate-menu-item:hover:not(:disabled) {
  background: rgba(243, 223, 212, 0.6);
  color: var(--accent-deep);
}
.translate-menu-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.translate-menu-meta {
  font-size: 11px;
  color: var(--muted);
  font-weight: 500;
}
.translate-menu-item:hover:not(:disabled) .translate-menu-meta {
  color: var(--accent-deep);
}
.refresh-menu {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 200;
  min-width: 200px;
  padding: 4px;
  background: rgba(255, 250, 243, 0.98);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  gap: 2px;
  animation: refresh-menu-in 0.12s ease-out;
}
@keyframes refresh-menu-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
.refresh-menu-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  font-size: 12px;
  text-align: left;
  background: transparent;
  color: var(--ink);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
  font-family: inherit;
  font-weight: 600;
}
.refresh-menu-item:hover {
  background: rgba(243, 223, 212, 0.6);
  color: var(--accent-deep);
}
.refresh-menu-meta {
  font-size: 11px;
  color: var(--muted);
  font-weight: 500;
}
.refresh-menu-item:hover .refresh-menu-meta {
  color: var(--accent-deep);
}

/* ---------------- 刷新范围 Modal ---------------- */
.range-refresh-modal {
  width: 400px;
  max-width: 92vw;
  background: rgba(255, 250, 243, 0.98);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.range-refresh-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* ---------------- 收藏卡片 / chip 异色高亮 ---------------- */
.search-input-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.search-input-with-clear { padding-right: 28px; }
.search-clear-btn {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(74, 53, 25, 0.35);
  color: #fff;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.search-clear-btn:hover { background: rgba(157, 44, 44, 0.7); }

/* 单张图片收藏 ♥ 按钮，固定在缩略图右上角 */
.image-card { position: relative; }
.img-fav-toggle {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 3;
  width: 30px;
  height: 30px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s, transform 0.15s, color 0.2s;
  backdrop-filter: blur(4px);
}
.img-fav-toggle:hover { background: rgba(0, 0, 0, 0.65); transform: scale(1.08); }
.img-fav-toggle.active {
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  color: #fff;
  box-shadow: 0 2px 8px rgba(209, 40, 105, 0.5);
}

.img-select-toggle {
  position: absolute;
  bottom: 8px;
  left: 8px;
  z-index: 3;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 2px solid rgba(255, 255, 255, 0.85);
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  font-size: 15px;
  line-height: 1;
  font-weight: 700;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.18s, transform 0.12s, border-color 0.18s;
  backdrop-filter: blur(4px);
}
.thumb-wrap {
  position: relative;
  width: 100%;
  line-height: 0;
}
.img-select-toggle:hover { background: rgba(0, 0, 0, 0.7); transform: scale(1.08); }
.img-select-toggle.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  border-color: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
}
.image-card.is-selected {
  outline: 3px solid var(--accent);
  outline-offset: -1px;
}

.select-mode-btn.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  border: none;
  color: #fff;
}

.selection-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 25;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-radius: 999px;
  background: rgba(20, 16, 10, 0.88);
  color: #fff;
  box-shadow: 0 10px 32px rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(8px);
}
.selection-bar.inline-bar {
  position: relative;
  bottom: auto;
  left: auto;
  transform: none;
  z-index: 1;
  margin: 6px 0 4px;
  padding: 8px 14px;
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(243, 223, 212, 0.85), rgba(235, 208, 192, 0.85));
  color: var(--ink);
  border: 1px solid rgba(182, 84, 52, 0.2);
  box-shadow: 0 2px 10px rgba(74, 53, 25, 0.08);
  backdrop-filter: none;
  flex-wrap: wrap;
}
.selection-bar.inline-bar .selection-count {
  color: var(--ink);
  font-size: 13px;
}
.selection-bar.inline-bar .selection-count strong {
  color: var(--accent-deep);
  font-size: 15px;
  margin: 0 2px;
}
.selection-bar.inline-bar button {
  padding: 5px 12px;
  font-size: 12px;
}
.selection-bar.inline-bar .ghost {
  color: var(--muted);
  border: 1px solid rgba(74, 53, 25, 0.18);
  background: rgba(255, 255, 255, 0.5);
}
.selection-bar.inline-bar .ghost:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.85);
  color: var(--ink);
}
.selection-bar.inline-bar .secondary.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border: none;
}
.selection-count {
  font-size: 13px;
  color: #fff;
}
.selection-count strong {
  color: #ffd166;
  font-size: 16px;
  margin: 0 2px;
}
.selection-bar button {
  padding: 6px 14px;
  font-size: 13px;
}
.selection-bar .ghost {
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.25);
  background: transparent;
}
.selection-bar .ghost:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.1);
}

/* 页码列表选择器 */
.pg-picker-host {
  position: relative;
}
.pg-jump-btn.pg-jump-go.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border-color: transparent;
}
.pg-picker-panel {
  position: absolute;
  bottom: calc(100% + 8px);
  right: 0;
  z-index: 50;
  background: #fff;
  border: 1px solid rgba(74, 53, 25, 0.18);
  border-radius: 12px;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.18);
  padding: 10px 12px;
  min-width: 360px;
  max-height: 320px;
  display: flex;
  flex-direction: column;
}
.pg-picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}
.pg-picker-head .ghost {
  background: transparent;
  border: none;
  font-size: 16px;
  color: var(--muted);
  padding: 0 4px;
  cursor: pointer;
}
.pg-picker-grid {
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: 4px;
  overflow-y: auto;
  padding: 2px;
}
.pg-picker-cell {
  min-width: 30px;
  padding: 5px 0;
  font-size: 12px;
  font-weight: 600;
  border-radius: 6px;
  background: linear-gradient(135deg, #fbf4eb, #f2e8db);
  color: var(--ink);
  border: 1px solid rgba(74, 53, 25, 0.08);
  cursor: pointer;
  transition: background 0.15s, transform 0.1s;
}
.pg-picker-cell:hover {
  background: linear-gradient(135deg, #f3dfd4, #ebd0c0);
}
.pg-picker-cell.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  box-shadow: 0 2px 6px rgba(182, 84, 52, 0.25);
}

/* 已选清单弹窗 */
.selection-list-card {
  width: 720px;
  max-width: 92vw;
  max-height: 86vh;
  background: rgba(255, 255, 255, 0.97);
  border-radius: 14px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.selection-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.selection-list-body {
  flex: 1 1 auto;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-right: 4px;
}
.selection-list-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-deep);
  margin-bottom: 6px;
}
.selection-list-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
}
.selection-list-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  background: rgba(243, 223, 212, 0.4);
  border: 1px solid rgba(182, 84, 52, 0.14);
  border-radius: 10px;
}
.selection-list-thumb {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 6px;
  background: #eee;
  flex: 0 0 auto;
}
.selection-list-item-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1 1 auto;
  min-width: 0;
}
.selection-list-id {
  font-family: Consolas, monospace;
  font-size: 12px;
  color: var(--ink);
  font-weight: 600;
}
.selection-list-item-actions {
  display: flex;
  gap: 4px;
}
.selection-list-item-actions button {
  padding: 4px 8px;
  font-size: 11px;
}
.selection-list-other {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.selection-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px 4px 10px;
  border-radius: 999px;
  background: rgba(243, 223, 212, 0.5);
  border: 1px solid rgba(182, 84, 52, 0.18);
  font-family: Consolas, monospace;
  font-size: 12px;
  color: var(--ink);
}
.selection-chip button {
  background: transparent;
  border: none;
  color: var(--muted);
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
  cursor: pointer;
}
.selection-chip button:hover {
  color: #d12869;
}
.selection-list-foot {
  display: flex;
  justify-content: flex-end;
}

/* 加密工具弹窗 */
.crypto-tool-card {
  width: 640px;
  max-width: 92vw;
  max-height: 86vh;
  background: rgba(255, 255, 255, 0.97);
  border-radius: 14px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.crypto-tool-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.crypto-tool-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
}
.crypto-tool-label {
  font-weight: 600;
  font-size: 13px;
  color: var(--accent-deep);
}
.crypto-tool-mini {
  padding: 3px 10px;
  font-size: 11px;
  margin-left: auto;
}
.crypto-tool-row .crypto-tool-mini + .crypto-tool-mini {
  margin-left: 0;
}
.crypto-tool-textarea {
  width: 100%;
  font-family: Consolas, monospace;
  font-size: 12px;
  resize: vertical;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.6);
}
.crypto-tool-textarea[readonly] {
  background: rgba(243, 223, 212, 0.25);
}
.crypto-tool-actions {
  display: flex;
  gap: 8px;
  margin: 6px 0 2px;
  flex-wrap: wrap;
}
.crypto-tool-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}
.crypto-tool-foot > .muted {
  margin-right: auto;
}

/* 大图浏览器底部的「收藏」切换按钮 */
.viewer-fav-btn {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.25);
}
.viewer-fav-btn:hover:not(:disabled) { background: rgba(255, 255, 255, 0.22); }
.viewer-fav-btn.active {
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  border-color: transparent;
  color: #fff;
  box-shadow: 0 2px 8px rgba(209, 40, 105, 0.5);
}


.image-card.is-favorited {
  background: linear-gradient(135deg, rgba(255, 222, 173, 0.35), rgba(255, 188, 86, 0.18));
  border: 3px solid #d48f2f;
  box-shadow: 0 0 0 1px rgba(212, 143, 47, 0.25), 0 6px 22px rgba(212, 143, 47, 0.4);
  position: relative;
}
.image-card.is-favorited::before {
  content: '★ 收藏';
  position: absolute;
  top: 32px;
  left: 6px;
  z-index: 2;
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, #d48f2f, #b46e16);
  border-radius: 999px;
  box-shadow: 0 2px 6px rgba(180, 110, 22, 0.45);
  letter-spacing: 0.5px;
  pointer-events: none;
}
/* 图片直接收藏比作者/角色命中优先级更高：粉红色厚边框 + 阴影更强 */
.image-card.is-img-favorited {
  background: linear-gradient(135deg, rgba(255, 192, 213, 0.4), rgba(209, 40, 105, 0.18));
  border: 3px solid #d12869;
  box-shadow: 0 0 0 1px rgba(209, 40, 105, 0.3), 0 6px 22px rgba(209, 40, 105, 0.45);
}
.image-card.is-img-favorited::before {
  content: '';
  display: none;
}
.token-chip.is-favorited-chip {
  background: linear-gradient(135deg, rgba(255, 188, 86, 0.45), rgba(212, 143, 47, 0.3));
  color: #6b3f0a;
  border: 1px solid rgba(212, 143, 47, 0.55);
  font-weight: 700;
}
.token-chip.is-favorited-chip:hover {
  background: linear-gradient(135deg, rgba(255, 188, 86, 0.65), rgba(212, 143, 47, 0.45));
}

/* SFW 开关按钮 */
.safe-mode-btn {
  padding: 4px 12px;
  border-radius: 999px;
  font-weight: 600;
  font-size: 12px;
  border: 1px solid transparent;
  white-space: nowrap;
  transition: background 0.18s, color 0.18s, border-color 0.18s;
}
.safe-mode-btn.is-safe {
  background: rgba(77, 145, 90, 0.15);
  color: #2d7a3e;
  border-color: rgba(77, 145, 90, 0.4);
}
.safe-mode-btn.is-safe:hover {
  background: rgba(77, 145, 90, 0.25);
}
.safe-mode-btn.is-unsafe {
  background: rgba(209, 86, 40, 0.18);
  color: #b04420;
  border-color: rgba(209, 86, 40, 0.45);
}
.safe-mode-btn.is-unsafe:hover {
  background: rgba(209, 86, 40, 0.3);
}
</style>
