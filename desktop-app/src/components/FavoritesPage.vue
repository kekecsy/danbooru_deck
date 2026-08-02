<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

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
// 缩略图淡入：每张图 @load 后把 key 记下来，CSS 用 .is-loaded 把 opacity 0→1。
// 翻页或清空时整个 Set 替换为新的（保留旧 key 不变）以触发响应式
const thumbLoaded = ref(new Set());
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

    // zip 的话尝试同目录的 .gif（抓图页转换后产物），失败由 onerror 兜底。
    // 顺便把 hasGifCompanion 写回 item，让 viewer 工具栏的「转GIF」按钮按状态显示。
    if (ext === 'zip' && it.local_path && window.desktopAPI?.file?.exists) {
      try {
        const gifPath = it.local_path.replace(/\.zip$/i, '.gif');
        if (await window.desktopAPI.file.exists(gifPath)) {
          imageThumbCache.value[it.key] = await window.desktopAPI.file.toLocalUrl(gifPath);
          it.hasGifCompanion = true;
          return;
        }
        it.hasGifCompanion = false;
      } catch (_) { /* fall through */ }
    }

    // 视频 / 其他：交给前端占位符（保持现状）
    imageThumbCache.value[it.key] = it.web_url ? `${API_BASE}${it.web_url}` : '';
  }));
}

// 跟抓图页同款「是否动画/视频」判定，复用 .video-format-watermark 水印
// （抓图页用的是 isAnimatedCard / isVideoItem / cardFormatLabel，这里单独写一份
//  避免两个组件互相 import）
const FAV_VIDEO_EXTS = ['webm', 'mp4'];
function isFavAnimatedCard(it) {
  const ext = (it.filename || '').split('.').pop().toLowerCase();
  if (FAV_VIDEO_EXTS.includes(ext)) return true;
  if (ext === 'zip') {
    // zip 转 GIF 成功 = 动画
    const gifPath = (it.local_path || '').replace(/\.zip$/i, '.gif');
    return !!gifPath;
  }
  return ext === 'gif';
}
function isFavVideoItem(it) {
  const ext = (it.filename || '').split('.').pop().toLowerCase();
  return FAV_VIDEO_EXTS.includes(ext);
}
function favCardFormatLabel(it) {
  return isFavVideoItem(it) ? 'mp4' : 'gif';
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
  const ok = await showFavConfirm({
    message: `移除收藏：${item.filename}？`,
    confirmText: '移除',
    danger: true,
  });
  if (!ok) return;
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

// ZIP → GIF 转换：跟抓图页 viewer 同款按钮，逻辑共用 /api/convert_local_zip
async function convertGif(item) {
  if (!item?.local_path) {
    showToast('找不到本地路径', 'error');
    return;
  }
  showToast('正在转换为 GIF...', 'info');
  try {
    const res = await fetch('http://127.0.0.1:8000/api/convert_local_zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ local_path: item.local_path }),
    });
    const result = await res.json();
    if (result.ok) {
      showToast('转换成功，正在打开...', 'success');
      await window.desktopAPI.gallery.openLocalFile(result.gif_path);
      // 标记同 item 已转过 GIF，让 toolbar 的「转GIF」按钮立刻消失
      item.hasGifCompanion = true;
    } else {
      showToast('转换失败: ' + result.msg, 'error');
    }
  } catch (err) {
    showToast('请求失败: ' + err.message, 'error');
  }
}

// 复制图片到剪贴板：跟抓图页 viewer 同款按钮。
// 收藏页 item 用 snake_case 字段（local_path / filename），逻辑跟抓图页一致
async function copyViewerImage() {
  const item = viewerItem.value;
  if (!item?.local_path) {
    showToast('当前图片没有可复制的本地文件', 'warning');
    return;
  }
  let imagePath = item.local_path;
  if ((item.filename || '').toLowerCase().endsWith('.zip')) {
    const gifPath = imagePath.replace(/\.zip$/i, '.gif');
    if (await window.desktopAPI.file.exists(gifPath)) imagePath = gifPath;
  }
  try {
    const result = await window.desktopAPI.caption.copyImage(imagePath, 2000);
    if (result?.ok) {
      if (result.isGif) {
        const kb = Math.round((result.bytes || 0) / 1024);
        showToast(`已复制完整 GIF ${result.width}×${result.height}（${kb}KB，保留多帧动画）`, 'success');
      } else {
        showToast(`已复制图片 ${result.width}×${result.height}（上限 2000px）`, 'success');
      }
    } else {
      showToast(`复制失败：${result?.error || '不支持的图片格式'}`, 'error');
    }
  } catch (err) {
    showToast('复制失败：' + err.message, 'error');
  }
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
// viewer 主图淡入：src 变化时置 false，@load 后置 true 触发 .is-loaded
const viewerImageLoaded = ref(false);
watch(() => viewer.value.imageUrl, () => { viewerImageLoaded.value = false; });
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

// viewer 里画师/角色 token chip：复用爬虫页的 splitTags 风格（空格分隔）。
// 这里没引入 CrawlerPage 的 splitTags，避免跨组件 import；favorites 自身有 copyArtist 用同样逻辑。
function favSplitArtist(s) {
  if (!s) return [];
  return String(s).trim().split(/\s+/).filter(Boolean);
}
// 角色 token 在收藏里已经是数组（后端给的），原样用；显示时去掉 "[source]" 后缀，跟爬虫页一致
function favCharLabel(token) {
  return token.includes(' [') ? token.split(' [')[0] : token;
}
const viewerArtistTokens = computed(() => favSplitArtist(viewerItem.value?.artist || ''));
const viewerCharacterTokens = computed(() => Array.isArray(viewerItem.value?.characters) ? viewerItem.value.characters : []);

// viewer 内点击 token chip：复制原始 token 到剪贴板（含 [source] 后缀）
async function copyViewerToken(token) {
  if (!token) return;
  try {
    await navigator.clipboard.writeText(token);
    showToast(`已复制：${favCharLabel(token)}`, 'success');
  } catch (e) {
    showToast('复制失败：' + e.message, 'error');
  }
}
// viewer 角落信息栏「跳转到第 N 张」输入框回车处理
async function onViewerJump(e) {
  const total = filteredImages.value.length;
  if (!total) return;
  const v = Math.max(1, Math.min(total, Number(e.target.value) || 1));
  if (v - 1 === viewer.value.index) {
    e.target.value = v;
    return;
  }
  viewer.value.index = v - 1;
  await syncViewerImage();
  e.target.value = v;
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
  // 点分组菜单外任意位置关闭菜单
  document.addEventListener('click', onDocumentClick);
});
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onViewerKeyDown);
  document.removeEventListener('click', onDocumentClick);
});

