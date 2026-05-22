<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';

const emit = defineEmits(['edit-image']);

const API_BASE = 'http://127.0.0.1:8000';
const ALL_KEY = '__all__';

// 'artist' | 'character'：两个 tab 各持一份 groups，共享同一套 UI
const activeTab = ref('artist');
const tabMeta = {
  artist: {
    label: '画师',
    endpoint: '/api/artist_favorites',
    entityLabel: '画师',
    placeholder: '搜索画师',
    addLabel: '手动添加画师',
    addTitle: '手动添加画师',
    addTextLabel: '画师名（可一次粘多个，用空格 / 逗号 / 换行分隔）',
    addExample: '例如：kantoku  mika_pikazo, sakimichan',
    emptyHint: '还没有收藏画师 —— 去抓图页点画师 chip 的 ★ 加入，或在这里手动添加',
  },
  character: {
    label: '角色',
    endpoint: '/api/character_favorites',
    entityLabel: '角色',
    placeholder: '搜索角色',
    addLabel: '手动添加角色',
    addTitle: '手动添加角色',
    addTextLabel: '角色 token（可一次粘多个，用空格 / 逗号 / 换行分隔；建议带 [source_hint]）',
    addExample: '例如：初音未来 [vocaloid]  博丽灵梦 [touhou]',
    emptyHint: '还没有收藏角色 —— 去抓图页点角色 chip 的 ★ 加入（会按 source_hint 自动归类）',
  },
};
const currentMeta = computed(() => tabMeta[activeTab.value]);

const groups = ref({});       // {groupName: [name, ...]}
const loading = ref(false);
const saving = ref(false);
const selectedGroup = ref(ALL_KEY);   // 当前过滤
const search = ref('');

const toast = ref({ show: false, msg: '', type: 'info' });
function showToast(msg, type = 'info') {
  toast.value = { show: true, msg, type };
  setTimeout(() => { toast.value.show = false; }, 2500);
}

// 全部条目扁平化 + 反向索引（用于显示每个条目属于哪些分组）
const allArtists = computed(() => {
  const m = new Map();   // name -> Set(groupName)
  for (const [g, arts] of Object.entries(groups.value)) {
    for (const a of arts) {
      if (!m.has(a)) m.set(a, new Set());
      m.get(a).add(g);
    }
  }
  return Array.from(m.entries())
    .map(([artist, groupSet]) => ({ artist, groups: Array.from(groupSet) }))
    .sort((a, b) => a.artist.localeCompare(b.artist));
});

const visibleArtists = computed(() => {
  const kw = search.value.trim().toLowerCase();
  const filter = selectedGroup.value;
  return allArtists.value.filter(it => {
    if (filter !== ALL_KEY && !it.groups.includes(filter)) return false;
    if (kw && !it.artist.toLowerCase().includes(kw)) return false;
    return true;
  });
});

const groupList = computed(() => {
  return Object.entries(groups.value)
    .map(([name, arts]) => ({ name, count: arts.length }))
    .sort((a, b) => a.name.localeCompare(b.name));
});

async function loadFavorites() {
  loading.value = true;
  try {
    const res = await fetch(`${API_BASE}${currentMeta.value.endpoint}`);
    const data = await res.json();
    if (data.ok) {
      groups.value = data.groups || {};
    }
  } catch (err) {
    showToast('加载失败: ' + err.message, 'error');
  } finally {
    loading.value = false;
  }
}

