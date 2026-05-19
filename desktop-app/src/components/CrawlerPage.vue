<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue';
import GalleryCalendar from './GalleryCalendar.vue';

const emit = defineEmits(['edit-image']);

const LATEST_PAGE_SIZE = 15;

const savedHabitsStr = localStorage.getItem('crawlerHabits') || '{}';
const habits = JSON.parse(savedHabitsStr);

const form = ref({
  startPage: habits.rank_start || 1,
  endPage: habits.rank_end || 16,
  tags: habits.tags || 'furry, futanari',
  mode: habits.mode || 'rank',
  targetDate: '',
  startDate: '',
  endDate: ''
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
  pageSize: habits.pageSize || 30,
  page: 1
});

watch(() => [gallery.value.sortBy, gallery.value.pageSize, gallery.value.hotThreshold], () => {
  habits.sortBy = gallery.value.sortBy;
  habits.pageSize = gallery.value.pageSize;
  habits.hotThreshold = gallery.value.hotThreshold;
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
  zoom: 1
});

const refresh = ref({
  isRunning: false,
  done: 0,
  total: 0,
  dateStr: ''
});

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
  if (sortBy === 'score') {
    result = [...result].sort((a, b) => (b.score || 0) - (a.score || 0));
  } else if (sortBy === 'fav') {
    result = [...result].sort((a, b) => (b.favCount || 0) - (a.favCount || 0));
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
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const out = [1];
  if (cur > 3) out.push('…');
  const start = Math.max(2, cur - 1);
  const end = Math.min(total - 1, cur + 1);
  for (let i = start; i <= end; i++) out.push(i);
  if (cur < total - 2) out.push('…');
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
    const result = await window.desktopAPI.crawler.start({
      start_page: Number(form.value.startPage) || 1,
      end_page: Number(form.value.endPage) || 1,
      tags: form.value.tags || '',
      mode: form.value.mode || 'rank',
      target_date: form.value.targetDate || '',
      start_date: form.value.startDate || '',
      end_date: form.value.endDate || ''
    });
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

function extractPostId(postUrl) {
  if (!postUrl) return '';
  const tail = String(postUrl).replace(/\/$/, '').split('/').pop();
  return /^\d+$/.test(tail) ? tail : '';
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

async function stopRefreshScores() {
  // 现在使用同步的 /api/refresh_visible，没有后台线程可停 —— 保留按钮但只做兜底
  refresh.value.isRunning = false;
  try {
    await fetch('http://127.0.0.1:8000/api/refresh_scores_stop', { method: 'POST' });
  } catch (_) { /* noop */ }
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
watch(() => gallery.value.sortBy, () => { gallery.value.page = 1; });
watch(() => gallery.value.hotOnly, () => { gallery.value.page = 1; });
watch(() => gallery.value.pageSize, () => { gallery.value.page = 1; });

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
  // 静默加载：Python 后端就绪后在后台刷新翻译，不再显示“正在读取”遮罩，消除闪烁
  await loadGallery(gallery.value.selectedDate, true);
  await syncStatus();
  pollTimer = window.setInterval(syncStatus, 1200);
  window.addEventListener('keydown', onKeyDown);
});

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer);
  window.removeEventListener('keydown', onKeyDown);
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
        <button class="ghost" @click="openHostsHint" style="margin-left: auto; color: #ff9800;">无法连接？修改Hosts教程</button>
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
        <div>
          <h2>本地已下载</h2>
          <p class="inline-note">
            共 {{ galleryStats.total }} 张<span v-if="galleryStats.filtered !== galleryStats.total"> · 已筛选 {{ galleryStats.filtered }} 张</span><span v-if="galleryStats.avg > 0"> · 平均 ★ {{ galleryStats.avg }} · 中位 ★ {{ galleryStats.median }}</span> · {{ gallery.selectedDate || '未选择' }}
          </p>
        </div>
        <div class="gallery-tools">
          <select v-model="gallery.sortBy" class="search-input" style="width: auto;" title="排序方式">
            <option value="default">默认抓取顺序</option>
            <option value="score">按 score 排序</option>
            <option value="fav">按收藏数排序</option>
          </select>
          <select v-model="gallery.filterFormat" class="search-input" style="width: auto;">
            <option value="all">全部格式</option>
            <option value="image">图片</option>
            <option value="video">视频</option>
            <option value="zip">动图ZIP</option>
          </select>
          <select v-model.number="gallery.pageSize" class="search-input" style="width: auto;" title="每页数量">
            <option :value="15">15 / 页</option>
            <option :value="30">30 / 页</option>
            <option :value="60">60 / 页</option>
            <option :value="120">120 / 页</option>
          </select>
          <button
            :class="['hot-toggle', { active: gallery.hotOnly }]"
            @click="gallery.hotOnly = !gallery.hotOnly"
            :title="`只看 score ≥ ${gallery.hotThreshold}`"
          >🔥 只看高分</button>
          <button
            :class="['refresh-btn', { active: refresh.isRunning }]"
            @click="refresh.isRunning ? stopRefreshScores() : startRefreshScores()"
            :disabled="!gallery.selectedDate"
            :title="refresh.isRunning ? '点击停止刷新' : '刷新当前页可见图片的 score / 收藏数 / 画师（对缺失热度的图会反查补全）'"
          >
            <span v-if="!refresh.isRunning">🔄 刷新本页</span>
            <span v-else>⏸ {{ refresh.done }}/{{ refresh.total }}</span>
          </button>
          <input v-model="gallery.search" class="search-input" type="text" placeholder="搜索作者 / 角色" />
          <button class="secondary" @click="importTranslationFile" style="white-space: nowrap; font-size: 12px; padding: 6px 12px;">导入翻译字典</button>
          <input type="file" ref="translationFileInput" style="display: none" accept=".json" @change="onTranslationFileSelected" />
        </div>
      </div>

      <GalleryCalendar
        :available-dates="gallery.availableDates"
        :selected-date="gallery.selectedDate"
        :today="gallery.today"
        @select="loadGallery"
      />

      <div v-if="loadingGallery" class="gallery-empty">正在读取图库...</div>
      <div v-else-if="!activeItems.length" class="gallery-empty">当前日期没有图片</div>
      <div v-else class="gallery-grid">
        <article v-for="item in activeItems" :key="item.localPath || item.filename" class="image-card">
          <img class="thumb clickable-thumb" :src="item.thumbUrl" :alt="item.filename" loading="lazy" decoding="async" @click="openViewer(item)" />
          <div v-if="(item.score || 0) > 0 || (item.favCount || 0) > 0" class="score-badge">
            <span><span class="score-star">★</span> {{ item.score || 0 }}</span>
            <span><span class="score-heart">♥</span> {{ item.favCount || 0 }}</span>
          </div>
          <div class="card-meta">
            <div class="token-row">
              <button
                v-for="token in (item.artistTokens?.length ? item.artistTokens : ['未知'])"
                :key="`artist-${token}`"
                class="meta-link author-link token-chip"
                @click="applySearch(token)"
              >
                {{ token }}
              </button>
            </div>
            <div class="token-row">
              <button
                v-for="token in item.characterTokens"
                :key="`character-${token}`"
                class="meta-link token-chip"
                @click="applySearch(token)"
              >
                {{ token.includes(' [') ? token.split(' [')[0] : token }}
              </button>
              <span v-if="!item.characterTokens?.length" class="muted compact-text">无角色标签</span>
            </div>
          </div>
          <div class="button-row compact">
            <button class="secondary" @click="openOriginal(item)" :disabled="!item.postUrl">原帖</button>
            <button class="secondary" @click="openLocal(item)" :disabled="!item.localPath">打开本地</button>
            <button @click="editItem(item)">编辑打码</button>
            <button v-if="item.filename?.toLowerCase().endsWith('.zip')" class="secondary" @click="convertGif(item)" style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;">转GIF</button>
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
        <span class="pg-jump">
          跳转
          <input type="number" min="1" :max="activeTotalPages" v-model.number="jumpInput" @keyup.enter="doJump" />
          / {{ activeTotalPages }}
        </span>
      </div>
    </section>

    <div v-if="viewer.open" class="viewer-overlay" @click.self="closeViewer">
      <div class="viewer-toolbar">
        <div class="viewer-toolbar-info">
          <strong>{{ viewerItem?.artist || '未知' }}</strong>
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
        <div class="button-row compact viewer-actions">
          <button class="secondary" @click="stepViewer(-1)" :disabled="viewer.index <= 0">上一张</button>
          <button class="secondary" @click="stepViewer(1)" :disabled="viewer.index >= viewerItems.length - 1">下一张</button>
          <button v-if="viewerItem?.filename?.toLowerCase().endsWith('.zip')" class="secondary" @click="convertGif(viewerItem)" style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;">转GIF</button>
          <button @click="editItem(viewerItem)" style="background: linear-gradient(135deg, var(--accent), var(--accent-deep)); border: none; color: white;">编辑图片</button>
          <button class="ghost" @click="closeViewer" style="color: #fff; border: 1px solid rgba(255,255,255,0.2);">关闭</button>
        </div>
      </div>
      <div class="viewer-stage" @wheel="onViewerWheel" @click.self="closeViewer">
        <div class="viewer-image-wrap" :style="{ zoom: viewer.zoom }">
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
        <textarea readonly style="width: 100%; height: 60px; font-family: Consolas, monospace; font-size: 13px; resize: none; background: rgba(0,0,0,0.03); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: 10px; outline: none; cursor: text;" onfocus="this.select()">104.26.11.39 danbooru.donmai.us</textarea>
        <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 10px;">
          <button @click="openHostsFolder" class="secondary">打开目录</button>
          <button @click="hostsModal.open = false" style="min-width: 80px;">确定</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 控制面板设为 relative，给日志面板的「放大」态做绝对定位锚点 */
.control-panel {
  position: relative;
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
</style>