// ----- 分组 CRUD -----
// createGroup 现在只是个触发器：实际创建逻辑在 submitCreateGroup() 里，
// 通过 createGroupModal 完成（替代原本失效的 prompt()）
function createGroup() {
  openCreateGroup();
}

// ----- 分组重命名（自定义 modal，替代原本失效的 prompt()） -----
// Electron 的 window.prompt() 在 renderer 里是空实现（点完没反应），
// 所以走自建 modal，校验 / Enter / Esc / focus 全可控。
const renameGroupModal = ref({
  open: false,
  oldName: '',
  newName: '',
  error: '',
});
const renameInput = ref(null);
const openMenuForGroup = ref(null);

function openRenameGroup(oldName) {
  renameGroupModal.value = {
    open: true,
    oldName,
    newName: oldName,
    error: '',
  };
  openMenuForGroup.value = null;
  // 下一帧聚焦 + 全选，方便用户直接覆盖
  nextTick(() => {
    const el = renameInput.value;
    if (el) {
      el.focus();
      el.select();
    }
  });
}

function closeRenameGroupModal() {
  renameGroupModal.value.open = false;
  renameGroupModal.value.error = '';
}

const canSubmitRename = computed(() => {
  const m = renameGroupModal.value;
  if (!m.open) return false;
  const trimmed = (m.newName || '').trim();
  if (!trimmed) return false;
  if (m.error) return false;
  return true;
});

async function submitRenameGroup() {
  if (!canSubmitRename.value) return;
  const oldName = renameGroupModal.value.oldName;
  const trimmed = renameGroupModal.value.newName.trim();
  // 与原名相同：直接关 modal，不打后端
  if (trimmed === oldName) { closeRenameGroupModal(); return; }
  const next = {};
  for (const [k, v] of Object.entries(groups.value)) {
    next[k === oldName ? trimmed : k] = v;
  }
  groups.value = next;
  if (await persist()) {
    if (selectedGroup.value === oldName) selectedGroup.value = trimmed;
    showToast(`已重命名为「${trimmed}」`, 'success');
    closeRenameGroupModal();
  }
}

// 实时校验：用户每键入一个字符就更新错误提示
watch(() => renameGroupModal.value.newName, (val) => {
  if (!renameGroupModal.value.open) return;
  const trimmed = (val || '').trim();
  if (!trimmed) {
    renameGroupModal.value.error = '分组名不能为空';
  } else if (groups.value[trimmed] && trimmed !== renameGroupModal.value.oldName) {
    renameGroupModal.value.error = `分组「${trimmed}」已存在`;
  } else {
    renameGroupModal.value.error = '';
  }
});

// ----- 分组操作菜单（⋮）：hover 出现，点击展开「重命名 / 删除」 -----
function toggleGroupMenu(name) {
  openMenuForGroup.value = openMenuForGroup.value === name ? null : name;
}

function closeGroupMenu() {
  openMenuForGroup.value = null;
}

// 点菜单外部关闭：用 document 监听，避免和 v-if / hover 互相打架
// 同时也处理画师行内编辑器的「点外部关闭」
function onDocumentClick(e) {
  if (openMenuForGroup.value && !e.target.closest('.group-item-wrap')) {
    openMenuForGroup.value = null;
  }
  if (editingArtist.value && !e.target.closest('.artist-card')) {
    editingArtist.value = '';
    editingNewGroupName.value = '';
  }
}

// ----- 新建分组（自定义 modal，替代原本失效的 prompt()） -----
// 跟 renameGroup 几乎对称，但只需要输入新名，不显示原名字段
const createGroupModal = ref({
  open: false,
  name: '',
  error: '',
});
const createGroupInput = ref(null);

function openCreateGroup() {
  createGroupModal.value = { open: true, name: '', error: '' };
  openMenuForGroup.value = null;
  nextTick(() => {
    const el = createGroupInput.value;
    if (el) el.focus();
  });
}

function closeCreateGroupModal() {
  createGroupModal.value.open = false;
  createGroupModal.value.error = '';
}

const canSubmitCreateGroup = computed(() => {
  const m = createGroupModal.value;
  if (!m.open) return false;
  const trimmed = (m.name || '').trim();
  if (!trimmed) return false;
  if (m.error) return false;
  return true;
});

async function submitCreateGroup() {
  if (!canSubmitCreateGroup.value) return;
  const trimmed = createGroupModal.value.name.trim();
  // 重复检查（实时校验里已经做过，但 submit 仍兜底）
  if (groups.value[trimmed]) {
    createGroupModal.value.error = `分组「${trimmed}」已存在`;
    return;
  }
  groups.value = { ...groups.value, [trimmed]: [] };
  if (await persist()) {
    selectedGroup.value = trimmed;
    showToast(`已创建分组「${trimmed}」`, 'success');
    closeCreateGroupModal();
  }
}

// 实时校验
watch(() => createGroupModal.value.name, (val) => {
  if (!createGroupModal.value.open) return;
  const trimmed = (val || '').trim();
  if (!trimmed) {
    createGroupModal.value.error = '分组名不能为空';
  } else if (groups.value[trimmed]) {
    createGroupModal.value.error = `分组「${trimmed}」已存在`;
  } else {
    createGroupModal.value.error = '';
  }
});

// ----- 通用 confirm modal（替代所有 confirm()） -----
// 异步 API：const ok = await showFavConfirm({ message: '...' });
// 跟 prompt() 一样，Electron renderer 里 confirm 也是可能失效/样式不可控，
// 统一走自建 modal 顺便也能 danger 模式高亮、定制按钮文案
const favConfirm = ref({
  open: false,
  message: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
  resolve: null,
});
const favConfirmBtn = ref(null);

function showFavConfirm(options) {
  return new Promise((resolve) => {
    favConfirm.value = {
      open: true,
      message: options.message,
      confirmText: options.confirmText || '确定',
      cancelText: options.cancelText || '取消',
      danger: !!options.danger,
      resolve,
    };
  });
}

function _resolveFavConfirm(value) {
  const resolve = favConfirm.value.resolve;
  favConfirm.value.open = false;
  favConfirm.value.resolve = null;
  if (resolve) resolve(value);
}

