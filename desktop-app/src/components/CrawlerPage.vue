<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue';
import GalleryCalendar from './GalleryCalendar.vue';

const emit = defineEmits(['edit-image']);

const LOCAL_PAGE_SIZE = 25;
const LATEST_PAGE_SIZE = 25;

const savedHabitsStr = localStorage.getItem('crawlerHabits') || '{}';
const habits = JSON.parse(savedHabitsStr);
const savedSearchFavoritesStr = localStorage.getItem('crawlerSearchFavorites') || '[]';
const searchFavorites = ref(JSON.parse(savedSearchFavoritesStr));

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

watch(searchFavorites, (value) => {
  localStorage.setItem('crawlerSearchFavorites', JSON.stringify(value || []));
}, { deep: true });

const gallery = ref({
  selectedDate: '',
  availableDates: [],
  today: '',
  images: [],
  search: '',
  page: 1
});

const task = ref({
  isRunning: false,
  isPaused: false,
  logs: ['桌面端已启动。'],
  backendError: '',
  backendTail: [],
  showLogs: false
});
const viewer = ref({
  open: false,
  index: 0,
  imageUrl: '',
  zoom: 1
});

const toast = ref({
  show: false,
  msg: '',
  type: 'info'
});

const translationEditor = ref({
  open: false,
  loading: false,
  saving: false,
  originalKey: '',
  key: '',
  chineseName: '',
  sourceHint: '',
  sourceHintZh: ''
});

const tagContextMenu = ref({
  open: false,
  x: 0,
  y: 0,
  entry: null
});

function showToast(msg, type = 'info') {
  toast.value = { show: true, msg, type };
  setTimeout(() => { toast.value.show = false; }, 3000);
}

const loadingGallery = ref(false);
let pollTimer = null;
const logBodyRef = ref(null);
const thumbUrlCache = new Map();
let pendingGalleryReload = false;
let lastGalleryReloadAt = 0;
const GALLERY_RELOAD_COOLDOWN_MS = 4000;
const IDLE_POLL_MS = 10000;
const ACTIVE_POLL_MS = 2500;

watch(() => task.value.logs.length, async () => {
  if (task.value.showLogs && logBodyRef.value) {
    await nextTick();
    logBodyRef.value.scrollTop = logBodyRef.value.scrollHeight;
  }
});

const filteredLocalImages = computed(() => {
  const keyword = normalizeSearchText(gallery.value.search);
  const source = gallery.value.images;
  if (!keyword) return source;
  return source.filter(item => String(item.searchIndex || '').includes(keyword));
});

const localTotalPages = computed(() => Math.max(1, Math.ceil(filteredLocalImages.value.length / LOCAL_PAGE_SIZE)));

const pagedLocalImages = computed(() => {
  const page = Math.min(gallery.value.page, localTotalPages.value);
  const start = (page - 1) * LOCAL_PAGE_SIZE;
  return filteredLocalImages.value.slice(start, start + LOCAL_PAGE_SIZE);
});

const activeItems = computed(() => pagedLocalImages.value);
const activeCount = computed(() => filteredLocalImages.value.length);
const activeTotalPages = computed(() => localTotalPages.value);
const viewerItems = computed(() => filteredLocalImages.value);
const viewerItem = computed(() => viewerItems.value[viewer.value.index] || null);
const activePage = computed({
  get() {
    return gallery.value.page;
  },
  set(value) {
    gallery.value.page = value;
  }
});

function appendLog(message) {
  if (!message) return;
  task.value.logs.push(message);
  task.value.logs = task.value.logs.slice(-320);
}

function splitTags(value) {
  return String(value || '').split(' ').map(item => item.trim()).filter(Boolean);
}

function normalizeSearchText(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[_-]+/g, ' ')
    .replace(/[()]/g, ' ')
    .replace(/\s+/g, ' ');
}

function buildSearchIndex(item, characterEntries) {
  const parts = [
    item.artist,
    item.filename
  ];

  characterEntries.forEach(entry => {
    parts.push(
      entry.name,
      entry.key,
      entry.matched_key,
      entry.source_hint,
      entry.source_hint_zh
    );
  });

  return normalizeSearchText(parts.filter(Boolean).join(' '));
}