async function persist() {
  saving.value = true;
  try {
    const res = await fetch(`${API_BASE}${currentMeta.value.endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groups: groups.value }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast('保存失败: ' + (data.msg || ''), 'error');
      return false;
    }
    groups.value = data.groups || {};
    return true;
  } catch (err) {
    showToast('保存失败: ' + err.message, 'error');
    return false;
  } finally {
    saving.value = false;
  }
}

// 切换 tab 时清空本地状态并重新加载
watch(activeTab, (next) => {
  groups.value = {};
  selectedGroup.value = ALL_KEY;
  search.value = '';
  currentPage.value = 1;
  if (next === 'image') {
    loadImageFavorites();
  } else {
    loadFavorites();
  }
});

// ---------------- 图片收藏（独立列表，不复用 groups 结构） ----------------
const images = ref([]);          // [{key, date, filename, artist, characters, score, fav_count, local_path, ...}]
const imageThumbCache = ref({});  // key -> dataUrl，避免重渲染重复 hydrate
const imagesSearch = ref('');

async function loadImageFavorites() {
  loading.value = true;
  try {
    const res = await fetch(`${API_BASE}/api/image_favorites`);
    const data = await res.json();
    if (data.ok) {
      images.value = data.items || [];
      // 只孵化当前页缩略图，翻页时按需补；上百张时省一大批 IPC
      hydrateImageThumbs(pagedImages.value);
    }
  } catch (err) {
    showToast('加载图片收藏失败: ' + err.message, 'error');
  } finally {
    loading.value = false;
  }
}

async function hydrateImageThumbs(targets) {
  const list = targets && targets.length ? targets : images.value;
  // 直接走后端 /thumb 接口拿磁盘缓存的 JPEG 缩略图（~30KB 量级），
  // 避免每张图都加载几 MB 原图后再由浏览器缩放，CPU/内存压力降一个数量级。
  // 仅在图像格式才走 /thumb；视频/未知格式仍用占位符。
  await Promise.all(list.map(async (it) => {
    if (imageThumbCache.value[it.key]) return;
    const ext = (it.filename || '').split('.').pop().toLowerCase();
    const isImage = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'avif'].includes(ext);

    if (isImage && it.date && it.filename) {
      imageThumbCache.value[it.key] = `${API_BASE}/thumb/${encodeURIComponent(it.date)}/${encodeURIComponent(it.filename)}?w=400`;
      return;
    }

    // zip 的话尝试同目录的 .gif（抓图页转换后产物），失败由 onerror 兜底
    if (ext === 'zip' && it.local_path && window.desktopAPI?.file?.exists) {
      try {
        const gifPath = it.local_path.replace(/\.zip$/i, '.gif');
        if (await window.desktopAPI.file.exists(gifPath)) {
          imageThumbCache.value[it.key] = await window.desktopAPI.file.toLocalUrl(gifPath);
          return;
        }
      } catch (_) { /* fall through */ }
    }

    // 视频 / 其他：交给前端占位符（保持现状）
    imageThumbCache.value[it.key] = it.web_url ? `${API_BASE}${it.web_url}` : '';
  }));
}

const filteredImages = computed(() => {
  const kw = imagesSearch.value.trim().toLowerCase();
  if (!kw) return images.value;
  return images.value.filter(it => {
    if ((it.artist || '').toLowerCase().includes(kw)) return true;
    if ((it.filename || '').toLowerCase().includes(kw)) return true;
    if ((it.date || '').toLowerCase().includes(kw)) return true;
    if (Array.isArray(it.characters) && it.characters.some(c => String(c).toLowerCase().includes(kw))) return true;
    return false;
  });
});

async function removeImageFavorite(item) {
  if (!confirm(`移除收藏：${item.filename}？`)) return;
  try {
    const res = await fetch(`${API_BASE}/api/image_favorites/remove`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: item.key }),
    });
    const data = await res.json();
    if (!data.ok) { showToast('移除失败', 'error'); return; }
    images.value = images.value.filter(it => it.key !== item.key);
    delete imageThumbCache.value[item.key];
    showToast('已移除', 'success');
  } catch (err) {
    showToast('移除失败: ' + err.message, 'error');
  }
}

async function openImageLocally(item) {
  if (!item.local_path || !window.desktopAPI?.gallery?.openLocalFile) return;
  await window.desktopAPI.gallery.openLocalFile(item.local_path);
}

async function openImageOriginal(item) {
  if (!item.post_url || !window.desktopAPI?.external?.open) return;
  await window.desktopAPI.external.open(item.post_url);
}

// ---------------- 分页 ----------------
// 画师 / 角色：按用户选择的数量分页（写盘记忆）
// 图片：默认每页 14 张（约对应 7 列 × 2 行），用户可改成 28 / 56 / 112，独立持久化
const STORAGE_KEY_FAV_PAGE_SIZE = 'favoritesPageSize';
const STORAGE_KEY_FAV_IMAGE_PAGE_SIZE = 'favoritesImagePageSize';
const pageSizeChoice = ref(Number(localStorage.getItem(STORAGE_KEY_FAV_PAGE_SIZE)) || 30);
const imagePageSizeChoice = ref(Number(localStorage.getItem(STORAGE_KEY_FAV_IMAGE_PAGE_SIZE)) || 14);

const pageSize = computed(() => activeTab.value === 'image'
  ? Math.max(1, imagePageSizeChoice.value)
  : pageSizeChoice.value);

const currentPage = ref(1);
watch(pageSizeChoice, (v) => {
  try { localStorage.setItem(STORAGE_KEY_FAV_PAGE_SIZE, String(v)); } catch { /* noop */ }
  currentPage.value = 1;
});
watch(imagePageSizeChoice, (v) => {
  try { localStorage.setItem(STORAGE_KEY_FAV_IMAGE_PAGE_SIZE, String(v)); } catch { /* noop */ }
  currentPage.value = 1;
});

// 筛选/搜索/分组切换都从第 1 页开始，避免停在一个空页
watch([search, selectedGroup, imagesSearch], () => {
  currentPage.value = 1;
});

const currentFilteredCount = computed(() => activeTab.value === 'image'
  ? filteredImages.value.length
  : visibleArtists.value.length);

const totalPages = computed(() => Math.max(1, Math.ceil(currentFilteredCount.value / pageSize.value)));

const pagedArtists = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return visibleArtists.value.slice(start, start + pageSize.value);
});
const pagedImages = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredImages.value.slice(start, start + pageSize.value);
});

const pageNumbers = computed(() => {
  const total = totalPages.value;
  const cur = currentPage.value;
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

function gotoPage(n) {
  if (typeof n !== 'number') return;
  currentPage.value = Math.max(1, Math.min(totalPages.value, n));
}
const jumpInput = ref(1);
watch(currentPage, (n) => { jumpInput.value = n; });
function doJump() {
  const n = Math.max(1, Math.min(totalPages.value, jumpInput.value || 1));
  currentPage.value = n;
  jumpInput.value = n;
}
// 当列表收缩到当前页之外时往前回退一格（删除最后一张时常见）
watch(totalPages, (t) => {
  if (currentPage.value > t) currentPage.value = t;
});

// 翻页 / 筛选变化时按需 hydrate 这一页的缩略图（图片 tab 才有意义）
watch(pagedImages, (items) => {
  if (activeTab.value === 'image' && items.length) hydrateImageThumbs(items);
});

// ---------------- 图片浏览器（与抓图页同款体验） ----------------
const VIDEO_EXTS = ['mp4', 'webm', 'avi', 'mov', 'mkv'];
const VIEWER_HABITS_KEY = 'crawlerHabits';
function readViewerHabits() {
  try { return JSON.parse(localStorage.getItem(VIEWER_HABITS_KEY) || '{}'); }
  catch { return {}; }
}
function writeViewerHabit(key, value) {
  try {
    const habits = readViewerHabits();
    habits[key] = value;
    localStorage.setItem(VIEWER_HABITS_KEY, JSON.stringify(habits));
  } catch { /* noop */ }
}
const _initHabits = readViewerHabits();
const viewer = ref({
  open: false,
  index: 0,
  imageUrl: '',
  zoom: 1,
  fitMode: _initHabits.viewerFitMode === 'actual' ? 'actual' : 'fit',
  toolbarPinned: _initHabits.viewerToolbarPinned === true,
  toolbarHovered: false
});
const viewerToolbarVisible = computed(() => viewer.value.toolbarPinned || viewer.value.toolbarHovered);
function toggleViewerFitMode() {
  viewer.value.fitMode = viewer.value.fitMode === 'fit' ? 'actual' : 'fit';
  viewer.value.zoom = 1;
  writeViewerHabit('viewerFitMode', viewer.value.fitMode);
}
function toggleViewerToolbarPin() {
  viewer.value.toolbarPinned = !viewer.value.toolbarPinned;
  writeViewerHabit('viewerToolbarPinned', viewer.value.toolbarPinned);
}
function onViewerMouseMove(event) {
  if (viewer.value.toolbarPinned) return;
  viewer.value.toolbarHovered = event.clientY < 160;
}
const viewerItem = computed(() => filteredImages.value[viewer.value.index] || null);
const viewerIsVideo = computed(() => {
  const ext = (viewerItem.value?.filename || '').split('.').pop().toLowerCase();
  return VIDEO_EXTS.includes(ext);
});

async function syncViewerImage() {
  viewer.value.imageUrl = '';
  viewer.value.zoom = 1;
  const it = viewerItem.value;
  if (!it) return;
  const ext = (it.filename || '').split('.').pop().toLowerCase();
  if (VIDEO_EXTS.includes(ext) && it.date && it.filename) {
    viewer.value.imageUrl = `${API_BASE}/images/${it.date}/${encodeURIComponent(it.filename)}`;
    return;
  }
  if (it.local_path && window.desktopAPI?.file?.toLocalUrl) {
    try {
      if (ext === 'zip') {
        const gifPath = it.local_path.replace(/\.zip$/i, '.gif');
        if (await window.desktopAPI.file.exists(gifPath)) {
          viewer.value.imageUrl = await window.desktopAPI.file.toLocalUrl(gifPath);
          return;
        }
      }
      viewer.value.imageUrl = await window.desktopAPI.file.toLocalUrl(it.local_path);
      return;
    } catch (_) { /* fall through */ }
  }
  if (it.web_url) viewer.value.imageUrl = `${API_BASE}${it.web_url}`;
}

async function openViewer(item) {
  const idx = filteredImages.value.findIndex(it => it.key === item.key);
  if (idx < 0) return;
  viewer.value.open = true;
  viewer.value.index = idx;
  viewer.value.zoom = 1;
  await syncViewerImage();
}

function closeViewer() {
  viewer.value.open = false;
  viewer.value.imageUrl = '';
  viewer.value.zoom = 1;
}

async function stepViewer(offset) {
  if (!filteredImages.value.length) return;
  const next = Math.min(Math.max(0, viewer.value.index + offset), filteredImages.value.length - 1);
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

function onViewerKeyDown(event) {
  if (!viewer.value.open) return;
  if (event.key === 'Escape') { closeViewer(); return; }
  const tag = event.target?.tagName?.toLowerCase();
  if (['input', 'textarea', 'select', 'video'].includes(tag)) return;
  if (event.key === 'ArrowLeft') stepViewer(-1);
  else if (event.key === 'ArrowRight') stepViewer(1);
}

function editFromViewer() {
  if (!viewerItem.value) return;
  // 把 favorites 的 snake_case 字段转成 EditorPage 期望的形状
  const it = viewerItem.value;
  emit('edit-image', {
    localPath: it.local_path,
    postUrl: it.post_url,
    filename: it.filename,
    artist: it.artist,
    characters: it.characters,
    web_url: it.web_url,
    score: it.score,
    favCount: it.fav_count,
  });
  closeViewer();
}

onMounted(() => {
  window.addEventListener('keydown', onViewerKeyDown);
});
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onViewerKeyDown);
});

// ----- 分组 CRUD -----
async function createGroup() {
  const name = prompt('新建分组名称');
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) { showToast('分组名不能为空', 'error'); return; }
  if (groups.value[trimmed]) { showToast('分组已存在', 'error'); return; }
  groups.value = { ...groups.value, [trimmed]: [] };
  if (await persist()) {
    selectedGroup.value = trimmed;
    showToast(`已创建分组「${trimmed}」`, 'success');
  }
}

async function renameGroup(oldName) {
  const name = prompt('重命名分组', oldName);
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed || trimmed === oldName) return;
  if (groups.value[trimmed]) { showToast('目标分组名已存在', 'error'); return; }
  const next = {};
  for (const [k, v] of Object.entries(groups.value)) {
    next[k === oldName ? trimmed : k] = v;
  }
  groups.value = next;
  if (await persist()) {
    if (selectedGroup.value === oldName) selectedGroup.value = trimmed;
    showToast('已重命名', 'success');
  }
}

async function deleteGroup(name) {
  const arts = groups.value[name] || [];
  if (!confirm(`确定删除分组「${name}」（含 ${arts.length} 个${currentMeta.value.entityLabel}）？此操作不删除${currentMeta.value.entityLabel}本身，仅从该分组中移除。`)) return;
  const next = { ...groups.value };
  delete next[name];
  groups.value = next;
  if (await persist()) {
    if (selectedGroup.value === name) selectedGroup.value = ALL_KEY;
    showToast('已删除分组', 'success');
  }
}

// ----- 画师 CRUD -----
const manualAdd = ref({
  open: false,
  text: '',
  selectedGroups: [],
});

function openManualAdd() {
  manualAdd.value.open = true;
  manualAdd.value.text = '';
  // 默认勾选当前过滤的分组（若是"全部"则不预选）
  manualAdd.value.selectedGroups = selectedGroup.value !== ALL_KEY ? [selectedGroup.value] : [];
}
function closeManualAdd() { manualAdd.value.open = false; }

function toggleManualGroup(name) {
  const idx = manualAdd.value.selectedGroups.indexOf(name);
  if (idx >= 0) manualAdd.value.selectedGroups.splice(idx, 1);
  else manualAdd.value.selectedGroups.push(name);
}

async function submitManualAdd() {
  const names = manualAdd.value.text
    .split(/[\s,，;；\n]+/)
    .map(s => s.trim())
    .filter(Boolean);
  if (!names.length) { showToast(`请输入${currentMeta.value.entityLabel}名`, 'error'); return; }
  if (!manualAdd.value.selectedGroups.length) { showToast('请至少勾选一个分组', 'error'); return; }
  const next = { ...groups.value };
  for (const g of manualAdd.value.selectedGroups) {
    const arr = next[g] ? [...next[g]] : [];
    for (const n of names) {
      if (!arr.includes(n)) arr.push(n);
    }
    next[g] = arr;
  }
  groups.value = next;
  if (await persist()) {
    showToast(`已添加 ${names.length} 个${currentMeta.value.entityLabel}到 ${manualAdd.value.selectedGroups.length} 个分组`, 'success');
    closeManualAdd();
  }
}

// 编辑一个画师所属的分组（多选）
const editArtist = ref({
  open: false,
  artist: '',
  selectedGroups: [],
});

function openEditArtist(item) {
  editArtist.value.open = true;
  editArtist.value.artist = item.artist;
  editArtist.value.selectedGroups = [...item.groups];
}
function closeEditArtist() { editArtist.value.open = false; }

function toggleEditGroup(name) {
  const idx = editArtist.value.selectedGroups.indexOf(name);
  if (idx >= 0) editArtist.value.selectedGroups.splice(idx, 1);
  else editArtist.value.selectedGroups.push(name);
}

async function submitEditArtist() {
  const artist = editArtist.value.artist;
  const targetGroups = new Set(editArtist.value.selectedGroups);
  const next = {};
  for (const [g, arr] of Object.entries(groups.value)) {
    const has = arr.includes(artist);
    if (targetGroups.has(g) && !has) {
      next[g] = [...arr, artist];
    } else if (!targetGroups.has(g) && has) {
      next[g] = arr.filter(a => a !== artist);
    } else {
      next[g] = arr;
    }
  }
  groups.value = next;
  if (await persist()) {
    showToast('已更新分组', 'success');
    closeEditArtist();
  }
}

async function removeArtistFromAll(artist) {
  if (!confirm(`从所有分组中移除${currentMeta.value.entityLabel}「${artist}」？`)) return;
  const next = {};
  for (const [g, arr] of Object.entries(groups.value)) {
    next[g] = arr.filter(a => a !== artist);
  }
  groups.value = next;
  if (await persist()) showToast('已移除', 'success');
}

async function removeArtistFromGroup(artist, group) {
  const next = { ...groups.value };
  next[group] = (next[group] || []).filter(a => a !== artist);
  groups.value = next;
  if (await persist()) showToast(`已从「${group}」移除`, 'success');
}

async function copyArtist(artist) {
  try {
    await navigator.clipboard.writeText(artist);
    showToast(`已复制：${artist}`, 'success');
  } catch (err) {
    showToast('复制失败: ' + err.message, 'error');
  }
}

onMounted(loadFavorites);
</script>

<template>
  <div class="favorites-layout" :class="{ 'is-image-tab': activeTab === 'image' }">
    <!-- 顶部 tab 切换 -->
    <div class="fav-top-tabs">
      <button class="fav-tab" :class="{ active: activeTab === 'artist' }" @click="activeTab = 'artist'">画师收藏</button>
      <button class="fav-tab" :class="{ active: activeTab === 'character' }" @click="activeTab = 'character'">角色收藏</button>
      <button class="fav-tab" :class="{ active: activeTab === 'image' }" @click="activeTab = 'image'">图片收藏</button>
    </div>

    <template v-if="activeTab !== 'image'">
      <!-- 左侧分组列表 -->
      <section class="panel card favorites-side">
      <div class="panel-head compact-head">
        <div>
          <h2>分组</h2>
          <p class="inline-note">共 {{ groupList.length }} 个 · {{ allArtists.length }} 个{{ currentMeta.entityLabel }}</p>
        </div>
        <button @click="createGroup" :disabled="saving">新建分组</button>
      </div>

      <div class="group-list">
        <button
          class="group-item"
          :class="{ active: selectedGroup === ALL_KEY }"
          @click="selectedGroup = ALL_KEY"
        >
          <span class="group-name">全部</span>
          <span class="group-count">{{ allArtists.length }}</span>
        </button>
        <div
          v-for="g in groupList"
          :key="g.name"
          class="group-item-wrap"
        >
          <button
            class="group-item"
            :class="{ active: selectedGroup === g.name }"
            @click="selectedGroup = g.name"
          >
            <span class="group-name">{{ g.name }}</span>
            <span class="group-count">{{ g.count }}</span>
          </button>
          <div class="group-actions">
            <button class="ghost icon-btn" @click="renameGroup(g.name)" title="重命名">✎</button>
            <button class="ghost icon-btn" @click="deleteGroup(g.name)" title="删除">×</button>
          </div>
        </div>
        <div v-if="!groupList.length" class="empty-hint">还没有分组，点右上「新建分组」开始</div>
      </div>
    </section>

    <!-- 右侧条目列表 -->
    <section class="panel card favorites-main">
      <div class="panel-head compact-head">
        <div>
          <h2>{{ selectedGroup === ALL_KEY ? `全部${currentMeta.entityLabel}` : selectedGroup }}</h2>
          <p class="inline-note">
            {{ visibleArtists.length }} 个{{ currentMeta.entityLabel }}<span v-if="search"> · 已搜索</span><span v-if="totalPages > 1"> · 第 {{ currentPage }} / {{ totalPages }} 页</span> · 点击名称复制到剪贴板
          </p>
        </div>
        <div style="display: flex; gap: 8px;">
          <input
            v-model="search"
            class="search-input"
            type="text"
            :placeholder="currentMeta.placeholder"
            style="width: 200px;"
          />
          <button @click="openManualAdd" :disabled="saving">{{ currentMeta.addLabel }}</button>
        </div>
      </div>

      <div v-if="loading" class="gallery-empty">正在加载收藏...</div>
      <div v-else-if="!visibleArtists.length" class="gallery-empty">
        {{ allArtists.length ? `没有匹配的${currentMeta.entityLabel}` : currentMeta.emptyHint }}
      </div>

      <div v-else class="favorites-grid">
        <article
          v-for="item in pagedArtists"
          :key="item.artist"
          class="artist-card"
        >
          <button
            class="artist-name"
            @click="copyArtist(item.artist)"
            :title="`点击复制：${item.artist}`"
          >{{ item.artist }}</button>
          <div class="artist-groups">
            <span
              v-for="g in item.groups"
              :key="g"
              class="group-tag"
              :title="`从「${g}」中移除`"
              @click.stop="removeArtistFromGroup(item.artist, g)"
            >{{ g }} ×</span>
          </div>
          <div class="artist-actions">
            <button class="secondary" @click.stop="openEditArtist(item)" style="padding: 4px 10px; font-size: 11px;">编辑分组</button>
            <button class="ghost" @click.stop="removeArtistFromAll(item.artist)" style="padding: 4px 10px; font-size: 11px; color: #9d2c2c;">全部移除</button>
          </div>
        </article>
      </div>

      <div v-if="visibleArtists.length" class="pagination-bar fav-pagination">
        <select v-model.number="pageSizeChoice" class="search-input" style="width: auto;" title="每页数量">
          <option :value="15">15 / 页</option>
          <option :value="30">30 / 页</option>
          <option :value="60">60 / 页</option>
          <option :value="120">120 / 页</option>
        </select>
        <button class="ghost pg-btn" @click="gotoPage(1)" :disabled="currentPage <= 1" title="首页">«</button>
        <button class="ghost pg-btn" @click="gotoPage(currentPage - 1)" :disabled="currentPage <= 1" title="上一页">‹</button>
        <button
          v-for="(n, i) in pageNumbers"
          :key="`pg-${i}-${n}`"
          class="pg-num"
          :class="{ active: n === currentPage, ellipsis: n === '…' }"
          :disabled="n === '…'"
          @click="gotoPage(n)"
        >{{ n }}</button>
        <button class="ghost pg-btn" @click="gotoPage(currentPage + 1)" :disabled="currentPage >= totalPages" title="下一页">›</button>
        <button class="ghost pg-btn" @click="gotoPage(totalPages)" :disabled="currentPage >= totalPages" title="末页">»</button>
        <span class="pg-jump">
          跳转
          <input type="number" min="1" :max="totalPages" v-model.number="jumpInput" @keyup.enter="doJump" />
          / {{ totalPages }}
        </span>
      </div>
    </section>
    </template>

    <!-- 图片收藏 tab -->
    <section v-else class="panel card favorites-images-panel">
      <div class="panel-head compact-head">
        <div>
          <h2>图片收藏</h2>
          <p class="inline-note">
            共 {{ images.length }} 张<span v-if="imagesSearch"> · 已筛选 {{ filteredImages.length }} 张</span><span v-if="totalPages > 1"> · 第 {{ currentPage }} / {{ totalPages }} 页 · 每页 {{ pageSize }} 张</span> · 点击缩略图在程序内查看
          </p>
        </div>
        <div style="display: flex; gap: 8px; align-items: center;">
          <span class="search-input-wrap">
            <input
              v-model="imagesSearch"
              class="search-input search-input-with-clear"
              type="text"
              placeholder="搜索画师 / 角色 / 文件名 / 日期"
              style="width: 240px;"
            />
            <button
              v-if="imagesSearch"
              class="search-clear-btn"
              @click="imagesSearch = ''"
              title="清空搜索"
              type="button"
            >×</button>
          </span>
          <button class="secondary" @click="loadImageFavorites" :disabled="loading" style="white-space: nowrap;">
            {{ loading ? '加载中...' : '🔄 刷新' }}
          </button>
        </div>
      </div>

      <div v-if="loading" class="gallery-empty">正在加载收藏...</div>
      <div v-else-if="!filteredImages.length" class="gallery-empty">
        {{ images.length ? '没有匹配的图片' : '还没有收藏图片 —— 去抓图页点缩略图右上角的 ♡ 加入' }}
      </div>

      <div v-else class="gallery-grid">
        <article v-for="it in pagedImages" :key="it.key" class="image-card">
          <img
            class="thumb clickable-thumb"
            :src="imageThumbCache[it.key]"
            :alt="it.filename"
            loading="lazy"
            decoding="async"
            @click="openViewer(it)"
          />
          <button
            class="img-fav-toggle active"
            @click.stop="removeImageFavorite(it)"
            title="从收藏中移除"
          >♥</button>
          <div v-if="(it.score || 0) > 0 || (it.fav_count || 0) > 0" class="score-badge">
            <span><span class="score-star">★</span> {{ it.score || 0 }}</span>
            <span><span class="score-heart">♥</span> {{ it.fav_count || 0 }}</span>
          </div>
          <div class="card-meta">
            <div class="token-row">
              <button class="meta-link author-link token-chip" :title="`画师：${it.artist || '未知'}`">
                {{ it.artist || '未知' }}
              </button>
              <span class="meta-link" style="padding: 2px 8px; font-size: 11px;">{{ it.date }}</span>
            </div>
            <div class="token-row" v-if="Array.isArray(it.characters) && it.characters.length">
              <span
                v-for="token in it.characters"
                :key="`char-${it.key}-${token}`"
                class="meta-link token-chip"
              >{{ token.includes(' [') ? token.split(' [')[0] : token }}</span>
            </div>
          </div>
          <div class="button-row compact">
            <button class="secondary" @click="openImageOriginal(it)" :disabled="!it.post_url">原帖</button>
            <button class="secondary" @click="openImageLocally(it)" :disabled="!it.local_path">本地</button>
          </div>
        </article>
      </div>

      <div v-if="filteredImages.length" class="pagination-bar fav-pagination">
        <select v-model.number="imagePageSizeChoice" class="search-input" style="width: auto;" title="每页数量">
          <option :value="14">14 / 页</option>
          <option :value="28">28 / 页</option>
          <option :value="56">56 / 页</option>
          <option :value="112">112 / 页</option>
        </select>
        <button class="ghost pg-btn" @click="gotoPage(1)" :disabled="currentPage <= 1" title="首页">«</button>
        <button class="ghost pg-btn" @click="gotoPage(currentPage - 1)" :disabled="currentPage <= 1" title="上一页">‹</button>
        <button
          v-for="(n, i) in pageNumbers"
          :key="`pg-img-${i}-${n}`"
          class="pg-num"
          :class="{ active: n === currentPage, ellipsis: n === '…' }"
          :disabled="n === '…'"
          @click="gotoPage(n)"
        >{{ n }}</button>
        <button class="ghost pg-btn" @click="gotoPage(currentPage + 1)" :disabled="currentPage >= totalPages" title="下一页">›</button>
        <button class="ghost pg-btn" @click="gotoPage(totalPages)" :disabled="currentPage >= totalPages" title="末页">»</button>
        <span class="pg-jump">
          跳转
          <input type="number" min="1" :max="totalPages" v-model.number="jumpInput" @keyup.enter="doJump" />
          / {{ totalPages }}
        </span>
      </div>
    </section>

    <!-- 手动添加 modal -->
    <div v-if="manualAdd.open" class="viewer-overlay fav-overlay" @click.self="closeManualAdd">
      <div class="fav-modal">
        <div class="fav-modal-head">
          <h3>{{ currentMeta.addTitle }}</h3>
          <button class="ghost" @click="closeManualAdd" style="color: var(--muted);">×</button>
        </div>
        <label class="fav-field">
          <span>{{ currentMeta.addTextLabel }}</span>
          <textarea
            v-model="manualAdd.text"
            class="fav-textarea"
            :placeholder="currentMeta.addExample"
          ></textarea>
        </label>
        <label class="fav-field">
          <span>添加到分组（可多选）</span>
          <div class="fav-checkbox-list">
            <label v-for="g in groupList" :key="g.name" class="fav-checkbox-row">
              <input
                type="checkbox"
                :checked="manualAdd.selectedGroups.includes(g.name)"
                @change="toggleManualGroup(g.name)"
              />
              <span>{{ g.name }} <span class="muted compact-text">({{ g.count }})</span></span>
            </label>
            <div v-if="!groupList.length" class="muted compact-text">还没有分组，请先点左上「新建分组」</div>
          </div>
        </label>
        <div class="fav-modal-foot">
          <button class="ghost" @click="closeManualAdd" style="color: var(--accent-deep);">取消</button>
          <button @click="submitManualAdd" :disabled="saving">{{ saving ? '保存中...' : '确定添加' }}</button>
        </div>
      </div>
    </div>

    <!-- 编辑所属分组 modal -->
    <div v-if="editArtist.open" class="viewer-overlay fav-overlay" @click.self="closeEditArtist">
      <div class="fav-modal">
        <div class="fav-modal-head">
          <h3>编辑「{{ editArtist.artist }}」所属分组</h3>
          <button class="ghost" @click="closeEditArtist" style="color: var(--muted);">×</button>
        </div>
        <div class="fav-checkbox-list">
          <label v-for="g in groupList" :key="g.name" class="fav-checkbox-row">
            <input
              type="checkbox"
              :checked="editArtist.selectedGroups.includes(g.name)"
              @change="toggleEditGroup(g.name)"
            />
            <span>{{ g.name }} <span class="muted compact-text">({{ g.count }})</span></span>
          </label>
        </div>
        <div class="fav-modal-foot">
          <button class="ghost" @click="closeEditArtist" style="color: var(--accent-deep);">取消</button>
          <button @click="submitEditArtist" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</button>
        </div>
      </div>
    </div>

    <!-- 大图查看器（与抓图页同款） -->
    <div v-if="viewer.open" class="viewer-overlay" @click.self="closeViewer" @mousemove="onViewerMouseMove" @mouseleave="viewer.toolbarHovered = false">
      <div class="viewer-toolbar" :class="{ 'is-hidden': !viewerToolbarVisible }">
        <div class="viewer-toolbar-info">
          <strong>{{ viewerItem?.artist || '未知' }}</strong>
          <span class="muted compact-text" style="color: #ccc;">
            第 {{ viewer.index + 1 }} / {{ filteredImages.length }} 张
          </span>
          <span class="muted compact-text" style="color: #ccc;">{{ viewerItem?.date }}</span>
          <span v-if="(viewerItem?.score || 0) > 0" class="viewer-score">★ {{ viewerItem.score }}</span>
          <span v-if="(viewerItem?.fav_count || 0) > 0" class="viewer-fav">♥ {{ viewerItem.fav_count }}</span>
        </div>
        <div class="button-row compact viewer-actions">
          <button class="secondary" @click="stepViewer(-1)" :disabled="viewer.index <= 0">上一张</button>
          <button class="secondary" @click="stepViewer(1)" :disabled="viewer.index >= filteredImages.length - 1">下一张</button>
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
            class="ghost"
            @click="viewerItem && removeImageFavorite(viewerItem)"
            :disabled="!viewerItem"
            style="color: #fff; border: 1px solid rgba(255, 188, 188, 0.4); background: rgba(157, 44, 44, 0.25);"
          >取消收藏</button>
          <button @click="editFromViewer" :disabled="!viewerItem" style="background: linear-gradient(135deg, var(--accent), var(--accent-deep)); border: none; color: white;">编辑图片</button>
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

    <div v-if="toast.show" class="toast-overlay" :class="toast.type">{{ toast.msg }}</div>
  </div>
</template>

<style scoped>
.favorites-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  grid-template-rows: auto 1fr;
  gap: 16px;
  min-height: 100%;
}
.favorites-layout.is-image-tab {
  grid-template-columns: minmax(0, 1fr);
}
.fav-top-tabs {
  grid-column: 1 / -1;
  display: flex;
  gap: 6px;
  padding: 4px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid var(--line);
  border-radius: 999px;
  align-self: start;
  width: fit-content;
}
.favorites-images-panel {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
/* 图片 tab 复用全局 .gallery-grid / .image-card，外加 content-visibility 让屏外卡片彻底零成本 */
.favorites-images-panel .gallery-grid {
  /* 默认 14 张时其实是 2 行；但用户可改成 28/56/112，这里允许 flex 拉伸并滚动 */
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  grid-auto-rows: min-content;
  align-content: start;
}
.favorites-images-panel .image-card {
  content-visibility: auto;
  contain-intrinsic-size: 200px 320px;
  contain: layout paint style;
  position: relative;
}
/* 收藏页内的「移除收藏」红心按钮，固定在缩略图右上角 */
.favorites-images-panel .img-fav-toggle {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 3;
  width: 30px;
  height: 30px;
  padding: 0;
  border: none;
  border-radius: 50%;
  color: #fff;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s, transform 0.15s;
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  box-shadow: 0 2px 8px rgba(209, 40, 105, 0.5);
}
.favorites-images-panel .img-fav-toggle:hover {
  background: linear-gradient(135deg, #ff7aa1, #e0367a);
  transform: scale(1.08);
}

/* 收藏页底部分页栏：复用全局 .pagination-bar 样式，只追加间距 */
.fav-pagination {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--line);
  flex-wrap: wrap;
}

/* 共享给图片 tab 搜索框的清空按钮（与 CrawlerPage 同款样式） */
.search-input-wrap { position: relative; display: inline-flex; align-items: center; }
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

.favorites-side {
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 32px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.fav-tab-bar {
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
  padding: 4px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid var(--line);
  border-radius: 999px;
}
.fav-tab {
  flex: 1;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 700;
  color: var(--accent-deep);
  background: transparent;
  border: none;
  border-radius: 999px;
  cursor: pointer;
  transition: background 0.18s, color 0.18s;
}
.fav-tab:hover { background: rgba(243, 223, 212, 0.6); }
.fav-tab.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  box-shadow: 0 2px 6px rgba(180, 110, 22, 0.3);
}
.favorites-main {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.group-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 0;
}
.group-item {
  width: 100%;
  text-align: left;
  background: rgba(255, 255, 255, 0.55);
  color: var(--ink);
  border: 1px solid var(--line);
  padding: 8px 12px;
  border-radius: 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
}
.group-item:hover { background: rgba(243, 223, 212, 0.55); }
.group-item.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border-color: transparent;
}
.group-item.active .group-count { background: rgba(255, 255, 255, 0.25); color: #fff; }
.group-name { word-break: break-all; }
.group-count {
  flex-shrink: 0;
  margin-left: 8px;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--soft);
  color: var(--accent-deep);
}

.group-item-wrap {
  position: relative;
}
.group-actions {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  display: none;
  gap: 4px;
  z-index: 2;
}
.group-item-wrap:hover .group-actions { display: flex; }
.icon-btn {
  width: 24px;
  height: 24px;
  padding: 0;
  border-radius: 6px;
  font-size: 12px;
  background: rgba(255, 255, 255, 0.9);
  color: var(--ink);
}
.group-item.active ~ .group-actions .icon-btn,
.group-item-wrap:hover .group-item.active + .group-actions .icon-btn {
  background: rgba(255, 255, 255, 0.95);
  color: var(--accent-deep);
}

.empty-hint {
  padding: 16px;
  text-align: center;
  color: var(--muted);
  font-size: 12px;
  border: 1px dashed var(--line);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.4);
}

.favorites-grid {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  /* 单列行式列表：每个画师/角色一行，名字 + 分组 + 操作横向排开 */
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-right: 4px;
}
.artist-card {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.7);
  content-visibility: auto;
  contain-intrinsic-size: 100% 44px;
}
.artist-card .artist-name {
  flex: 0 0 220px;
  width: 220px;
  max-width: 40%;
}
.artist-card .artist-groups {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
}
.artist-card .artist-actions {
  flex-shrink: 0;
  margin-top: 0;
}
.artist-name {
  width: 100%;
  text-align: left;
  background: linear-gradient(135deg, #fbf4eb, #f2e8db);
  color: var(--ink);
  font-family: Consolas, monospace;
  font-size: 13px;
  font-weight: 700;
  padding: 8px 10px;
  border-radius: 8px;
  word-break: break-all;
  cursor: copy;
}
.artist-name:hover {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
}
.artist-groups {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.group-tag {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--soft);
  color: var(--accent-deep);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
}
.group-tag:hover {
  background: rgba(157, 44, 44, 0.2);
  color: #9d2c2c;
}
.artist-actions {
  display: flex;
  gap: 6px;
  margin-top: auto;
}

.fav-overlay {
  z-index: 10000;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 24px;
}
.fav-modal {
  width: 480px;
  max-width: 92vw;
  background: rgba(255, 250, 243, 0.98);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.fav-modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.fav-modal-head h3 {
  margin: 0;
  color: var(--accent-deep);
  font-size: 17px;
}
.fav-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.fav-textarea {
  width: 100%;
  height: 80px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  color: var(--ink);
  font-size: 13px;
  resize: vertical;
}
.fav-checkbox-list {
  max-height: 240px;
  overflow-y: auto;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid var(--line);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fav-checkbox-row {
  display: flex;
  gap: 8px;
  align-items: center;
  cursor: pointer;
  font-size: 13px;
  color: var(--ink);
}
.fav-checkbox-row input[type="checkbox"] {
  width: auto;
  margin: 0;
}
.fav-modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
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
  z-index: 10100;
  box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  pointer-events: none;
}
.toast-overlay.success { background: rgba(212, 237, 218, 0.95); color: #155724; border: 1px solid #c3e6cb; }
.toast-overlay.error { background: rgba(248, 215, 218, 0.95); color: #721c24; border: 1px solid #f5c6cb; }
.toast-overlay.info { background: rgba(209, 236, 241, 0.95); color: #0c5460; border: 1px solid #bee5eb; }

@media (max-width: 980px) {
  .favorites-layout { grid-template-columns: 1fr; }
  .favorites-side { position: static; max-height: none; }
}
</style>