// 打开后默认 focus 在取消按钮（防误触，危险操作尤其重要），
// 用户按 Esc 取消 / Enter 确认（按钮 focus 在取消时也会被 Enter 触发），
// 实际上更稳妥：自动 focus 取消按钮，需要按 Tab 切到确认按钮
watch(() => favConfirm.value.open, (open) => {
  if (open) {
    nextTick(() => favConfirmBtn.value?.focus());
  }
});

async function deleteGroup(name) {
  const arts = groups.value[name] || [];
  const ok = await showFavConfirm({
    message: `确定删除分组「${name}」（含 ${arts.length} 个${currentMeta.value.entityLabel}）？此操作不删除${currentMeta.value.entityLabel}本身，仅从该分组中移除。`,
    confirmText: '删除',
    danger: true,
  });
  if (!ok) return;
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

// ----- 编辑画师所属分组（行内编辑模式，即时提交） -----
// 替代原本的 editArtist modal：点「编辑分组」后，artist-card 内部展开一个
// 行内面板，已有分组用 chip 切换（即时生效），底部输入框+回车「创建并加入」新分组。
// 不再需要「保存」按钮，每次切换都直接打后端；点击卡片外部或「完成」关闭。
const editingArtist = ref('');
const editingNewGroupName = ref('');
const editingInputRef = ref(null);

function isArtistInGroup(artist, groupName) {
  return (groups.value[groupName] || []).includes(artist);
}

function openEditArtist(item) {
  if (editingArtist.value === item.artist) {
    // 已经在编辑同一个画师 → 关闭
    closeEditArtist();
    return;
  }
  editingArtist.value = item.artist;
  editingNewGroupName.value = '';
  // 下一帧聚焦到新分组输入框
  nextTick(() => {
    const el = editingInputRef.value;
    if (el) el.focus();
  });
}

function closeEditArtist() {
  editingArtist.value = '';
  editingNewGroupName.value = '';
}

// 切换「artist 在某 group」：即时 commit，无保存按钮
async function toggleArtistGroup(artist, groupName) {
  if (!artist || !groupName) return;
  const isIn = isArtistInGroup(artist, groupName);
  const next = { ...groups.value };
  if (isIn) {
    next[groupName] = (next[groupName] || []).filter(a => a !== artist);
  } else {
    next[groupName] = [...(next[groupName] || []), artist];
  }
  groups.value = next;
  await persist();
  // 不关闭编辑器，允许连续调整多个分组
}

// 新建分组并把当前编辑的画师加入：若分组名已存在则复用并只加入画师
async function createGroupAndAssign(artist) {
  if (!artist) return;
  const trimmed = editingNewGroupName.value.trim();
  if (!trimmed) return;
  // 实时校验同样的查重逻辑：已存在时不要走「创建」，只加入
  const exists = !!groups.value[trimmed];
  const alreadyIn = exists && isArtistInGroup(artist, trimmed);
  if (exists && alreadyIn) {
    showToast(`「${trimmed}」里已经有「${artist}」了`, 'info');
    editingNewGroupName.value = '';
    return;
  }
  const next = { ...groups.value };
  if (exists) {
    next[trimmed] = [...next[trimmed], artist];
  } else {
    next[trimmed] = [artist];
  }
  groups.value = next;
  if (await persist()) {
    if (exists) {
      showToast(`已加入分组「${trimmed}」`, 'success');
    } else {
      showToast(`已创建并加入分组「${trimmed}」`, 'success');
    }
    editingNewGroupName.value = '';
  }
}

async function removeArtistFromAll(artist) {
  const ok = await showFavConfirm({
    message: `从所有分组中移除${currentMeta.value.entityLabel}「${artist}」？`,
    confirmText: '全部移除',
    danger: true,
  });
  if (!ok) return;
  // 如果移除的正是当前正在编辑的画师，先关掉行内编辑器
  // （否则 editingArtist 指向一个不再可见的项，UI 状态会卡住）
  if (editingArtist.value === artist) closeEditArtist();
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
          <button
            class="ghost group-menu-trigger"
            :class="{ 'is-open': openMenuForGroup === g.name }"
            @click.stop="toggleGroupMenu(g.name)"
            title="更多操作"
            aria-label="更多操作"
            :disabled="saving"
          >⋮</button>
          <div
            v-if="openMenuForGroup === g.name"
            class="group-menu"
            @click.stop
          >
            <button class="group-menu-item" @click="openRenameGroup(g.name)">重命名</button>
            <button class="group-menu-item danger" @click="deleteGroup(g.name)">删除</button>
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
          <!-- 第一行：画师名 + 右侧动作按钮（横排） -->
          <div class="artist-header">
            <button
              class="artist-name"
              @click="copyArtist(item.artist)"
              :title="`点击复制：${item.artist}`"
            >{{ item.artist }}</button>
            <div class="artist-actions">
              <button
                class="secondary action-btn"
                @click.stop="openEditArtist(item)"
              >{{ editingArtist === item.artist ? '完成' : '编辑' }}</button>
              <button
                class="ghost action-btn action-btn-danger"
                @click.stop="removeArtistFromAll(item.artist)"
              >移除</button>
            </div>
          </div>
          <!-- 编辑模式：行内面板，替代原来弹出的 modal -->
          <div
            v-if="editingArtist === item.artist"
            class="artist-edit-panel"
            @click.stop
          >
            <div class="edit-panel-label">点击分组切换加入/移出：</div>
            <div class="edit-panel-chips">
              <button
                v-for="g in groupList"
                :key="g.name"
                class="edit-group-chip"
                :class="{
                  active: isArtistInGroup(item.artist, g.name),
                }"
                :disabled="saving"
                @click.stop="toggleArtistGroup(item.artist, g.name)"
              >{{ g.name }}</button>
              <span v-if="!groupList.length" class="edit-panel-empty">还没有分组，去左侧「新建分组」</span>
            </div>
            <div class="edit-panel-divider">
              <span>或新建分组</span>
            </div>
            <div class="edit-panel-new">
              <input
                ref="editingInputRef"
                v-model="editingNewGroupName"
                class="edit-panel-input"
                type="text"
                placeholder="输入新分组名（按 Enter 添加）"
                maxlength="100"
                @keyup.enter="createGroupAndAssign(item.artist)"
                @click.stop
              />
              <button
                class="edit-panel-add-btn"
                :disabled="!editingNewGroupName.trim() || saving"
                @click.stop="createGroupAndAssign(item.artist)"
              >+ 创建并加入</button>
            </div>
          </div>
          <!-- 普通模式：显示 group tags -->
          <div v-else class="artist-groups">
            <span
              v-for="g in item.groups"
              :key="g"
              class="group-tag"
              :title="`从「${g}」中移除`"
              @click.stop="removeArtistFromGroup(item.artist, g)"
            >{{ g }} ×</span>
            <span v-if="!item.groups.length" class="group-tag-empty">未分组 · 点「编辑」加入</span>
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
            {{ loading ? '加载中...' : '刷新' }}
          </button>
        </div>
      </div>

      <div v-if="loading" class="gallery-empty">正在加载收藏...</div>
      <div v-else-if="!filteredImages.length" class="gallery-empty">
        {{ images.length ? '没有匹配的图片' : '还没有收藏图片 —— 去抓图页点缩略图右上角的 ♡ 加入' }}
      </div>

      <div v-else class="gallery-grid" :style="`--card-min-w: 180px`">
        <article
          v-for="it in pagedImages"
          :key="it.key"
          class="image-card is-img-favorited"
        >
          <div class="thumb-wrap" :class="{ 'is-broken': it.thumbBroken }">
            <img
              class="thumb clickable-thumb"
              :class="{ 'is-loaded': thumbLoaded.has(it.key) }"
              :src="imageThumbCache[it.key]"
              :alt="it.filename"
              loading="lazy"
              decoding="async"
              @load="thumbLoaded.add(it.key); thumbLoaded = new Set(thumbLoaded)"
              @click="openViewer(it)"
            />
            <div v-if="!thumbLoaded.has(it.key) && imageThumbCache[it.key]" class="thumb-skeleton" aria-hidden="true"></div>
            <!-- 动画 / 视频格式水印（zip 配 gif 兄弟、gif 等）：
                 跟抓图页同样的视觉语言，让两个 tab 看起来是同一套设计 -->
            <span
              v-if="isFavAnimatedCard(it)"
              class="video-format-watermark"
              :class="`format-${favCardFormatLabel(it)}`"
              :aria-label="favCardFormatLabel(it).toUpperCase()"
            >
              <svg v-if="isFavVideoItem(it)" class="format-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M8 5v14l11-7z"/>
              </svg>
              <span v-else class="format-text">GIF</span>
            </span>
            <span
              v-if="it.rating"
              class="rating-badge"
              :class="`rating-${it.rating}`"
              :title="`Danbooru 分级：${it.rating.toUpperCase()}`"
            >{{ it.rating.toUpperCase() }}</span>
          </div>
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
          <div class="button-row compact card-actions">
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

    <!-- 重命名分组 modal（替代原本的 prompt()，因为 Electron renderer 里 prompt 是空实现） -->
    <div
      v-if="renameGroupModal.open"
      class="viewer-overlay fav-overlay"
      @click.self="closeRenameGroupModal"
    >
      <div class="fav-modal">
        <div class="fav-modal-head">
          <h3>重命名分组</h3>
          <button class="ghost" @click="closeRenameGroupModal" style="color: var(--muted);">×</button>
        </div>
        <label class="fav-field">
          <span>原名</span>
          <div class="fav-rename-old">{{ renameGroupModal.oldName }}</div>
        </label>
        <label class="fav-field">
          <span>新名</span>
          <input
            ref="renameInput"
            v-model="renameGroupModal.newName"
            class="fav-rename-input"
            type="text"
            placeholder="输入新的分组名"
            maxlength="100"
            @keyup.enter="submitRenameGroup"
            @keyup.esc="closeRenameGroupModal"
          />
        </label>
        <div v-if="renameGroupModal.error" class="fav-rename-error">
          {{ renameGroupModal.error }}
        </div>
        <div class="fav-modal-foot">
          <button class="ghost" @click="closeRenameGroupModal" style="color: var(--accent-deep);">取消</button>
          <button @click="submitRenameGroup" :disabled="saving || !canSubmitRename">
            {{ saving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 新建分组 modal（替代原本的 prompt()） -->
    <div
      v-if="createGroupModal.open"
      class="viewer-overlay fav-overlay"
      @click.self="closeCreateGroupModal"
    >
      <div class="fav-modal">
        <div class="fav-modal-head">
          <h3>新建分组</h3>
          <button class="ghost" @click="closeCreateGroupModal" style="color: var(--muted);">×</button>
        </div>
        <label class="fav-field">
          <span>分组名</span>
          <input
            ref="createGroupInput"
            v-model="createGroupModal.name"
            class="fav-rename-input"
            type="text"
            placeholder="给新分组起个名字"
            maxlength="100"
            @keyup.enter="submitCreateGroup"
            @keyup.esc="closeCreateGroupModal"
          />
        </label>
        <div v-if="createGroupModal.error" class="fav-rename-error">
          {{ createGroupModal.error }}
        </div>
        <div class="fav-modal-foot">
          <button class="ghost" @click="closeCreateGroupModal" style="color: var(--accent-deep);">取消</button>
          <button @click="submitCreateGroup" :disabled="saving || !canSubmitCreateGroup">
            {{ saving ? '创建中...' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 通用 confirm modal（替代所有 confirm()） -->
    <div
      v-if="favConfirm.open"
      class="viewer-overlay fav-overlay"
      @click.self="_resolveFavConfirm(false)"
      @keyup.esc="_resolveFavConfirm(false)"
    >
      <div class="fav-modal fav-confirm-modal" :class="{ 'is-danger': favConfirm.danger }">
        <div class="fav-modal-head">
          <h3>{{ favConfirm.danger ? '请确认' : '提示' }}</h3>
          <button class="ghost" @click="_resolveFavConfirm(false)" style="color: var(--muted);">×</button>
        </div>
        <div class="fav-confirm-message">{{ favConfirm.message }}</div>
        <div class="fav-modal-foot">
          <button
            ref="favConfirmBtn"
            class="ghost"
            @click="_resolveFavConfirm(false)"
            @keyup.enter="_resolveFavConfirm(false)"
            style="color: var(--accent-deep);"
          >{{ favConfirm.cancelText }}</button>
          <button
            :class="favConfirm.danger ? 'fav-confirm-danger' : ''"
            @click="_resolveFavConfirm(true)"
            @keyup.enter="_resolveFavConfirm(true)"
          >{{ favConfirm.confirmText }}</button>
        </div>
      </div>
    </div>

    <!-- 大图查看器（与抓图页同款） -->
    <div v-if="viewer.open" class="viewer-overlay" @click.self="closeViewer" @mousemove="onViewerMouseMove" @mouseleave="viewer.toolbarHovered = false">
      <!-- 顶部信息 / 操作栏：跟抓图页同款，画师/角色 token 可点复制 -->
      <div class="viewer-toolbar fav-viewer-toolbar" :class="{ 'is-hidden': !viewerToolbarVisible }">
        <div class="viewer-toolbar-info">
          <div class="viewer-meta-block">
            <span class="viewer-meta-label">画师</span>
            <template v-if="viewerArtistTokens.length">
              <button
                v-for="(token, i) in viewerArtistTokens"
                :key="`v-art-${i}-${token}`"
                class="meta-link author-link token-chip viewer-token-chip"
                :title="`点击复制：${token}`"
                @click="copyViewerToken(token)"
              >{{ token }}</button>
            </template>
            <span v-else class="muted compact-text" style="color: #ccc;">未知</span>
          </div>
          <div v-if="viewerCharacterTokens.length" class="viewer-meta-block">
            <span class="viewer-meta-label">角色</span>
            <button
              v-for="(token, i) in viewerCharacterTokens"
              :key="`v-char-${i}-${token}`"
              class="meta-link token-chip viewer-token-chip"
              :title="`点击复制：${token}`"
              @click="copyViewerToken(token)"
            >{{ favCharLabel(token) }}</button>
          </div>
          <span class="muted compact-text" style="color: #ccc; margin-left: auto;">{{ viewerItem?.date }}</span>
        </div>
        <div class="button-row compact viewer-actions">
          <button class="secondary" @click="stepViewer(-1)" :disabled="viewer.index <= 0">上一张</button>
          <button class="secondary" @click="stepViewer(1)" :disabled="viewer.index >= filteredImages.length - 1">下一张</button>
          <!-- 收藏页里所有图都已收藏，所以按钮显示「已收藏」+ active 态，点一下 = 取消收藏 -->
          <button
            class="viewer-fav-btn active"
            :disabled="!viewerItem"
            @click="viewerItem && removeImageFavorite(viewerItem)"
            title="取消图片收藏"
          >♥ 已收藏</button>
          <!-- 转GIF：跟抓图页 viewer 同款，仅当 .zip 且同目录还没 .gif 兄弟时出现 -->
          <button
            v-if="viewerItem?.filename?.toLowerCase().endsWith('.zip') && !viewerItem?.hasGifCompanion"
            class="secondary"
            @click="convertGif(viewerItem)"
            style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;"
            title="ZIP 动画转 GIF"
          >转GIF</button>
          <!-- 复制图片：跟抓图页 viewer 同款 -->
          <button class="secondary" @click="copyViewerImage" :disabled="!viewerItem?.local_path" title="复制图片到剪贴板，最长边不超过 2000px">复制图片</button>
          <button @click="editFromViewer" :disabled="!viewerItem" style="background: linear-gradient(135deg, var(--accent), var(--accent-deep)); border: none; color: white;">编辑图片</button>
        </div>
      </div>

      <!-- 始终显示的右上角信息小栏：跟抓图页同款 -->
      <div class="viewer-corner-info">
        <button class="viewer-corner-row viewer-corner-btn viewer-corner-close" @click="closeViewer" title="关闭大图（Esc）">× 关闭</button>
        <div class="viewer-corner-row viewer-corner-counter">
          <span class="viewer-corner-counter-label">第</span>
          <input
            class="viewer-jump-input viewer-corner-jump"
            type="number"
            min="1"
            :max="filteredImages.length"
            :value="viewer.index + 1"
            @keyup.enter="onViewerJump($event)"
            @change="onViewerJump($event)"
            title="输入并回车跳转到指定张数"
          />
          <span class="viewer-corner-counter-label">/ {{ filteredImages.length }} 张</span>
          <span v-if="(viewerItem?.score || 0) > 0" class="viewer-score">★ {{ viewerItem.score }}</span>
          <span v-if="(viewerItem?.fav_count || 0) > 0" class="viewer-fav">♥ {{ viewerItem.fav_count }}</span>
        </div>
        <button
          class="viewer-corner-row viewer-corner-btn"
          @click="toggleViewerFitMode"
          :title="viewer.fitMode === 'fit' ? '当前：适应窗口，点击切换为原始大小' : '当前：原始大小，点击切换为适应窗口'"
        >{{ viewer.fitMode === 'fit' ? '原始大小' : '适应窗口' }}</button>
        <button
          class="viewer-corner-row viewer-corner-btn"
          :class="{ 'is-active': viewer.toolbarPinned }"
          @click="toggleViewerToolbarPin"
          :title="viewer.toolbarPinned ? '已固定信息栏，点击取消固定（恢复鼠标悬浮显示）' : '固定信息栏（默认悬浮显示）'"
        >{{ viewer.toolbarPinned ? '已固定' : '固定' }}</button>
      </div>

      <!-- 左右切换箭头：跟抓图页同款 -->
      <button
        v-show="viewer.index > 0"
        class="viewer-nav-arrow viewer-nav-prev"
        @click.stop="stepViewer(-1)"
        title="上一张 (←)"
        aria-label="上一张"
      >
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 18 9 12 15 6"></polyline>
        </svg>
      </button>
      <button
        v-show="viewer.index < filteredImages.length - 1"
        class="viewer-nav-arrow viewer-nav-next"
        @click.stop="stepViewer(1)"
        title="下一张 (→)"
        aria-label="下一张"
      >
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"></polyline>
        </svg>
      </button>

      <div class="viewer-stage" :class="{ 'is-fit': viewer.fitMode === 'fit' }" @wheel="onViewerWheel" @click.self="closeViewer">
        <div class="viewer-image-wrap" :class="{ 'is-fit': viewer.fitMode === 'fit' }" :style="{ zoom: viewer.zoom }">
          <video
            v-if="viewer.imageUrl && viewerIsVideo"
            class="viewer-image"
            :class="{ 'is-loaded': viewerImageLoaded }"
            :src="viewer.imageUrl"
            controls
            autoplay
            preload="metadata"
            @loadeddata="viewerImageLoaded = true"
          />
          <img
            v-else-if="viewer.imageUrl"
            class="viewer-image"
            :class="{ 'is-loaded': viewerImageLoaded }"
            :src="viewer.imageUrl"
            :alt="viewerItem?.filename || 'preview'"
            @load="viewerImageLoaded = true"
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
/* 收藏页内的「移除收藏」红心按钮，固定在缩略图右上角。
   收藏页里所有图都已在收藏里（class 上永远带 active），hover 时不能再换一种更浅的粉
   —— 那会让「已收藏」的颜色身份瞬间失忆。这里把 :hover 的 background 变化去掉，
   改用 filter 微暗提示交互，跟抓图页 .img-fav-toggle.active:hover 同款视觉语言
   （去掉 scale 放大，跟其他普通按钮的悬浮行为保持一致） */
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
  transition: background 0.2s, box-shadow 0.2s, filter 0.2s;
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  box-shadow: 0 2px 8px rgba(209, 40, 105, 0.5);
}
.favorites-images-panel .img-fav-toggle:hover {
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  filter: brightness(0.92) saturate(1.08);
  box-shadow: 0 4px 12px rgba(209, 40, 105, 0.6);
  /* 父规则里 transition 还包含 background 0.2s —— 显式去掉，
     防止已收藏态悬浮时从粉→浅粉→粉 的过渡闪一下。 */
  transition: box-shadow 0.2s, filter 0.2s;
}

/* 缩略图容器 + 骨架屏 + 淡入：跟抓图页同款，避免两个 tab 的视觉语言割裂。
   抓图页的同名样式在 CrawlerPage 的 <style scoped> 里，跨组件不共享，所以这里抄一份 */
.favorites-images-panel .thumb-wrap {
  position: relative;
  width: 100%;
  aspect-ratio: 1 / 1;
  line-height: 0;
  overflow: hidden;
}
.favorites-images-panel .thumb-wrap > .thumb {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.favorites-images-panel .thumb-wrap .thumb {
  opacity: 0;
  transition: opacity 0.32s ease;
}
.favorites-images-panel .thumb-wrap .thumb.is-loaded {
  opacity: 1;
}
.favorites-images-panel .thumb-skeleton {
  position: absolute;
  inset: 0;
  background: linear-gradient(100deg, #eef1f8 30%, #f6f8fd 50%, #eef1f8 70%);
  background-size: 220% 100%;
  animation: fav-thumb-shimmer 1.25s ease-in-out infinite;
  pointer-events: none;
  z-index: 1;
}
.favorites-images-panel .thumb-wrap.is-broken .thumb-skeleton {
  animation: none;
  background: linear-gradient(135deg, #2a2f3a, #1a1d24);
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.45);
  font-size: 12px;
  letter-spacing: 0.1em;
  font-weight: 700;
}
.favorites-images-panel .thumb-wrap.is-broken .thumb-skeleton::after {
  content: 'NO PREVIEW';
}
@keyframes fav-thumb-shimmer {
  0% { background-position: 140% 0; }
  100% { background-position: -40% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .favorites-images-panel .thumb-skeleton { animation: none; }
  .favorites-images-panel .thumb-wrap .thumb { transition: none; }
}

/* 抓图页同款「动画 / 视频」中心水印 */
.favorites-images-panel .video-format-watermark {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 4;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  pointer-events: none;
  backdrop-filter: blur(4px);
}
.favorites-images-panel .video-format-watermark .format-icon {
  width: 22px;
  height: 22px;
  color: #fff;
}
.favorites-images-panel .video-format-watermark .format-text {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.12em;
  line-height: 1;
  color: #fff;
}

/* 抓图页同款「分级角标」，左下角 */
.favorites-images-panel .rating-badge {
  position: absolute;
  left: 6px;
  bottom: 6px;
  z-index: 3;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 4px;
  pointer-events: none;
  background: rgba(0, 0, 0, 0.55);
}
.favorites-images-panel .rating-badge.rating-e { background: rgba(220, 50, 50, 0.9); }
.favorites-images-panel .rating-badge.rating-q { background: rgba(220, 150, 40, 0.9); }
.favorites-images-panel .rating-badge.rating-s { background: rgba(50, 160, 90, 0.9); }

/* 按钮行贴底，跟抓图页 .card-actions 一致：
   缩略图固定 1:1，按钮行高度固定，剩余空间塞到上面让按钮贴底 */
.favorites-images-panel .image-card .card-actions {
  padding: 8px;
  margin-top: auto;
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
  background: rgba(30, 41, 82, 0.35);
  color: #fff;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.search-clear-btn:hover { background: rgba(157, 44, 44, 0.7); }

/* ---------------- 大图 viewer 样式（对齐抓图页） ----------------
   全局 style.css 已经给了 .viewer-overlay / .viewer-toolbar / .viewer-stage /
   .viewer-image / .viewer-nav-arrow 这些基础骨架，剩下组件级（meta block、token chip、
   fav btn、corner info、按钮行配色等）在 CrawlerPage 的 <style scoped> 里，跨组件不共享，
   所以收藏页这里抄一份。 */
.fav-viewer-toolbar {
  /* 跟抓图页 .viewer-toolbar 同款（要覆盖全局的 pill 形状）：
     - 列布局：meta 行 + actions 行
     - 圆角 18px（不是全局的 999px 药丸）
     - min-width 480px：跟抓图页一致（之前是 600px，比抓图页宽一截显得格格不入）
     - max-height 让 chips 多时不撑爆屏幕，而是内部滚动 */
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
  max-width: 92vw;
  min-width: 480px;
  max-height: min(46vh, 390px);
  border-radius: 18px;
  padding: 12px 18px;
}
.fav-viewer-toolbar .viewer-toolbar-info {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 4px;
}
.fav-viewer-toolbar .viewer-actions {
  flex-wrap: nowrap;
  justify-content: flex-end;
}
/* 抓图页同款 action 按钮尺寸：上一张 / 下一张 / 已收藏 / 转GIF / 复制图片 / 编辑图片
   7 个按钮需要 padding 8px 16px + font-size 13.5px + min-height 36px 才能稳定装下一行。
   之前 6px 10px / 12px 是临时迁就 7 个旧按钮的（包含原帖/本地/关闭），现在跟抓图页对齐 */
.fav-viewer-toolbar .viewer-actions > button {
  flex: 0 0 auto;
  white-space: nowrap;
  padding: 8px 16px;
  font-size: 13.5px;
  min-height: 36px;
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
  margin-right: 2px;
}
.viewer-token-chip {
  background: rgba(255, 255, 255, 0.92);
  color: var(--ink);
  font-size: 12px;
}
.viewer-token-chip:hover {
  background: #fff;
  color: var(--accent-deep);
  border-color: rgba(79, 118, 224, 0.5);
}
.viewer-fav-btn {
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: #ffd699;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.2s, color 0.2s, box-shadow 0.2s, border-color 0.2s, filter 0.2s;
}
.viewer-fav-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.2);
}
/* 已收藏态的 hover：保持红色身份（不被白底洗成"未选中"感）。
   显式 lock 住 background = 粉色渐变 + override transition 去掉 background 项，
   避免父规则 transition: background 0.2s 启动「粉→白→粉」过渡出现「按钮变无色」的闪烁。
   顺便把 box-shadow 加强一档，让悬浮时粉色外光晕更明显，提示「点一下就取消收藏」
   （去掉 scale 放大，跟其他普通按钮的悬浮行为保持一致） */
.viewer-fav-btn.active:hover:not(:disabled) {
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  filter: brightness(0.9) saturate(1.1);
  box-shadow: 0 0 0 1px rgba(209, 40, 105, 0.5), 0 8px 26px rgba(209, 40, 105, 0.6);
  transition: box-shadow 0.2s, border-color 0.2s, filter 0.2s;
}
.viewer-fav-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.viewer-fav-btn.active {
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  color: #fff;
  border-color: transparent;
  box-shadow: 0 0 0 1px rgba(209, 40, 105, 0.3), 0 6px 22px rgba(209, 40, 105, 0.45);
}

/* 始终显示的右上角信息小栏（跟抓图页同款） */
.viewer-corner-info {
  position: absolute;
  top: 20px;
  right: 20px;
  z-index: 45;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  pointer-events: none;
}
.viewer-corner-row {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 6px 12px;
  background: rgba(0, 0, 0, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  color: #fff;
  font-size: 12.5px;
  font-weight: 600;
  backdrop-filter: blur(8px);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
  white-space: nowrap;
}
.viewer-corner-counter {
  padding: 5px 12px;
  opacity: 0.35;
  transition: opacity 0.18s ease;
}
.viewer-corner-counter:hover { opacity: 1; }
.viewer-corner-btn.viewer-corner-close {
  align-self: flex-end;
  background: rgba(125, 32, 32, 0.66);
  border-color: rgba(255, 180, 180, 0.34);
  opacity: 0.58;
  transition: opacity 0.18s ease, background 0.18s ease;
  cursor: pointer;
  font-family: inherit;
}
.viewer-corner-btn.viewer-corner-close:hover {
  opacity: 1;
  background: rgba(157, 44, 44, 0.9);
}
.viewer-corner-counter-label {
  color: rgba(255, 255, 255, 0.75);
  font-weight: 500;
  font-size: 12px;
}
.viewer-corner-jump {
  width: 48px;
  padding: 2px 6px;
  font-size: 12px;
  text-align: center;
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 6px;
}
.viewer-corner-btn {
  cursor: pointer;
  transition: background 0.18s, border-color 0.18s, color 0.18s, opacity 0.18s ease;
  font-family: inherit;
  opacity: 0.35;
}
.viewer-corner-btn:hover {
  background: rgba(0, 0, 0, 0.75);
  border-color: rgba(255, 255, 255, 0.35);
  opacity: 1;
}
.viewer-corner-btn.is-active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  border-color: transparent;
  color: #fff;
  box-shadow: 0 4px 16px rgba(79, 118, 224, 0.45);
  opacity: 1;
}

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
.fav-tab:hover { background: rgba(99, 102, 241, 0.1); }
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
.group-item:hover { background: rgba(99, 102, 241, 0.09); }
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

/* ⋮ 触发器：默认透明 + 不可点击，hover / focus / is-open 时淡入可见。
   两层修复叠加：
   1) 用 opacity + visibility + pointer-events 替换 display: none 切换，
      避免隐藏时从布局消失、被下层 .group-item 误点击穿透，以及 display 硬切造成的「跳」。
   2) class="ghost group-menu-trigger" 会被全局 button.ghost 覆盖 background / color /
      border-color / box-shadow，被全局 button:hover:not(:disabled) 覆盖 filter / box-shadow，
      被全局 button:active:not(:disabled) 覆盖 transform: translateY(1px) —— 后者
      会覆盖掉本地的 translateY(-50%) 居中偏移，导致点击瞬间按钮「下移 1px」再回弹，
      这才是用户看到的「抖动 / 点不到」的真凶。
      仿照 .viewer-nav-arrow 的写法，用 :not(:disabled) 显式抬升特异性到 (0,3,1)，
      覆盖全局 :active 的 transform 与 filter，按下时维持居中、不再位移。 */
.group-menu-trigger {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  display: inline-flex;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.95);
  color: var(--ink);
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  z-index: 3;
  align-items: center;
  justify-content: center;
  font-family: inherit;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity 0.15s ease, visibility 0.15s ease,
              background 0.15s, color 0.15s, border-color 0.15s;
}
.group-item-wrap:hover .group-menu-trigger,
.group-menu-trigger:focus,
.group-menu-trigger.is-open {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
}
.group-menu-trigger:hover {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border-color: transparent;
  /* 抑制全局 button:hover:not(:disabled) 的 filter: brightness(0.93)，
     保持本地 hover 时显示的渐变背景原色，不被「泛灰一档」干扰 */
  filter: none;
}
.group-menu-trigger.is-open {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border-color: transparent;
  filter: none;
}
/* 核心修复：覆盖全局 button:active:not(:disabled) 的 transform: translateY(1px)，
   否则按下瞬间 -50% 居中偏移被全局 1px 下移覆盖，按钮「跳」到非居中位置，松开又跳回。
   specificity (0,3,1) > 全局 (0,1,1)，稳赢。同时关掉全局 filter: brightness(0.88)。 */
.group-menu-trigger:active:not(:disabled) {
  transform: translateY(-50%);
  filter: none;
}
.group-menu-trigger:disabled { opacity: 0.5; cursor: not-allowed; }

/* ⋮ 下拉菜单：触发器右下展开，document 点击外部关闭 */
.group-menu {
  position: absolute;
  right: 6px;
  top: calc(50% + 14px);
  z-index: 5;
  min-width: 110px;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.group-menu-item {
  padding: 6px 10px;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: var(--ink);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s;
}
.group-menu-item:hover { background: rgba(99, 102, 241, 0.1); }
.group-menu-item.danger { color: #9d2c2c; }
.group-menu-item.danger:hover { background: rgba(157, 44, 44, 0.12); }

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
  /* 两列网格：宽屏 2 列，窄屏回退 1 列。
     之前单列每行只放一个画师/角色，左半边空一大块；改两列后密度自然翻倍 */
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  padding-right: 4px;
  align-content: start;
}
@media (max-width: 720px) {
  /* 容器宽度 < 720px 时一列也放得下两列了，会被挤变形；降回 1 列 */
  .favorites-grid { grid-template-columns: minmax(0, 1fr); }
}
.artist-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.7);
  content-visibility: auto;
  contain-intrinsic-size: 100% 88px;
}
.artist-card .artist-header {
  /* 第一行：画师名（占满剩余空间）+ 右侧动作按钮组 */
  display: flex;
  align-items: stretch;
  gap: 6px;
  width: 100%;
  flex: 0 0 auto;
}
.artist-card .artist-name {
  /* 名字在 header 左侧占满剩余空间，方便点击复制 */
  flex: 1 1 auto;
  min-width: 0;
  text-align: left;
}
.artist-card .artist-groups {
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
  overflow: hidden;
}
.artist-card .artist-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 4px;
}
.artist-card .artist-actions .action-btn {
  padding: 4px 8px;
  font-size: 11px;
  line-height: 1.2;
  white-space: nowrap;
  border-radius: 6px;
}
.artist-card .artist-actions .action-btn-danger {
  color: #9d2c2c;
}

/* 行内编辑面板：替代原本的「编辑所属分组」modal */
.artist-edit-panel {
  flex: 0 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid rgba(99, 102, 241, 0.35);
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.06), rgba(99, 102, 241, 0.02));
}
.edit-panel-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0.02em;
}
.edit-panel-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-height: 24px;
}
.edit-group-chip {
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--soft);
  color: var(--muted);
  border: 1px solid transparent;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s, color 0.15s, border-color 0.15s, transform 0.1s;
  font-family: inherit;
}
.edit-group-chip:hover:not(:disabled) {
  background: rgba(99, 102, 241, 0.12);
  color: var(--accent-deep);
}
.edit-group-chip.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border-color: transparent;
  box-shadow: 0 2px 6px rgba(99, 102, 241, 0.3);
}
.edit-group-chip.active:hover:not(:disabled) {
  /* 已加入态的 hover 提示：点一下 = 移出 */
  filter: brightness(0.92);
  transform: scale(1.04);
}
.edit-group-chip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.edit-panel-empty {
  font-size: 11px;
  color: var(--muted);
  font-style: italic;
}
.edit-panel-divider {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
  margin: 2px 0;
}
.edit-panel-divider::before,
.edit-panel-divider::after {
  content: '';
  flex: 1 1 auto;
  border-top: 1px dashed var(--line);
}
.edit-panel-new {
  display: flex;
  gap: 4px;
  align-items: stretch;
}
.edit-panel-input {
  flex: 1 1 auto;
  min-width: 0;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--ink);
  font-size: 12px;
  font-family: inherit;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.edit-panel-input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.15);
}
.edit-panel-add-btn {
  flex: 0 0 auto;
  padding: 4px 10px;
  border: none;
  border-radius: 6px;
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
  font-family: inherit;
  transition: filter 0.15s, transform 0.1s;
}
.edit-panel-add-btn:hover:not(:disabled) {
  filter: brightness(1.05);
  transform: translateY(-1px);
}
.edit-panel-add-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.group-tag-empty {
  font-size: 11px;
  color: var(--muted);
  font-style: italic;
  padding: 2px 4px;
}
.artist-name {
  width: 100%;
  text-align: left;
  background: var(--surface-muted);
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
  background: rgba(255, 255, 255, 0.98);
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
.fav-rename-input {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  color: var(--ink);
  font-size: 14px;
  font-family: inherit;
  box-sizing: border-box;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.fav-rename-input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}
.fav-rename-old {
  padding: 8px 10px;
  border: 1px dashed var(--line);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.5);
  color: var(--muted);
  font-size: 13px;
  font-family: Consolas, monospace;
  word-break: break-all;
}
.fav-rename-error {
  padding: 8px 10px;
  background: rgba(248, 215, 218, 0.6);
  border: 1px solid #f5c6cb;
  border-radius: 8px;
  color: #721c24;
  font-size: 12px;
}

