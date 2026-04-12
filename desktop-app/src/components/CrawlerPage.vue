<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import GalleryCalendar from './GalleryCalendar.vue';

const emit = defineEmits(['edit-image']);

const LOCAL_PAGE_SIZE = 24;
const LATEST_PAGE_SIZE = 18;

const form = ref({
  startPage: 1,
  endPage: 16,
  tags: 'furry, futanari'
});

const gallery = ref({
  tab: 'latest',
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
  latestImages: [],
  latestPage: 1,
  backendError: '',
  backendTail: []
});

const loadingGallery = ref(false);
let pollTimer = null;

const filteredLocalImages = computed(() => {
  const keyword = gallery.value.search.trim().toLowerCase();
  const source = gallery.value.images;
  if (!keyword) return source;
  return source.filter(item =>
    (item.artist || '').toLowerCase().includes(keyword) ||
    (item.characters || '').toLowerCase().includes(keyword) ||
    (item.filename || '').toLowerCase().includes(keyword)
  );
});

const filteredLatestImages = computed(() => {
  const keyword = gallery.value.search.trim().toLowerCase();
  const source = task.value.latestImages;
  if (!keyword) return source;
  return source.filter(item =>
    (item.artist || '').toLowerCase().includes(keyword) ||
    (item.characters || '').toLowerCase().includes(keyword) ||
    (item.filename || '').toLowerCase().includes(keyword)
  );
});

const localTotalPages = computed(() => Math.max(1, Math.ceil(filteredLocalImages.value.length / LOCAL_PAGE_SIZE)));
const latestTotalPages = computed(() => Math.max(1, Math.ceil(filteredLatestImages.value.length / LATEST_PAGE_SIZE)));

const pagedLocalImages = computed(() => {
  const page = Math.min(gallery.value.page, localTotalPages.value);
  const start = (page - 1) * LOCAL_PAGE_SIZE;
  return filteredLocalImages.value.slice(start, start + LOCAL_PAGE_SIZE);
});

const pagedLatestImages = computed(() => {
  const page = Math.min(task.value.latestPage, latestTotalPages.value);
  const start = (page - 1) * LATEST_PAGE_SIZE;
  return filteredLatestImages.value.slice(start, start + LATEST_PAGE_SIZE);
});

const activeItems = computed(() => gallery.value.tab === 'local' ? pagedLocalImages.value : pagedLatestImages.value);
const activeCount = computed(() => gallery.value.tab === 'local' ? filteredLocalImages.value.length : filteredLatestImages.value.length);
const activeTotalPages = computed(() => gallery.value.tab === 'local' ? localTotalPages.value : latestTotalPages.value);
const activePage = computed({
  get() {
    return gallery.value.tab === 'local' ? gallery.value.page : task.value.latestPage;
  },
  set(value) {
    if (gallery.value.tab === 'local') gallery.value.page = value;
    else task.value.latestPage = value;
  }
});

function appendLog(message) {
  if (!message) return;
  task.value.logs.push(message);
  task.value.logs = task.value.logs.slice(-320);
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
    if (item.thumbUrl || !item.localPath) return;
    item.thumbUrl = await window.desktopAPI.file.readDataUrl(item.localPath);
  }));
}

async function loadGallery(date) {
  loadingGallery.value = true;
  try {
    const data = await window.desktopAPI.gallery.getByDate(date || gallery.value.selectedDate);
    const normalizedImages = data.images.map(item => ({
      ...item,
      thumbUrl: '',
      displayCharacters: (item.characters || '').split(' ').filter(Boolean).join(', ')
    }));
    gallery.value.selectedDate = data.selectedDate;
    gallery.value.availableDates = data.availableDates;
    gallery.value.today = data.today;
    gallery.value.images = normalizedImages;
    if (!task.value.latestImages.length && data.selectedDate === data.today) {
      task.value.latestImages = normalizedImages.map(item => ({ ...item }));
    }
    gallery.value.page = 1;
    await hydrateThumbs(pagedLocalImages.value);
  } finally {
    loadingGallery.value = false;
  }
}

function mergeLatestImages(newItems) {
  const incoming = newItems.map(item => ({
    ...item,
    localPath: item.local_path || item.localPath || '',
    postUrl: item.post_url || item.postUrl || '',
    characters: item.tags?.tag_string_character || item.characters || '',
    thumbUrl: '',
    displayCharacters: (item.tags?.tag_string_character || item.characters || '').split(' ').filter(Boolean).join(', ')
  }));
  const seen = new Set(task.value.latestImages.map(item => item.localPath || item.web_url || item.filename));
  for (const item of incoming) {
    const key = item.localPath || item.web_url || item.filename;
    if (seen.has(key)) continue;
    seen.add(key);
    task.value.latestImages.unshift(item);
  }
  task.value.latestImages = task.value.latestImages.slice(0, 120);
  task.value.latestPage = 1;
}