function getCharacterEntries(item) {
  if (Array.isArray(item.character_entries)) return item.character_entries;
  if (Array.isArray(item.characterEntries)) return item.characterEntries;
  if (Array.isArray(item.characters)) {
    return item.characters.map(name => ({
      key: '',
      matched_key: '',
      name: String(name || ''),
      source_hint: '',
      source_hint_zh: ''
    }));
  }
  return [];
}

function normalizeGalleryItem(item) {
  const characterEntries = getCharacterEntries(item);
  return {
    ...item,
    thumbUrl: item.thumbUrl || '',
    artistTokens: splitTags(item.artist),
    characterEntries,
    characterTokens: characterEntries.length
      ? characterEntries.map(entry => entry?.name).filter(Boolean)
      : (Array.isArray(item.characters) ? item.characters : splitTags(item.characters)),
    searchIndex: buildSearchIndex(item, characterEntries)
  };
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

async function hydrateThumbs(items) {
  await Promise.all(items.map(async item => {
    if (item.thumbUrl) return;
    
    const webUrl = item.web_url || item.webUrl;
    if (webUrl) {
      item.thumbUrl = `http://127.0.0.1:8000${webUrl}`;
      return;
    }

    if (!item.localPath) return;
    if (thumbUrlCache.has(item.localPath)) {
      item.thumbUrl = thumbUrlCache.get(item.localPath);
      return;
    }
    const dataUrl = await window.desktopAPI.file.readDataUrl(item.localPath);
    if (!dataUrl) return;
    thumbUrlCache.set(item.localPath, dataUrl);
    item.thumbUrl = dataUrl;
  }));
}

async function loadGallery(date) {
  if (loadingGallery.value) return;
  loadingGallery.value = true;
  try {
    const data = await window.desktopAPI.gallery.getByDate(date || gallery.value.selectedDate);
    const normalizedImages = data.images.map(normalizeGalleryItem);
    gallery.value.selectedDate = data.selectedDate;
    gallery.value.availableDates = data.availableDates;
    gallery.value.today = data.today;
    gallery.value.images = normalizedImages;
    gallery.value.page = 1;
    lastGalleryReloadAt = Date.now();
    await hydrateThumbs(pagedLocalImages.value);
  } finally {
    loadingGallery.value = false;
  }
}

async function requestGalleryReload(date) {
  const now = Date.now();
  if (loadingGallery.value || now - lastGalleryReloadAt < GALLERY_RELOAD_COOLDOWN_MS) {
    pendingGalleryReload = true;
    return;
  }
  pendingGalleryReload = false;
  await loadGallery(date);
}

function getPollDelay() {
  return task.value.isRunning ? ACTIVE_POLL_MS : IDLE_POLL_MS;
}

function scheduleNextStatusPoll(delay = getPollDelay()) {
  if (pollTimer) window.clearTimeout(pollTimer);
  pollTimer = window.setTimeout(async () => {
    await syncStatus();
    scheduleNextStatusPoll();
  }, delay);
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
        await requestGalleryReload(gallery.value.today);
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
    if (pendingGalleryReload && !loadingGallery.value && Date.now() - lastGalleryReloadAt >= GALLERY_RELOAD_COOLDOWN_MS) {
      await requestGalleryReload(gallery.value.selectedDate);
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
  closeTagContextMenu();
}

function openTagContextMenu(event, entry) {
  event.preventDefault();
  tagContextMenu.value = {
    open: true,
    x: event.clientX,
    y: event.clientY,
    entry
  };
}

function closeTagContextMenu() {
  if (!tagContextMenu.value.open) return;
  tagContextMenu.value.open = false;
}

async function editFromContextMenu() {
  const entry = tagContextMenu.value.entry;
  closeTagContextMenu();
  if (!entry) return;
  await openTranslationEditor(entry);
}

function saveCurrentSearch() {
  const keyword = gallery.value.search.trim();
  if (!keyword) return;
  if (searchFavorites.value.includes(keyword)) return;
  searchFavorites.value = [keyword, ...searchFavorites.value].slice(0, 16);
  showToast('已收藏搜索词', 'success');
}

function removeSearchFavorite(keyword) {
  searchFavorites.value = searchFavorites.value.filter(item => item !== keyword);
}

async function openTranslationEditor(entry) {
  const rawKey = entry?.key || entry?.matched_key || entry?.name || '';
  if (!rawKey) return;
  translationEditor.value.open = true;
  translationEditor.value.loading = true;
  translationEditor.value.originalKey = rawKey;
  translationEditor.value.key = rawKey;
  translationEditor.value.chineseName = entry?.name || '';
  translationEditor.value.sourceHint = entry?.source_hint || '';
  translationEditor.value.sourceHintZh = entry?.source_hint_zh || '';

  try {
    const res = await fetch(`http://127.0.0.1:8000/api/translation_entry/${encodeURIComponent(rawKey)}`);
    const json = await res.json();
    if (!json.ok) throw new Error(json.msg || '读取失败');
    const loaded = json.entry || {};
    translationEditor.value.key = loaded.matched_key || rawKey;
    translationEditor.value.chineseName = loaded.chinese_name || entry?.name || '';
    translationEditor.value.sourceHint = loaded.source_hint || '';
    translationEditor.value.sourceHintZh = loaded.source_hint_zh || '';
  } catch (error) {
    showToast(`读取翻译条目失败: ${error.message}`, 'error');
  } finally {
    translationEditor.value.loading = false;
  }
}

function closeTranslationEditor() {
  translationEditor.value.open = false;
}

async function saveTranslationEditor() {
  const payload = {
    key: translationEditor.value.key.trim(),
    chinese_name: translationEditor.value.chineseName.trim(),
    source_hint: translationEditor.value.sourceHint.trim(),
    source_hint_zh: translationEditor.value.sourceHintZh.trim()
  };
  if (!payload.key) {
    showToast('角色键不能为空', 'error');
    return;
  }

  translationEditor.value.saving = true;
  try {
    const res = await fetch('http://127.0.0.1:8000/api/translation_entry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.msg || '保存失败');
    showToast('翻译条目已保存', 'success');
    closeTranslationEditor();
    await loadGallery(gallery.value.selectedDate);
  } catch (error) {
    showToast(`保存失败: ${error.message}`, 'error');
  } finally {
    translationEditor.value.saving = false;
  }
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
  
  const webUrl = item?.web_url || item?.webUrl;
  if (webUrl) {
    viewer.value.imageUrl = `http://127.0.0.1:8000${webUrl}`;
    return;
  }
  
  if (item?.localPath) {
    if (thumbUrlCache.has(item.localPath)) {
      viewer.value.imageUrl = thumbUrlCache.get(item.localPath);
    } else {
      const dataUrl = await window.desktopAPI.file.readDataUrl(item.localPath);
      if (!dataUrl) return;
      thumbUrlCache.set(item.localPath, dataUrl);
      viewer.value.imageUrl = dataUrl;
    }
  }
}

async function syncViewerImage() {
  viewer.value.zoom = 1;
  viewer.value.imageUrl = '';
  if (!viewerItem.value) return;

  const webUrl = viewerItem.value.web_url || viewerItem.value.webUrl;
  if (webUrl) {
    viewer.value.imageUrl = `http://127.0.0.1:8000${webUrl}`;
    return;
  }

  if (!viewerItem.value.localPath) return;
  if (thumbUrlCache.has(viewerItem.value.localPath)) {
    viewer.value.imageUrl = thumbUrlCache.get(viewerItem.value.localPath);
  } else {
    const dataUrl = await window.desktopAPI.file.readDataUrl(viewerItem.value.localPath);
    if (!dataUrl) return;
    thumbUrlCache.set(viewerItem.value.localPath, dataUrl);
    viewer.value.imageUrl = dataUrl;
  }
}

function closeViewer() {
  viewer.value.open = false;
  viewer.value.imageUrl = '';
  viewer.value.zoom = 1;
}

function getLogType(line) {
  if (!line) return 'log-info';
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
}

function onViewerWheel(event) {
  if (!event.ctrlKey) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.1 : 0.9;
  viewer.value.zoom = Math.min(8, Math.max(0.2, viewer.value.zoom * factor));
}

async function onKeyDown(event) {
  if (tagContextMenu.value.open && event.key === 'Escape') {
    closeTagContextMenu();
    return;
  }
  if (!viewer.value.open) return;
  if (event.key === 'Escape') {
    closeViewer();
  } else if (event.key === 'ArrowLeft') {
    await stepViewer(-1);
  } else if (event.key === 'ArrowRight') {
    await stepViewer(1);
  }
}

watch(gallery.search, () => {
  gallery.value.page = 1;
});

watch(localTotalPages, total => {
  if (gallery.value.page > total) gallery.value.page = total;
});

watch(pagedLocalImages, async items => {
  await hydrateThumbs(items);
});

onMounted(async () => {
  await ensureService();
  await loadGallery();
  await syncStatus();
  scheduleNextStatusPoll();
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('click', closeTagContextMenu);
});

onBeforeUnmount(() => {
  if (pollTimer) window.clearTimeout(pollTimer);
  window.removeEventListener('keydown', onKeyDown);
  window.removeEventListener('click', closeTagContextMenu);
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

      <p v-if="task.backendError" class="error-text">{{ task.backendError }}</p>

      <div class="modern-log-wrapper" :class="{ 'is-expanded': task.showLogs }">
        <div class="modern-log-header" @click="task.showLogs = !task.showLogs">
          <div class="log-header-left">
            <span class="status-dot" :class="{ 'is-active': task.isRunning }"></span>
            <span class="log-title">运行动态</span>
          </div>
          <div class="log-header-right">
            <span class="log-count" v-if="task.logs.length">{{ task.logs.length }} 条记录</span>
            <svg class="chevron" :class="{ 'is-rotated': task.showLogs }" viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </div>
        </div>
        <transition name="log-expand">
          <div v-if="task.showLogs" class="modern-log-body" ref="logBodyRef">
            <div class="modern-log-line" v-for="(log, i) in task.logs" :key="i" :class="getLogType(log)">
              <span class="log-icon">{{ getLogIcon(log) }}</span>
              <span class="log-text">{{ log }}</span>
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
            共 {{ activeCount }} 张，当前日期 {{ gallery.selectedDate || '未选择' }}
          </p>
        </div>
        <div class="gallery-tools">
          <div class="search-row">
            <input v-model="gallery.search" class="search-input" type="text" placeholder="搜索角色 / 作品来源 / 来源翻译" />
            <button class="secondary" @click="saveCurrentSearch" style="white-space: nowrap; font-size: 12px; padding: 6px 12px;">收藏关键词</button>
          </div>
          <div v-if="searchFavorites.length" class="token-row favorite-row">
            <template v-for="keyword in searchFavorites" :key="keyword">
              <button class="meta-link favorite-chip" @click="applySearch(keyword)">{{ keyword }}</button>
              <button class="meta-link remove-chip" @click="removeSearchFavorite(keyword)">删除 {{ keyword }}</button>
            </template>
          </div>
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
              <template v-for="entry in item.characterEntries" :key="`character-${entry.key || entry.name}`">
                <button class="meta-link token-chip" @click="applySearch(entry.name)" @contextmenu="openTagContextMenu($event, entry)">
                  {{ entry.name }}
                </button>
                <button
                  v-if="entry.source_hint || entry.source_hint_zh"
                  class="meta-link source-chip"
                  @click="applySearch(entry.source_hint_zh || entry.source_hint)"
                  @contextmenu="openTagContextMenu($event, entry)"
                >
                  {{ entry.source_hint_zh || entry.source_hint }}
                </button>
              </template>
              <button
                v-for="token in (!item.characterEntries?.length ? item.characterTokens : [])"
                :key="`character-${token}`"
                class="meta-link token-chip"
                @click="applySearch(token)"
              >
                {{ token }}
              </button>
              <span v-if="!item.characterTokens?.length" class="muted compact-text">无角色标签</span>
            </div>
          </div>
          <div class="button-row compact">
            <button class="secondary" @click="openOriginal(item)" :disabled="!item.postUrl">原帖</button>
            <button class="secondary" @click="openLocal(item)" :disabled="!item.localPath">打开本地</button>
            <button @click="editItem(item)">编辑打码</button>
          </div>
        </article>
      </div>

      <div class="pagination-bar" v-if="activeCount">
        <button class="secondary" @click="activePage -= 1" :disabled="activePage <= 1">上一页</button>
        <span>第 {{ activePage }} / {{ activeTotalPages }} 页</span>
        <button class="secondary" @click="activePage += 1" :disabled="activePage >= activeTotalPages">下一页</button>
      </div>
    </section>

    <div v-if="viewer.open" class="viewer-overlay" @click.self="closeViewer">
      <div class="viewer-toolbar">
        <div>
          <strong>{{ viewerItem?.artist || '未知' }}</strong>
          <span class="muted compact-text" style="color: #ccc;">第 {{ viewer.index + 1 }} / {{ viewerItems.length }} 张</span>
        </div>
        <div class="button-row compact viewer-actions">
          <button class="secondary" @click="stepViewer(-1)" :disabled="viewer.index <= 0">上一张</button>
          <button class="secondary" @click="stepViewer(1)" :disabled="viewer.index >= viewerItems.length - 1">下一张</button>
          <button @click="editItem(viewerItem)" style="background: linear-gradient(135deg, var(--accent), var(--accent-deep)); border: none; color: white;">编辑图片</button>
          <button class="ghost" @click="closeViewer" style="color: #fff; border: 1px solid rgba(255,255,255,0.2);">关闭</button>
        </div>
      </div>
      <div class="viewer-stage" @wheel="onViewerWheel" @click.self="closeViewer">
        <div class="viewer-image-wrap" :style="{ zoom: viewer.zoom }">
          <img
            v-if="viewer.imageUrl"
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

    <div
      v-if="tagContextMenu.open"
      class="tag-context-menu"
      :style="{ left: `${tagContextMenu.x}px`, top: `${tagContextMenu.y}px` }"
    >
      <button class="tag-context-item" @click="editFromContextMenu">编辑翻译</button>
    </div>

    <div v-if="translationEditor.open" class="viewer-overlay" @click.self="closeTranslationEditor" style="z-index: 10001; display: flex; justify-content: center; align-items: center;">
      <div class="card panel translation-modal">
        <h3 style="margin: 0;">编辑角色翻译</h3>
        <label class="field-full">
          <span>角色键</span>
          <input v-model="translationEditor.key" type="text" :disabled="translationEditor.loading || translationEditor.saving" />
        </label>
        <label class="field-full">
          <span>角色中文名</span>
          <input v-model="translationEditor.chineseName" type="text" :disabled="translationEditor.loading || translationEditor.saving" />
        </label>
        <label class="field-full">
          <span>来源标识</span>
          <input v-model="translationEditor.sourceHint" type="text" :disabled="translationEditor.loading || translationEditor.saving" />
        </label>
        <label class="field-full">
          <span>来源翻译</span>
          <input v-model="translationEditor.sourceHintZh" type="text" :disabled="translationEditor.loading || translationEditor.saving" />
        </label>
        <div class="button-row">
          <button @click="saveTranslationEditor" :disabled="translationEditor.loading || translationEditor.saving">保存</button>
          <button class="secondary" @click="closeTranslationEditor" :disabled="translationEditor.saving">关闭</button>
        </div>
      </div>
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
  /* Use a reasonable max height so it fits the viewport */
  max-height: calc(100vh - 280px);
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

.log-count {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.7);
  background: rgba(0, 0, 0, 0.3);
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.1);
}

.chevron {
  color: rgba(255, 255, 255, 0.5);
  transition: transform 0.3s ease;
}
.chevron.is-rotated {
  transform: rotate(180deg);
}

.modern-log-body {
  padding: 0 16px 16px 16px;
  overflow-y: auto;
  /* Allow it to flex but bound the height */
  flex: 1 1 auto;
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
}
.modern-log-line:last-child {
  border-bottom: none;
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
.translation-modal {
  width: min(520px, 92vw);
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: rgba(255, 250, 243, 0.98);
}

@keyframes fadeInDown {
  from { opacity: 0; transform: translate(-50%, -15px); }
  to { opacity: 1; transform: translate(-50%, 0); }
}
</style>