/* 通用 confirm modal */
.fav-confirm-message {
  padding: 6px 2px;
  color: var(--ink);
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}
.fav-confirm-modal.is-danger .fav-modal-head h3 {
  color: #9d2c2c;
}
.fav-confirm-danger {
  background: linear-gradient(135deg, #d12869, #9d2c2c) !important;
  border-color: transparent !important;
  color: #fff !important;
  font-weight: 700;
}
.fav-confirm-danger:hover:not(:disabled) {
  filter: brightness(0.94) saturate(1.05);
  box-shadow: 0 4px 14px rgba(157, 44, 44, 0.45) !important;
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

/* Lightweight anime theme */
.fav-top-tabs {
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.72);
  box-shadow: 0 6px 18px rgba(30, 41, 82, 0.06);
}
.fav-tab { background: transparent; color: var(--muted); box-shadow: none; }
.fav-tab:hover { background: var(--surface-muted); color: var(--ink); box-shadow: none; }
.fav-tab.active { background: var(--accent-gradient); color: #fff; box-shadow: 0 5px 12px rgba(var(--accent-rgb), 0.18); }
.favorites-side,
.favorites-main,
.favorites-images-panel { border-color: var(--line); background: rgba(255, 255, 255, 0.92); }
.group-item,
.artist-card { border-color: var(--line); background: var(--surface-muted); }
.group-item:hover,
.artist-card:hover { border-color: rgba(var(--violet-rgb), 0.24); background: #faf9ff; }
.group-item.active { background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.14), rgba(var(--violet-rgb), 0.12)); color: var(--accent-deep); }
.artist-name { color: var(--ink); }
.group-tag { background: var(--soft-violet); color: var(--accent-deep); }
.fav-modal { border: 1px solid var(--line); background: rgba(255, 255, 255, 0.98); box-shadow: 0 24px 60px rgba(39, 34, 67, 0.22); }
.fav-checkbox-list { background: var(--surface-muted); }
</style>