async function syncStatus() {
  try {
    const status = await window.desktopAPI.crawler.status();
    task.value.isRunning = !!status.is_running;
    task.value.isPaused = !!status.is_paused;
    task.value.backendError = status.backendError || '';

    mergeBackendLogs(status.backendLogs);
    (status.new_logs || []).forEach(appendLog);

    if (status.new_images?.length) {
      mergeLatestImages(status.new_images);
      await hydrateThumbs(pagedLatestImages.value);
      if (gallery.value.selectedDate === gallery.value.today) {
        await loadGallery(gallery.value.today);
      }
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
      tags: form.value.tags || ''
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

function editItem(item) {
  emit('edit-image', item);
}

function switchTab(tab) {
  gallery.value.tab = tab;
  gallery.value.page = 1;
  task.value.latestPage = 1;
}

watch(() => gallery.value.search, () => {
  gallery.value.page = 1;
  task.value.latestPage = 1;
});

watch(localTotalPages, total => {
  if (gallery.value.page > total) gallery.value.page = total;
});

watch(latestTotalPages, total => {
  if (task.value.latestPage > total) task.value.latestPage = total;
});

watch(pagedLocalImages, async items => {
  await hydrateThumbs(items);
});

watch(pagedLatestImages, async items => {
  await hydrateThumbs(items);
});

onMounted(async () => {
  await loadGallery();
  await ensureService();
  await syncStatus();
  pollTimer = window.setInterval(syncStatus, 1200);
});

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer);
});
</script>

<template>
  <div class="crawler-layout">
    <section class="panel card control-panel">
      <div class="panel-head compact-head">
        <div>
          <h2>抓图任务</h2>
          <p class="inline-note">左侧负责任务和日志，右侧切换查看本地已下载或最新抓取。</p>
        </div>
      </div>

      <div class="field-grid">
        <label>
          <span>起始页</span>
          <input v-model.number="form.startPage" type="number" min="1" />
        </label>
        <label>
          <span>结束页</span>
          <input v-model.number="form.endPage" type="number" min="1" />
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

      <div class="log-panel">
        <h3>实时日志</h3>
        <pre>{{ task.logs.join('\n') }}</pre>
      </div>
    </section>

    <section class="panel card gallery-panel">
      <div class="gallery-head">
        <div>
          <h2>{{ gallery.tab === 'local' ? '本地已下载' : '最新抓取' }}</h2>
          <p class="inline-note">
            共 {{ activeCount }} 张
            <template v-if="gallery.tab === 'local'">，当前日期 {{ gallery.selectedDate || '未选择' }}</template>
          </p>
        </div>
        <div class="gallery-tools">
          <div class="mini-nav">
            <button class="nav-chip" :class="{ active: gallery.tab === 'latest' }" @click="switchTab('latest')">最新抓取</button>
            <button class="nav-chip" :class="{ active: gallery.tab === 'local' }" @click="switchTab('local')">本地已下载</button>
          </div>
          <input v-model="gallery.search" class="search-input" type="text" placeholder="搜索作者 / 角色 / 文件名" />
        </div>
      </div>

      <GalleryCalendar
        v-if="gallery.tab === 'local'"
        :available-dates="gallery.availableDates"
        :selected-date="gallery.selectedDate"
        :today="gallery.today"
        @select="loadGallery"
      />

      <div v-if="gallery.tab === 'local' && loadingGallery" class="gallery-empty">正在读取图库...</div>
      <div v-else-if="!activeItems.length" class="gallery-empty">
        {{ gallery.tab === 'local' ? '当前日期没有图片' : '等待新的抓取结果...' }}
      </div>
      <div v-else class="gallery-grid">
        <article v-for="item in activeItems" :key="item.localPath || item.filename" class="image-card">
          <img class="thumb" :src="item.thumbUrl" :alt="item.filename" />
          <div class="card-meta">
            <strong>{{ item.artist || '未知' }}</strong>
            <span class="muted">{{ item.displayCharacters || '无角色标签' }}</span>
            <span class="muted mono">{{ item.filename }}</span>
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
  </div>
</template>
