<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue';
import GalleryCalendar from './GalleryCalendar.vue';
import TaskDatePicker from './TaskDatePicker.vue';
import TutorialsModal from './crawler/TutorialsModal.vue';
import RefreshRangeModal from './crawler/RefreshRangeModal.vue';
import ArtistFavoriteModal from './crawler/ArtistFavoriteModal.vue';
import CharacterFavoriteModal from './crawler/CharacterFavoriteModal.vue';
import BrowseOverlay from './crawler/BrowseOverlay.vue';
import SearchHistoryDropdown from './SearchHistoryDropdown.vue';
import TranslationModal from './crawler/TranslationModal.vue';
import TranslateDetailModal from './crawler/TranslateDetailModal.vue';
import SelectionListModal from './crawler/SelectionListModal.vue';
import BrowseSelectionModal from './crawler/BrowseSelectionModal.vue';
import CryptoToolModal from './crawler/CryptoToolModal.vue';
import MergeViewerDataModal from './crawler/MergeViewerDataModal.vue';
import { parsePastedIds } from '../utils/idCodec.js';

const emit = defineEmits(['edit-image', 'caption-image']);

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

// 代理开关：运行时在「走代理 / 直连」间切换。解决「开代理启动后端→后端 PROXIES 定格→
// 关掉代理软件后下载仍走死代理报错」。直接打后端 /api/set_proxy 即时改 danbooru_api.PROXIES。
// 默认值仅占位，onMounted 里用后端 /api/proxy_state 的真实状态校正。
const useProxy = ref(true);
async function loadProxyState() {
  try {
    const res = await fetch('http://127.0.0.1:8000/api/proxy_state');
    const data = await res.json();
    if (data && typeof data.use_proxy === 'boolean') useProxy.value = data.use_proxy;
  } catch (e) { /* 后端未就绪时静默，onMounted 在 ensureService 后再调一次 */ }
}
async function toggleProxy() {
  const next = !useProxy.value;
  try {
    const res = await fetch('http://127.0.0.1:8000/api/set_proxy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ use_proxy: next })
    });
    const data = await res.json();
    useProxy.value = !!data.use_proxy;
    habits.useProxy = useProxy.value;
    localStorage.setItem('crawlerHabits', JSON.stringify(habits));
    if (data.use_proxy && data.alive === false) {
      showToast('已切到「走代理」，但探测代理端口不可达，请确认代理软件已开启', 'warning');
    } else if (data.use_proxy) {
      showToast('已切到「走代理」下载', 'success');
    } else {
      showToast('已切到「直连」下载（不使用代理）', 'success');
    }
  } catch (e) {
    showToast('切换代理失败：' + (e.message || e), 'error');
  }
}

// 初始 mode 也要用下面这一份来推导 startPage/endPage 初始值：watch(form.mode)
// 是非 immediate 的，启动时 mode 不会"变"，所以 form 初始值必须按 mode 选
// 对应的 habits 字段，否则会出现「打开就在 popular 却看到 rank 的 1-40 页」这种串台。
// 旧实现无脑写 habits.rank_start/_end，所以才把 popular 的 1-50 盖成 rank 的 1-40。
// 与下面 watch 的默认值保持一致：rank→16, popular→35, tags→5。
const _initialMode = (habits.mode === 'collect_ids' || habits.mode === 'download_ids' || habits.mode === 'popular_range')
  ? 'rank'
  : (habits.mode || 'rank');
const _initialStartDefault = _initialMode === 'popular' ? 1 : _initialMode === 'tags' ? 1 : 1;
const _initialEndDefault = _initialMode === 'popular' ? 35 : _initialMode === 'tags' ? 5 : 16;
const form = ref({
  startPage: habits[`${_initialMode}_start`] || _initialStartDefault,
  endPage: habits[`${_initialMode}_end`] || _initialEndDefault,
  // 用 typeof 判断而不是 || ：用户可能故意把过滤标签清空（=不过滤任何 tag），
  // 那种情况下应保留空串而不是回退到默认 "furry, futanari"
  tags: typeof habits.tags === 'string' ? habits.tags : 'furry, futanari, guro',
  mode: _initialMode,
  targetDate: '',
  startDate: '',
  endDate: '',
  idsText: '',
  tagQuery: typeof habits.tagQuery === 'string' ? habits.tagQuery : '',
  tagSource: habits.tagSource === 'gelbooru' ? 'gelbooru' : 'danbooru',
  // 「日期热门 / 日期范围」合到一个 mode 里，下面用 dateRange 决定单日 vs 范围
  dateRange: false,
  // popular·按ID下载 专用：True（默认）= log.json 已记录的 id 跳过；False = 强制重下（补齐 50 页场景）
  skipLogged: true
});
// 排行榜类的子操作：两档（download=两阶段 / collect_only=仅收ID）。与 popularAction 互不干扰。
// 历史上有过第三档 'download_by_ids'，但「按ID下载」语义上是针对日期 folder 的，
// 移到 popularAction 下，rank 不再持有这个状态。
const rankAction = ref('download');
// 日期热门类的子操作：四档
//   - download=两阶段（默认）
//   - collect_only=仅收ID
//   - recover=补全/补齐（仅单日）
//   - download_by_ids=按ID下载（仅单日；针对日期 folder，与 rank 无关）
// 与 rankAction 互不干扰（不同 mode 各自管自己的子操作状态）。
const popularAction = ref('download');
// 「按ID下载」子操作迁移到 popularAction 下后，全局条件统一走这个 computed：
// 包括日期同步、ID 粘贴区、下载策略行、resolveActualMode 等。
// 放在 popularAction 后面、下面的 form↔gallery watch 前面：
// watch 注册时会同步跑 getter 建立依赖 + immediate=true 的 callback，
// 若 isDownloadByIdsMode 还在 TDZ 会抛 ReferenceError 把组件挂载搞挂。
const isDownloadByIdsMode = computed(() =>
  form.value.mode === 'popular' && popularAction.value === 'download_by_ids'
);
// 下载协程数：夹到 [1, 16]，默认 4。改动会持久化到 habits，下个会话自动恢复
const downloadConcurrency = ref(
  Number.isFinite(habits.downloadConcurrency) && habits.downloadConcurrency >= 1 && habits.downloadConcurrency <= 16
    ? habits.downloadConcurrency : 4
);
watch(downloadConcurrency, (v) => {
  const clamped = Math.max(1, Math.min(16, Math.round(Number(v) || 4)));
  if (clamped !== v) downloadConcurrency.value = clamped;
  habits.downloadConcurrency = downloadConcurrency.value;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
});

watch(() => form.value.mode, (newMode) => {
  // 切 mode 会触发 applyDefaultDatesForMode 默认填"画廊日期 / 昨天"，
  // 这一帧内的 form 变化不应再触发 form→gallery 同步。
  isModeSwitching = true;
  if (newMode === 'rank') {
    form.value.startPage = habits.rank_start || 1;
    form.value.endPage = habits.rank_end || 16;
  } else if (newMode === 'popular') {
    form.value.startPage = habits.popular_start || 1;
    form.value.endPage = habits.popular_end || 35;
  } else if (newMode === 'tags') {
    form.value.startPage = habits.tags_start || 1;
    form.value.endPage = habits.tags_end || 5;
  }
  applyDefaultDatesForMode(newMode);
  recentStartPages.value = loadRecentPages(newMode, 'start');
  recentEndPages.value = loadRecentPages(newMode, 'end');
  nextTick(() => { isModeSwitching = false; });
});

// ---------------- 日期工具 + 「默认昨天」 ----------------
// popular / popular_range 在日期留空时默认填「昨天」（用户每日例行：早上跑昨天的热门）。
function todayString() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function yesterdayString() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
// 队列里把日期存成相对令牌，跑时再解析成「当时的」昨天/今天 ——
// 这样同一个队列项每天跑都对应新的昨天，而不是被钉死在某个旧日期。
function resolveDateToken(v) {
  if (v === '@yesterday') return yesterdayString();
  if (v === '@today') return todayString();
  return v || '';
}
function tokenizeDate(v) {
  if (!v) return '';
  if (v === yesterdayString()) return '@yesterday';
  if (v === todayString()) return '@today';
  return v;
}
function dateTokenLabel(v) {
  if (v === '@yesterday') return `昨天(${yesterdayString()})`;
  if (v === '@today') return `今天(${todayString()})`;
  return v || '今天';
}
function applyDefaultDatesForMode(mode) {
  if (mode === 'popular') {
    // 切到「日期热门」时优先用画廊当前选中日期作为默认目标日期；
    // 画廊为空 / 是 tag 文件夹时才回退到昨天。修复用户报修的"日期热门目标日期
    // 不跟随画廊日历"问题——之前无条件写 yesterday 会和画廊当前日期错位，
    // 必须先点画廊的"上一天/下一天"才能让 gallery→form watch 把 form 拉回画廊日期。
    // 用局部 regex 不用 ISO_DATE：ISO_DATE 在文件靠后位置才声明，避免 TDZ 报错。
    const galleryDate = gallery.value.selectedDate;
    const galleryHasDate = !!galleryDate && /^\d{4}-\d{2}-\d{2}$/.test(galleryDate);
    const defaultDate = galleryHasDate ? galleryDate : yesterdayString();
    if (form.value.dateRange) {
      if (!form.value.startDate) form.value.startDate = defaultDate;
      if (!form.value.endDate) form.value.endDate = defaultDate;
    } else if (!form.value.targetDate) {
      form.value.targetDate = defaultDate;
    }
  }
}

// ---------------- 起始页 / 结束页「最近使用」历史 ----------------
// 每个模式（rank / popular / popular_range / collect_ids）按 start / end 各自存最近 2 个值
// 历史在 startTask 时写入；删除按钮立即从 habits.recentPages 中移除
function loadRecentPages(mode, field) {
  const rp = habits.recentPages || {};
  const list = rp[mode] && rp[mode][field];
  return Array.isArray(list) ? list.filter(n => Number.isFinite(n) && n > 0) : [];
}
const recentStartPages = ref(loadRecentPages(form.value.mode, 'start'));
const recentEndPages = ref(loadRecentPages(form.value.mode, 'end'));

function pushRecentPage(mode, field, value) {
  if (!Number.isFinite(value) || value <= 0) return;
  const rp = { ...(habits.recentPages || {}) };
  const prev = rp[mode] && Array.isArray(rp[mode][field]) ? rp[mode][field] : [];
  const cur = prev.filter(v => v !== value);
  cur.unshift(value);
  while (cur.length > 2) cur.pop();
  rp[mode] = { ...(rp[mode] || {}), [field]: cur };
  habits.recentPages = rp;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
  if (mode === form.value.mode) {
    if (field === 'start') recentStartPages.value = cur;
    else recentEndPages.value = cur;
  }
}

function applyRecentPage(field, value) {
  if (field === 'start') form.value.startPage = value;
  else form.value.endPage = value;
}

// 「补全/补齐」子操作专属：原实现硬编码 50 页（用户已确认这是合理默认）。
// 切到该子操作时，若 endPage<50 提到 50（不覆盖用户主动设的更大值），
// 保持与原 `recoverPopular()` 函数行为一致。
function onPickPopularRecover() {
  popularAction.value = 'recover';
  if ((Number(form.value.endPage) || 0) < 50) {
    form.value.endPage = 50;
  }
}

// 「按ID下载」子操作专属：仅在单日模式有效，UI 上对应按钮 v-if 隐藏；
// 这里再多一道防御：watch dateRange=true 时若 popularAction 落在单日专属的子操作
// （recover / download_by_ids），自动重置成 'download'。否则 resolveActualMode
// 会把 popular_range + download_by_ids 错位翻译成 popular_range 跑。
watch(() => form.value.dateRange, (newVal) => {
  if (newVal && (popularAction.value === 'recover' || popularAction.value === 'download_by_ids')) {
    popularAction.value = 'download';
  }
});

function deleteRecentPage(mode, field, value) {
  const rp = { ...(habits.recentPages || {}) };
  const prev = rp[mode] && Array.isArray(rp[mode][field]) ? rp[mode][field] : [];
  const cur = prev.filter(v => v !== value);
  rp[mode] = { ...(rp[mode] || {}), [field]: cur };
  habits.recentPages = rp;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
  if (mode === form.value.mode) {
    if (field === 'start') recentStartPages.value = cur;
    else recentEndPages.value = cur;
  }
}

// 过滤标签单独写一个 watcher，确保即使用户清空成 "" 也立刻写盘
watch(() => form.value.tags, (v) => {
  habits.tags = typeof v === 'string' ? v : '';
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
});

// tag 查询串也单独存：用户切到 popular_range / rank 后再切回 tags 应能恢复
watch(() => form.value.tagQuery, (v) => {
  habits.tagQuery = typeof v === 'string' ? v : '';
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
});
watch(() => form.value.tagSource, (v) => {
  habits.tagSource = v === 'gelbooru' ? 'gelbooru' : 'danbooru';
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
  availableDateFolders: [],
  // 标签文件夹：[{folder: "tag_xxx", display: "xxx"}] —— 与日期文件夹并行，由后端扫描 hot_pic 提供
  availableTags: [],
  today: '',
  images: [],
  search: '',
  filterFormat: 'all',
  filterRatings: [],   // 多选：['s','q','e'] 的子集；空数组 = 不筛选（显示全部分级）
  sortBy: habits.sortBy || 'default',
  // 每页张数：默认 15（笔记本屏）；大屏可调高。过高会一次渲染很多卡片，缩略图 IPC + 网格布局会变卡
  pageSize: habits.pageSize || 15,
  cardSize: habits.cardSize || 150,
  // 缩略图长边像素：默认 360（远小于原图，省解码/内存）；0 = 原图。点开看大图仍走原图。
  thumbSize: habits.thumbSize != null ? habits.thumbSize : 360,
  // 点开/切换大图是否联网刷新该图 score/收藏数：默认关，离线不报错；开关在工具栏「翻译」右侧
  refreshOnView: habits.refreshOnView ?? false,
  page: 1,
  // 库根目录列表（来自 main.cjs loadLibraryRoots()）：用于「合并 viewer_data」跨盘工具
  // 至少含一个默认项 { id: 'default', label: 'hot_pic', path: <绝对路径> }，外置盘按 library_roots.json 追加
  libraryRoots: []
});
// 初始模式若是 popular，进来就把日期填成昨天 / 画廊日期。
// 必须在 gallery 定义之后调：函数体内访问 gallery.value.selectedDate，
// const 在 TDZ 阶段会抛 ReferenceError。
applyDefaultDatesForMode(form.value.mode);
const showGalleryPanel = ref(habits.showGalleryPanel !== false);

watch(() => [gallery.value.sortBy, gallery.value.cardSize, gallery.value.thumbSize, gallery.value.refreshOnView, gallery.value.pageSize], () => {
  habits.sortBy = gallery.value.sortBy;
  habits.cardSize = gallery.value.cardSize;
  habits.thumbSize = gallery.value.thumbSize;
  habits.refreshOnView = gallery.value.refreshOnView;
  habits.pageSize = gallery.value.pageSize;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
});

watch(showGalleryPanel, (value) => {
  habits.showGalleryPanel = value;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
});

// ---------------- form ↔ gallery 双向日期同步 ----------------
// 目标：日期热门 / 日期范围 / 按ID下载 的目标日期 和 右侧 GalleryCalendar 当前日期 联动。
// 选一边改，另一边跟着跳；省去"下载较早年份时两边都要选"的来回。
//
// 防回环：任一方向写入后，另一方向 watch 触发时先比值，值已一致就直接 return，
// 不会出现 A→B→A→B 死循环。
//
// popular_range 驱动侧用 startDate（"下载较早年份" 的目标日就是区间起点）；
// tags / rank / collect_ids 不参与同步。
//
// 位置注意：必须放在 gallery ref 定义之后。
// watch 注册时会立即调一次 getter 建立依赖；若 gallery 还在 TDZ 会抛
// "Cannot access 'gallery' before initialization"，整个组件挂载失败。
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
let isModeSwitching = false;

// 右侧 GalleryCalendar → 左侧 form
watch(() => gallery.value.selectedDate, (newDate) => {
  if (!newDate) return;
  const mode = form.value.mode;
  // 「按ID下载」子操作：目标日期跟随右侧 GalleryCalendar，
  // 同时主动 fetch 新日期 folder 的待下载 ID 写回 idsText，
  // 解决"切到 B 日期 → 粘贴区仍是 A 的 ID"这个频繁补日期的痛点。
  // 必须放在最前面：原版 if/else if 链在 popular+单日 模式下第一个分支先匹配，
  // 导致这里成 dead code，「按ID下载」状态下切日期只会改 targetDate、不会同步 idsText，
  // 出现"日期 A 的 IDs 被下载到日期 B folder"的跨日期污染。
  if (isDownloadByIdsMode.value) {
    if (form.value.targetDate !== newDate) {
      form.value.targetDate = newDate;
      fetchCollectedIdsForDate(newDate, {
        onSuccess: (fetchedDate, payload) => {
          // 防竞态：用户在 fetch 期间又切了日期 → 丢弃过期结果，让新日期的 watch 接管
          if ((form.value.targetDate || '').trim() !== fetchedDate) return;
          form.value.idsText = payload.ids.join('\n');
          showToast(`已加载 ${fetchedDate} 的 ${payload.ids.length} 个待下载 ID`, 'success');
        },
        onEmpty: (fetchedDate) => {
          if ((form.value.targetDate || '').trim() !== fetchedDate) return;
          form.value.idsText = '';
          showToast(`${fetchedDate} folder 没有待下载 ID，可粘贴自定义 ID`, 'info');
        },
        onError: (fetchedDate, err) => {
          if ((form.value.targetDate || '').trim() !== fetchedDate) return;
          showToast(`加载 ${fetchedDate} 的 ID 失败：${err.message}`, 'error');
        },
      });
    }
    return;
  }
  if (mode === 'popular' && !form.value.dateRange) {
    if (form.value.targetDate !== newDate) form.value.targetDate = newDate;
  } else if (mode === 'popular' && form.value.dateRange) {
    if (form.value.startDate !== newDate) form.value.startDate = newDate;
  }
});

// 左侧 TaskDatePicker → 右侧 gallery
watch(() => [form.value.targetDate, form.value.startDate], ([newTarget, newStart]) => {
  if (isModeSwitching) return;
  const mode = form.value.mode;
  if (mode === 'popular') {
    const candidate = form.value.dateRange ? newStart : newTarget;
    if (candidate && ISO_DATE.test(candidate) && candidate !== gallery.value.selectedDate) {
      loadGallery(candidate);
    }
  } else if (isDownloadByIdsMode.value) {
    // 「按ID下载」目前没有自己的日期选择器，targetDate 由右侧 gallery 同步过来；
    // 这里保持单一来源：不再反向写 gallery，避免与上面 watch 互相回环。
  }
});

// 切到「按ID下载」组合时，主动把 form.targetDate 校准成 gallery 当前日期。
// 场景：用户先在 popular 模式选过日期，form.targetDate 残留了 popular 的值，
// 然后切到 popular+按ID下载 — gallery 没动，上面的 watch 不会触发，
// 残留值会让提示文案/hint 指向错误 folder。watcher 注册时会立即跑一次，
// 所以打开页面直接落在该组合时也会被校准到 gallery 的当前日期。
watch([() => form.value.mode, rankAction, popularAction], () => {
  // 复用一个助手：gallery 校准 + 拉取并回填 idsText（防竞态）
  const calibrateAndFetch = () => {
    const galleryDate = gallery.value.selectedDate;
    if (galleryDate && galleryDate !== form.value.targetDate) {
      form.value.targetDate = galleryDate;
    }
    const target = (form.value.targetDate || '').trim();
    if (target) {
      fetchCollectedIdsForDate(target, {
        onSuccess: (fetchedDate, payload) => {
          if ((form.value.targetDate || '').trim() !== fetchedDate) return;
          form.value.idsText = payload.ids.join('\n');
          showToast(`已加载 ${fetchedDate} 的 ${payload.ids.length} 个待下载 ID`, 'success');
        },
        onEmpty: (fetchedDate) => {
          if ((form.value.targetDate || '').trim() !== fetchedDate) return;
          form.value.idsText = '';
          showToast(`${fetchedDate} folder 没有待下载 ID，可粘贴自定义 ID`, 'info');
        },
        onError: (fetchedDate, err) => {
          if ((form.value.targetDate || '').trim() !== fetchedDate) return;
          showToast(`加载 ${fetchedDate} 的 ID 失败：${err.message}`, 'error');
        },
      });
    }
  };
  // 「按ID下载」组合（已迁到 popular 下）
  if (isDownloadByIdsMode.value) {
    calibrateAndFetch();
  }
}, { immediate: true });

const task = ref({
  isRunning: false,
  isStopping: false,
  isPaused: false,
  jobId: '',
  mode: '',                 // 后端 status.mode：rank / popular / tags / collect_ids / download_ids ...
  outcome: 'idle',
  errorMessage: '',
  // 进度条数据：替代原 logs / totalLogCount / maximized / hideSuccess / expandedLogIdx。
  // /api/status 每秒拉一次，task_download_ids 入口在 main.py 设 total，worker 累加 success / fail。
  progress: { total: 0, success: 0, fail: 0 },
  // 抓取 ID 阶段的页进度（按当前 scope 统计；total=0 时不渲染条）。
  // 后端 collect 循环里写 page_current / page_total，进入 download 时清零。
  // done = 已完整跑完的页数（不论成功失败），用于算出"成功 X 页"。
  pageProgress: { current: 0, total: 0, done: 0 },
  // 运行中实时失败页 / 失败图计数：来自 /api/status 的 failed_pages / failed_ids，
  // 让用户在不点击失败横幅时也能直观看到「当前已经有 N 页抓失败」，配 runningPhaseText 一起渲染。
  runningFailedPages: 0,
  runningFailedIds: 0,
  backendError: '',
  backendErrorExpanded: false,
  backendTail: []
});
// 进度条上方的阶段提示：只在任务运行/暂停/停止中显示。
// 区分依据是 task.progress.total —— 只有 task_download_ids 入口在 main.py 里设 total_planned，
// 所以 total > 0 等价于「已进入按 ID 下载阶段」；否则是「抓取 ID / 抓取页面列表」阶段。
// collect 阶段如果后端给了 page_total，会顺带显示「N/M 页」。
const runningPhaseText = computed(() => {
  if (!task.value.isRunning && !task.value.isPaused && !task.value.isStopping) return '';
  if (task.value.isStopping) return '正在停止…';
  if (task.value.isPaused) return '已暂停';
  if (task.value.progress.total > 0) return '正在下载…';
  const pp = task.value.pageProgress;
  if (pp && pp.total > 0) {
    return `正在抓取ID…（${pp.current}/${pp.total} 页）`;
  }
  return '正在抓取ID…';
});
// 实时失败计数：有失败时紧跟在阶段提示后面，拼成 "阶段 · ⚠ 失败 3 页 / 1 图" 形式。
// 每次 failed_pages 增长都会自动更新（依赖 task.runningFailedPages/Ids），点击会聚焦失败横幅。
const runningFailureHint = computed(() => {
  const pages = task.value.runningFailedPages || 0;
  const ids = task.value.runningFailedIds || 0;
  if (!pages && !ids) return '';
  const parts = [];
  if (pages) parts.push(`${pages} 页`);
  if (ids) parts.push(`${ids} 图`);
  return ` · ⚠ 失败 ${parts.join(' / ')}`;
});
// 抓 ID 阶段（collect）的页级成功/失败计数：done - failed = 成功页数。
// done 来自后端 page_done_count（grabber 返回后 +1，不论成功失败）；
// failed 来自后端 failed_pages 数组长度（仅 record_failed_page 后才计入）。
// 两个值都是从后端 /api/status 透出的权威数据，前端不再做本地累加，避免双源不一致。
const pageFetchSucceeded = computed(() => {
  const done = task.value.pageProgress?.done || 0;
  const failed = task.value.runningFailedPages || 0;
  return Math.max(0, done - failed);
});
// 失败页 / 失败图：后端 /api/status 透出 failed_pages（页级 [{folder, page}]）和
// failed_ids（图级 [{folder, ids:[...]}]）。不弹横幅 —— 用户通过「运行动态日志」
// 区里简洁的"需手动重试的页：5, 6"提示 + × 关闭 自己看自己决。
// 之前 buildRetryQueueItem 用 form.value.tags 而不是原任务 tags，已连带删除。

// 简洁的"需重试页范围"提示：失败页有变化时更新；用户点 × 关闭。
// 用 folder+pages 拼接的 signature 去重（task 内部同一组失败只刷一次）。
// dismissedSignatures 记住用户已关闭的组，同一 job 内不再反复刷出。
const retryPagesHint = ref({ show: false, signature: '', pages: [], text: '' });
const dismissedHintSignatures = new Set();
function showRetryPagesHint(report) {
  const sig = (report.folder || 'unknown') + '::' + report.pages.map(p => p.page).join(',');
  if (dismissedHintSignatures.has(sig)) return;
  retryPagesHint.value = {
    show: true,
    signature: sig,
    pages: report.pages,
    text: `需手动重试的页：${report.pages.map(p => p.page).join(', ')}（在入队面板配置同样 mode+pages 范围重新入队）`,
  };
}
function dismissRetryPagesHint() {
  if (retryPagesHint.value.signature) dismissedHintSignatures.add(retryPagesHint.value.signature);
  retryPagesHint.value = { show: false, signature: '', pages: [], text: '' };
}
const viewer = ref({
  open: false,
  key: '',            // 当前大图的稳定唯一键（localPath||filename）。用键而非索引锁定当前图，
                      // 下载时新图 unshift 进画廊也不会让「正在看的图」错位（标签/复制跟着变）。
  imageUrl: '',
  zoom: 1,
  fitMode: habits.viewerFitMode === 'actual' ? 'actual' : 'fit',
  toolbarPinned: habits.viewerToolbarPinned === true,
  toolbarHovered: false
});

const viewerToolbarVisible = computed(() => viewer.value.toolbarPinned || viewer.value.toolbarHovered);
const viewerToolbarRef = ref(null);
// viewer 主图淡入：src 变化时立刻置 false，@load 后置 true 触发 .is-loaded。
// 配合 CSS opacity 0→1 过渡，避免上一张切下一张时"啪"地一下换图。
const viewerImageLoaded = ref(false);
watch(() => viewer.value.imageUrl, () => { viewerImageLoaded.value = false; });



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
  const toolbarBottom = viewerToolbarRef.value?.getBoundingClientRect?.().bottom || 160;
  viewer.value.toolbarHovered = event.clientY <= Math.max(160, toolbarBottom + 18);
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
// 优先用 loadGallery 预存的 item.postId，缺失时回退到正则解析（旧路径/未规范化 item）
function getPostId(item) {
  return item && item.postId ? item.postId : extractPostId(item);
}
function isItemSelected(item) {
  const id = getPostId(item);
  return !!id && selection.value.ids.has(id);
}
function toggleItemSelection(item) {
  const id = getPostId(item);
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
// parsePastedIds / compressIds / decompressIds 已抽到 src/utils/idCodec.js

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

// ID 压缩工具（独立面板）的开关由父组件控制；具体加/解密逻辑抽到 CryptoToolModal.vue
const cryptoTool = ref({ open: false, input: '', output: '' });
function openCryptoTool() { cryptoTool.value.open = true; }
function closeCryptoTool() { cryptoTool.value.open = false; }
// 「把已选 ID 载入到输入框」是父组件才能做的（要读 selection.value.ids）
function loadSelectionToCryptoInput() {
  const ids = Array.from(selection.value.ids);
  if (!ids.length) { showToast('当前没有已选图片', 'warning'); return; }
  cryptoTool.value.input = ids.join(',');
}
const parsedPastedIds = computed(() => parsePastedIds(form.value.idsText));
const isPastedCompressed = computed(() => /dbids:[0-9a-z.]+/i.test(form.value.idsText || ''));
// 「按ID下载」子操作没有自己的日期选择器，日期由右侧 GalleryCalendar 同步过来。
// 提示文案统一从这里取：选了具体日期就亮出来，没选就兜底今天，避免出现
// 「将下载到今天的图库」跟实际 folder 不一致的迷惑。
const downloadByIdsTargetDateLabel = computed(() => {
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(form.value.targetDate || '') ? form.value.targetDate : '';
  return iso || todayString();
});
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
const pagePickerHost = ref(null);   // 分页栏页码组，作为浮层的定位锚点
const pagePickerStyle = ref({});    // 浮层 position:fixed 的实时坐标（见 positionPagePicker）
// 浮层 Teleport 到 body 后，用锚点元素的视口坐标把它 fixed 在原来的位置（向上展开、右对齐）。
// 这样它脱离了分页栏(.cr-pg-bar)的 overflow 裁剪与图片卡片的层叠上下文，永远浮在最上层。
function positionPagePicker() {
  const host = pagePickerHost.value;
  if (!host) return;
  const r = host.getBoundingClientRect();
  pagePickerStyle.value = {
    right: `${Math.max(8, window.innerWidth - r.right)}px`,
    bottom: `${window.innerHeight - r.top + 8}px`,
  };
}
watch(() => pagePicker.value.open, open => {
  if (open) {
    positionPagePicker(); // flush:'pre'——在浮层渲染前算好坐标，避免首帧闪到错误位置
    window.addEventListener('resize', positionPagePicker);
    window.addEventListener('scroll', positionPagePicker, true); // 画廊在内层容器滚动，需捕获阶段
  } else {
    window.removeEventListener('resize', positionPagePicker);
    window.removeEventListener('scroll', positionPagePicker, true);
  }
});

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
    const id = getPostId(list[i]);
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

// Toast：堆叠在程序顶部，置顶显示；多条同时挂出（不互相覆盖）。
// 每条独立倒计时关闭（默认 4.5s），点 X 立即关。
const toasts = ref([]);   // [{id, msg, type}]
let toastSeq = 0;

function showToast(msg, type = 'info') {
  const id = ++toastSeq;
  toasts.value.push({ id, msg, type });
  // 队列最多挂 6 条，再多就把最老的挤掉，避免遮挡程序头部太久
  while (toasts.value.length > 6) toasts.value.shift();
  setTimeout(() => dismissToast(id), 4500);
}

function dismissToast(id) {
  toasts.value = toasts.value.filter(t => t.id !== id);
}

const loadingGallery = ref(false);
const convertingZips = ref(false);   // 批量 zip→gif 进行中：避免重复点击
const clearingThumbCache = ref(false);  // 清缩略图缓存进行中
const galleryPendingDate = ref('');
let galleryLoadSequence = 0;
let committedGalleryDate = '';
let pollTimer = null;

const filteredLocalImages = computed(() => {
  const keyword = gallery.value.search.trim().toLowerCase();
  const format = gallery.value.filterFormat;
  const source = gallery.value.images;

  let result = source.filter(item => {
    if (format !== 'all') {
      const ext = (item.filename || '').split('.').pop().toLowerCase();
      if (format === 'zip' && !['zip', 'gif'].includes(ext)) return false;
      if (format === 'video' && !['mp4', 'webm', 'avi', 'mov', 'mkv'].includes(ext)) return false;
      if (format === 'image' && !['jpg', 'jpeg', 'png', 'webp', 'bmp', 'avif'].includes(ext)) return false;
      if (format === 'favorited_artist' && !itemHasFavoritedArtist(item)) return false;
      if (format === 'favorited_character' && !itemHasFavoritedCharacter(item)) return false;
      if (format === 'not_favorited_artist' && itemHasFavoritedArtist(item)) return false;
      if (format === 'not_favorited_character' && itemHasFavoritedCharacter(item)) return false;
      if (format === 'captioned' && !hasCaption(item)) return false;
      if (format === 'not_captioned' && hasCaption(item)) return false;
    }

    if (gallery.value.filterRatings.length && !gallery.value.filterRatings.includes(item.rating || '')) return false;

    if (showOnlySelected.value) {
      const id = getPostId(item);
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
// 稳定唯一键：同 grid 卡片的 :key。用它锁定「正在看的图」，避免下载时列表变动导致错位。
function itemKey(item) {
  return item ? (item.localPath || item.filename || '') : '';
}
// 当前大图在列表中的实时位置（供「第 N 张」计数、上/下一张边界判断）。列表变动时它会自然重算，
// 但 viewerItem 始终由 key 决定，所以标签/复制不会跟着索引漂移。
const viewerIndex = computed(() =>
  viewer.value.key ? viewerItems.value.findIndex(it => itemKey(it) === viewer.value.key) : -1
);
const viewerItem = computed(() => viewerItems.value[viewerIndex.value] || null);
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

// 两级页码：每 50 页为一「块」，超过一块才显示块导航
const PAGE_BLOCK_SIZE = 50;
// 当前块从 activePage 派生 —— 翻页/跳转/筛选重置都会让块自动跟随，无需额外接线
const totalBlocks = computed(() => Math.max(1, Math.ceil(activeTotalPages.value / PAGE_BLOCK_SIZE)));
const hasMultipleBlocks = computed(() => totalBlocks.value > 1); // 少图（≤50 页）退化为现状
const currentBlock = computed(() => {
  const b = Math.floor((activePage.value - 1) / PAGE_BLOCK_SIZE);
  return Math.min(Math.max(0, b), totalBlocks.value - 1); // 夹紧防越界
});
const blockStartPage = computed(() => currentBlock.value * PAGE_BLOCK_SIZE + 1);
const blockEndPage = computed(() => Math.min(activeTotalPages.value, blockStartPage.value + PAGE_BLOCK_SIZE - 1));
const blockPageNumbers = computed(() => { // 当前块内页码（≤50 个），供分页栏页码选择器渲染
  const out = [];
  for (let n = blockStartPage.value; n <= blockEndPage.value; n++) out.push(n);
  return out;
});
const blockLabels = computed(() => // 块下拉选项："1-50" / "51-100" ...
  Array.from({ length: totalBlocks.value }, (_, i) => {
    const s = i * PAGE_BLOCK_SIZE + 1;
    const e = Math.min(activeTotalPages.value, s + PAGE_BLOCK_SIZE - 1);
    return { index: i, label: `${s}-${e}` };
  })
);

const galleryStats = computed(() => {
  // 只保留总数 / 已筛选数；分数均/中位这类统计信息取消展示（右侧改放边框含义图例）
  const all = gallery.value.images;
  const filtered = filteredLocalImages.value;
  return { total: all.length, filtered: filtered.length };
});

// 输入框绑 searchInput 即时回显；显式提交（Enter / 搜索按钮 / 选历史 / 清空）才写回 gallery.search 真正参与过滤
const searchInput = ref(gallery.value.search);
const searchHistoryRef = ref(null);
// 程序化写入（清空按钮 / 查看器点 token 跳转）：立即同步两端，避免回显与过滤关键字错位
function setSearch(keyword) {
  const v = keyword || '';
  searchInput.value = v;
  gallery.value.search = v;
  if (v.trim()) pushSearchHistory(v);
}
// 用户在输入框上按 Enter / 点「搜索」按钮 → 提交当前输入并关掉下拉
function commitGallerySearch() {
  showSearchHistory.value = false;
  setSearch(searchInput.value);
}
// 点了下拉里某条历史 → 复用 setSearch 提交（同时把这条刷新到历史最前）
function onPickSearchHistory(entry) {
  showSearchHistory.value = false;
  setSearch(entry);
}

// 搜索历史：作者/角色搜索（顶部搜索框）和 tag 浏览（BrowseOverlay）分开保存，各 10 条上限
const SEARCH_HISTORY_KEY = 'crawlerSearchHistory';
const TAG_SEARCH_HISTORY_KEY = 'crawlerTagSearchHistory';
const SEARCH_HISTORY_MAX = 10;
const searchHistory = ref(loadHistory(SEARCH_HISTORY_KEY));
const tagSearchHistory = ref(loadHistory(TAG_SEARCH_HISTORY_KEY));
const showSearchHistory = ref(false);
const showTagSearchHistory = ref(false);
function loadHistory(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter(s => typeof s === 'string' && s.trim()).slice(0, SEARCH_HISTORY_MAX) : [];
  } catch (e) {
    return [];
  }
}
function saveHistory(key, list) {
  try { localStorage.setItem(key, JSON.stringify(list.slice(0, SEARCH_HISTORY_MAX))); } catch (e) { /* localStorage 满 / 不可用时静默 */ }
}
// 把 q 推进 history：去重（大小写不敏感）、移到最前、超出上限截尾
function pushHistoryEntry(list, q) {
  const v = String(q || '').trim();
  if (!v) return list;
  const filtered = list.filter(x => x.toLowerCase() !== v.toLowerCase());
  return [v, ...filtered].slice(0, SEARCH_HISTORY_MAX);
}
function pushSearchHistory(q) {
  const next = pushHistoryEntry(searchHistory.value, q);
  if (next[0] === searchHistory.value[0] && next.length === searchHistory.value.length) return; // 没变
  searchHistory.value = next;
  saveHistory(SEARCH_HISTORY_KEY, next);
}
function pushTagSearchHistory(q) {
  const next = pushHistoryEntry(tagSearchHistory.value, q);
  if (next[0] === tagSearchHistory.value[0] && next.length === tagSearchHistory.value.length) return;
  tagSearchHistory.value = next;
  saveHistory(TAG_SEARCH_HISTORY_KEY, next);
}
function removeSearchHistoryEntry(q) {
  searchHistory.value = searchHistory.value.filter(x => x !== q);
  saveHistory(SEARCH_HISTORY_KEY, searchHistory.value);
}
function removeTagSearchHistoryEntry(q) {
  tagSearchHistory.value = tagSearchHistory.value.filter(x => x !== q);
  saveHistory(TAG_SEARCH_HISTORY_KEY, tagSearchHistory.value);
}
function clearSearchHistory() {
  searchHistory.value = [];
  saveHistory(SEARCH_HISTORY_KEY, []);
}
function clearTagSearchHistory() {
  tagSearchHistory.value = [];
  saveHistory(TAG_SEARCH_HISTORY_KEY, []);
}

// 常用 tag（独立于搜索历史）：用户可保存反复使用的 tag 片段（例如 official_art），
// 点 chip 就把这一段追加到当前 query，避开每次重新手打。
const SAVED_TAGS_KEY = 'crawlerSavedTags';
const SAVED_TAGS_MAX = 20;
const savedTags = ref(loadSavedTags());
function loadSavedTags() {
  try {
    const raw = localStorage.getItem(SAVED_TAGS_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter(s => typeof s === 'string' && s.trim()).slice(0, SAVED_TAGS_MAX) : [];
  } catch (e) { return []; }
}
function persistSavedTags(list) {
  try { localStorage.setItem(SAVED_TAGS_KEY, JSON.stringify(list.slice(0, SAVED_TAGS_MAX))); }
  catch (e) { /* localStorage 满 / 不可用时静默 */ }
}
function addSavedTag(tag) {
  const v = String(tag || '').trim();
  if (!v) return;
  // 去重 + 新的放最前 + 截到上限
  const next = [v, ...savedTags.value.filter(x => x !== v)].slice(0, SAVED_TAGS_MAX);
  if (next[0] === savedTags.value[0] && next.length === savedTags.value.length) return;
  savedTags.value = next;
  persistSavedTags(next);
}
function removeSavedTag(tag) {
  const next = savedTags.value.filter(x => x !== tag);
  if (next.length === savedTags.value.length) return;
  savedTags.value = next;
  persistSavedTags(next);
}
// BrowseOverlay 点了某条 tag 历史 → 覆写 query 并触发搜索
function pickTagSearchHistory(q) {
  showTagSearchHistory.value = false;
  if (!q) return;
  browse.value.query = q;
  browse.value.selectAllPhase = 0;
  runBrowseSearch(1);
}
// 作者/角色搜索历史下拉：失焦延时关（让 mousedown.prevent 抢先触发，避免点条目时关闭后无响应）
function onSearchHistoryBlur() {
  setTimeout(() => { showSearchHistory.value = false; }, 150);
}

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
// 每页张数：夹到 [1,120]，非法输入回落到当前值；改动会触发 habits 持久化 + localTotalPages 重算并夹紧 page
function onPageSizeInput(event) {
  const raw = Number(event.target.value);
  const clamped = Number.isFinite(raw) ? Math.max(1, Math.min(120, Math.round(raw))) : gallery.value.pageSize;
  gallery.value.pageSize = clamped;
  event.target.value = clamped;
}
// 块导航：跳到目标块的「块首页」
function gotoBlock(blockIndex) {
  const b = Math.min(Math.max(0, blockIndex), totalBlocks.value - 1);
  gotoPage(b * PAGE_BLOCK_SIZE + 1);
}
function prevBlock() { gotoBlock(currentBlock.value - 1); }
function nextBlock() { gotoBlock(currentBlock.value + 1); }
function onBlockSelect(e) { gotoBlock(Number(e.target.value)); } // select value 是字符串

function appendLog(message) {
  // 旧版本会把 message 塞进 task.logs 列表渲染，下载 700 ID 刷出几百行非常吵。
  // 现在 UI 用进度条替代文本日志，appendLog 退化成 console 包装：开发者工具里仍能看到
  // 完整时间线（操作反馈、阶段切换、错误摘要等），但不污染界面。
  if (!message) return;
  try { console.log('[crawler]', message); } catch { /* noop */ }
}

function dismissBackendError() {
  task.value.backendError = '';
  task.value.backendErrorExpanded = false;
}

// 进度条工具：把"成功/失败"绝对数转成 bar 段宽百分比。
// 灰色未下载段不需要独立 div，bar 容器自身的 rgba 灰背景就是那段。
// 默认分母用 progress.total（下载阶段的 success/fail 段），传 secondTotal 时
// 改用 pageProgress.total（页进度条）。
function pct(n, secondTotal) {
  const total = secondTotal != null ? secondTotal : task.value.progress.total;
  if (!total) return 0;
  return Math.max(0, Math.min(100, (n / total) * 100));
}
// 进度条 hover 提示文本，给鼠标停留时看具体数字用
const progressTooltip = computed(() => {
  const p = task.value.progress;
  return `成功 ${p.success} / 失败 ${p.fail} / 总计 ${p.total}`;
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

function itemExtension(item) {
  return (item?.filename || '').split('.').pop().toLowerCase();
}

function isVideoItem(item) {
  return VIDEO_EXTS.includes(itemExtension(item));
}

// 卡片水印 / 缩略图首帧等地方统一走这两个判断：
// 把"zip 旁边有已转换的 .gif"当作 gif 看待，让卡片视觉跟普通 gif 一致
// （GIF 角标 + 「转GIF」按钮自动隐藏）。
function isAnimatedCard(item) {
  if (!item) return false;
  const ext = itemExtension(item);
  if (VIDEO_EXTS.includes(ext)) return true;
  if (ext === 'gif') return true;
  if (ext === 'zip' && item.hasGifCompanion) return true;
  return false;
}
function cardFormatLabel(item) {
  const ext = itemExtension(item);
  if (ext === 'zip' && item?.hasGifCompanion) return 'gif';
  return ext;
}

// 缩略图 <img> 真正解码完成（或失败）后才标记 loaded，触发淡入并撤下骨架屏。
// 关键：不是在 thumbUrl 赋值时就淡入，而是等浏览器把图片解出来，避免"src 变了但像素还没到"的空白闪烁。
function onThumbLoad(item) {
  if (item) {
    item.loaded = true;
    item.thumbBroken = false;
  }
}

// 缩略图加载失败（404 / 5xx / ffmpeg 不可用）：保留骨架屏常驻，
// 并打 thumbBroken 标记让 CSS 走深色 NO PREVIEW 占位，避免露出 broken 图标。
function onThumbError(item) {
  if (!item) return;
  item.loaded = false;         // 故意不置 true，让 skeleton 继续渲染
  item.thumbBroken = true;
}

async function hydrateThumbs(items) {
  await Promise.all(items.map(async item => {
    if (item.thumbUrl) return;

    const ext = (item.filename || '').split('.').pop().toLowerCase();

    // 视频 / GIF 都走后端 /thumb/ 端点：视频走 ffmpeg 首帧、GIF 走 Pillow seek(0)，
    // 统一拿到静态 JPEG 缩略图，避开 <video> 的 metadata seek 不稳定和 <img> 自动循环。
    if (VIDEO_EXTS.includes(ext) || ext === 'gif') {
      const folder = item.date || gallery.value.selectedDate;
      const w = gallery.value.thumbSize || 400;
      if (folder) {
        item.thumbUrl = `http://127.0.0.1:8000/thumb/${encodeURIComponent(folder)}/${encodeURIComponent(item.filename)}?w=${w}`;
      } else {
        item.thumbUrl = generateFormatPlaceholder(ext);
      }
      return;
    }

    if (ext === 'zip' && item.localPath) {
      // zip 旁边如果已有同名 .gif（手工转过 / 批量转过 / 之前下载时本身就带 gif），
      // 跟普通 .gif 一样走后端 /thumb/ 端点生成首帧静态 JPEG，
      // 避免 <img> 直接加载 .gif 在缩略图里循环播放。
      const gifPath = item.localPath.replace(/\.zip$/i, '.gif');
      if (await window.desktopAPI.file.exists(gifPath)) {
        const folder = item.date || gallery.value.selectedDate;
        const w = gallery.value.thumbSize || 400;
        const gifFilename = gifPath.split(/[\\/]/).pop();
        if (folder) {
          item.thumbUrl = `http://127.0.0.1:8000/thumb/${encodeURIComponent(folder)}/${encodeURIComponent(gifFilename)}?w=${w}`;
        } else {
          // 极端兜底：拿不到 folder 时回退到 local:// 直接读，但仍标 hasGifCompanion 让模板对齐
          item.thumbUrl = await window.desktopAPI.file.toLocalUrl(gifPath);
        }
        item.hasGifCompanion = true;
        return;
      }
    }

    const isImage = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'avif'].includes(ext);

    if (!isImage) {
      item.thumbUrl = generateFormatPlaceholder(ext);
      return;
    }

    if (item.localPath) {
      item.thumbUrl = await window.desktopAPI.file.toThumbUrl(item.localPath, gallery.value.thumbSize);
      return;
    }

    const webUrl = item.web_url || item.webUrl;
    if (webUrl) {
      item.thumbUrl = `http://127.0.0.1:8000${webUrl}`;
      return;
    }
  }));
}

async function loadGallery(date, silent = false, preserveView = false) {
  const requestedDate = date || gallery.value.selectedDate;
  const requestSequence = ++galleryLoadSequence;
  const previousCommittedDate = committedGalleryDate || gallery.value.selectedDate;
  if (requestedDate) gallery.value.selectedDate = requestedDate; // 乐观更新，让连续点“上一天/下一天”能自然推进
  galleryPendingDate.value = requestedDate;
  if (!silent) loadingGallery.value = true;
  try {
    const data = await window.desktopAPI.gallery.getByDate(requestedDate);
    if (requestSequence !== galleryLoadSequence) return; // 快速切换时丢弃乱序返回，避免日期回跳
    // 保留已展示项的 thumbUrl/loaded：避免启动时第二次 loadGallery 把视频/gif 缩略图
    // （走 HTTP /thumb/，ffmpeg 抽帧慢）整体重置成空字符串导致可感知的"闪一下就没"。
    // hydrateThumbs 内有 `if (item.thumbUrl) return;` 守护，命中旧值就不会再走一次抽帧。
    const oldByKey = new Map(
      (gallery.value.images || []).map(img => [img.localPath || img.filename, img])
    );
    const normalizedImages = data.images.map(item => {
      const old = oldByKey.get(item.localPath || item.filename);
      return {
        ...item,
        thumbUrl: old?.thumbUrl || '',
        loaded: old?.loaded || false,
        postId: extractPostId(item),
        rating: ratingFromTags(item),
        artistTokens: splitTags(item.artist),
        characterTokens: Array.isArray(item.characters) ? item.characters : splitTags(item.characters)
      };
    });
    gallery.value.selectedDate = data.selectedDate;
    committedGalleryDate = data.selectedDate;
    gallery.value.availableDates = Array.isArray(data.availableDates) ? data.availableDates : [];
    gallery.value.availableDateFolders = Array.isArray(data.availableDateFolders) ? data.availableDateFolders : [];
    // tag 文件夹由 IPC/后端透传，IPC fallback 没有此字段时给空数组兜底
    gallery.value.availableTags = Array.isArray(data.availableTags) ? data.availableTags : [];
    gallery.value.today = data.today;
    // 库根目录列表（main.cjs 透传）—— 跨盘合并 viewer_data 工具需要
    gallery.value.libraryRoots = Array.isArray(data.libraryRoots) ? data.libraryRoots : [];
    gallery.value.images = normalizedImages;
    if (preserveView) {
      // 保持当前页与排序快照：新下载的图按既定设计落到末尾，
      // 等用户主动点「重新排序」再并入（见 sortSnapshot 注释），不打断正在进行的刷新
      const tp = Math.max(1, Math.ceil(filteredLocalImages.value.length / gallery.value.pageSize));
      if (gallery.value.page > tp) gallery.value.page = tp;
    } else {
      gallery.value.page = 1;
      rebuildSortSnapshot();
    }
    await hydrateThumbs(pagedLocalImages.value);
    if (requestSequence === galleryLoadSequence) {
      refreshCaptionedSet(data.selectedDate);
      // 后台预热 mp4/gif 缩略图：不等用户滚到，gallery 切完就触发后端生成并写磁盘缓存
      prewarmHeavyThumbs();
      // 切换到新日期时，检查 folder 里的 ids_data.json 是否有待下载 ID。
      // 只在日期真的变更时提示；reloadCurrentGallery / 静默轮询不会触发。
      if (data.selectedDate && data.selectedDate !== previousCommittedDate) {
        notifyPendingIds(data.selectedDate);
      }
    }
  } catch (error) {
    if (requestSequence === galleryLoadSequence) {
      gallery.value.selectedDate = previousCommittedDate;
      showToast(`切换图库失败：${error.message}`, 'error');
      appendLog(`切换图库失败: ${error.message}`);
    }
  } finally {
    if (requestSequence === galleryLoadSequence) {
      galleryPendingDate.value = '';
      if (!silent) loadingGallery.value = false;
    }
  }
}

// 预热缩略图：对当前日期里所有需要 ffmpeg/Pillow 处理的 mp4/gif，
// 在 gallery 加载完第一屏后用 fetch 发请求触发后端生成。
// 等卡片滚到视线内时，磁盘缓存已经命中，秒开。
// 限并发上限 4，避免大文件 mp4 一次性把后端 _THUMB_LOCK 排满。
async function prewarmHeavyThumbs() {
  const items = gallery.value.images || [];
  const heavy = items.filter(it => {
    const ext = itemExtension(it);
    return (ext === 'gif' || VIDEO_EXTS.includes(ext)) && it.thumbUrl;
  });
  if (!heavy.length) return;
  const concurrency = 4;
  let cursor = 0;
  const workers = Array.from({ length: concurrency }, () => (async () => {
    while (true) {
      const idx = cursor++;
      if (idx >= heavy.length) return;
      try {
        // 用 cache: 'no-store' 避免任何中间代理层把请求吞掉
        await fetch(heavy[idx].thumbUrl, { cache: 'no-store' });
      } catch { /* 单个失败不影响其它 */ }
    }
  })());
  await Promise.all(workers);
}

// 切换到指定日期后探测 folder 的 ids_data.json：「仅收集ID」模式
// 或 download_ids 模式尚未消费完都会留 id 在这里。count > 0 时给个轻量提示。
async function notifyPendingIds(date) {
  if (!date) return;
  try {
    const res = await fetch(`http://127.0.0.1:8000/api/collected_ids?date=${encodeURIComponent(date)}`);
    if (!res.ok) return;
    const payload = await res.json();
    if (payload?.ok && payload.count > 0) {
      showToast(`${date} 还有 ${payload.count} 个 ID 未下载（用「收集ID / 下载ID」消费）`, 'info');
    }
  } catch {
    // 探测失败静默，不影响图库加载主流程
  }
}

async function clearThumbCache() {
  if (clearingThumbCache.value) return;
  if (!window.confirm('清空 .thumb_cache 和 .browse_thumb_cache？\n已生成的 mp4/gif 首帧 JPEG 都会清掉，下次访问会重新生成（每次冷启动较慢）。')) return;
  clearingThumbCache.value = true;
  showToast('正在清空缩略图缓存…', 'info');
  try {
    const res = await fetch('http://127.0.0.1:8000/api/thumb_cache', { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      const detail = data.by_dir || {};
      const parts = [];
      if (detail.thumb_cache) parts.push(`.thumb_cache × ${detail.thumb_cache}`);
      if (detail.browse_thumb_cache) parts.push(`.browse_thumb_cache × ${detail.browse_thumb_cache}`);
      showToast(`已清空 ${data.deleted} 个缩略图文件${parts.length ? '（' + parts.join('，') + '）' : ''}`, 'success');
      // 当前日期里所有图片的 thumbUrl 缓存失效，强制 hydrate 一次（不再走原 .thumb_url）
      gallery.value.images.forEach(it => { it.thumbUrl = ''; it.loaded = false; it.thumbBroken = false; });
      if (gallery.value.images.length) {
        await hydrateThumbs(pagedLocalImages.value);
        prewarmHeavyThumbs();
      }
    } else {
      showToast('清缓存失败', 'error');
    }
  } catch (err) {
    showToast(`清缓存失败: ${err.message}`, 'error');
  } finally {
    clearingThumbCache.value = false;
  }
}

async function convertAllZipsToGif() {
  const date = gallery.value.selectedDate;
  if (!date) {
    showToast('请先选择日期', 'error');
    return;
  }
  if (convertingZips.value) return;
  convertingZips.value = true;
  showToast(`正在把 ${date} 的 .zip 批量转成 .gif…`, 'info');
  try {
    const res = await fetch('http://127.0.0.1:8000/api/convert_all_zips', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, overwrite: false })
    });
    const result = await res.json();
    if (!result.ok) {
      showToast(`批量转 GIF 失败: ${result.msg || '未知错误'}`, 'error');
      return;
    }
    const { total, converted, skipped, failed } = result;
    if (total === 0) {
      showToast(`${date} 没有 .zip 可转换`, 'info');
      return;
    }
    const parts = [`共 ${total} 个 .zip`];
    if (converted) parts.push(`成功 ${converted}`);
    if (skipped) parts.push(`跳过 ${skipped}（已有 .gif）`);
    if (failed) parts.push(`失败 ${failed}`);
    const type = failed > 0 ? 'warning' : 'success';
    showToast(parts.join(' · '), type);
    // 失败详情打一行到日志面板，方便排查
    if (failed > 0) {
      const failedItems = (result.results || []).filter(r => r.status === 'failed');
      failedItems.forEach(r => appendLog(`[批量转GIF] ${r.zip} 失败: ${r.msg}`));
    }
    // 刷新图库，让新的 .gif 替换 .zip 的缩略图
    if (converted > 0) {
      await reloadCurrentGallery();
    }
  } catch (err) {
    showToast(`请求失败: ${err.message}`, 'error');
  } finally {
    convertingZips.value = false;
  }
}

// 跨盘合并 viewer_data.json：把 source 目录里的 viewer_data 增量追加到 target 目录。
// 用途：本地 hot_pic 下的热门下载，迁移到外置盘后，本地 viewer_data 里仍残留
// 这些图片的元数据，但目标盘 viewer_data 是独立维护的，需要把本地的增量同步过去。
// 后端用 post_url 做主 key 去重，重写合并项的 local_path 到目标盘路径，幂等可重复执行。
//
// Electron 的 window.prompt / window.confirm 在 renderer 里是空实现（点完没反应），
// 真正的选择 / 预览 / 确认全部在 MergeViewerDataModal.vue 里做。这里只负责：
// 1) 跑前的前置条件检查  2) 把当前 date / libraryRoots 喂给 modal
// 3) 合并成功（modal emit 'success'）后 toast + 必要时 reload 图库
const mergeViewerDataModal = ref({
  open: false,
  date: '',
  roots: []
});

function mergeViewerData() {
  const date = gallery.value.selectedDate;
  if (!date) {
    showToast('请先选中一个日期', 'warn');
    return;
  }
  if (task.value.isRunning || task.value.isStopping) {
    showToast('已有爬虫任务在跑，请等待完成', 'warn');
    return;
  }
  const roots = gallery.value.libraryRoots || [];
  if (roots.length < 2) {
    showToast('库根目录少于 2 个（默认 hot_pic + 至少一个外置盘），无法合并', 'warn');
    return;
  }
  mergeViewerDataModal.value = {
    open: true,
    date,
    roots
  };
}

function closeMergeViewerDataModal() {
  if (mergeViewerDataModal.value.open) {
    mergeViewerDataModal.value.open = false;
  }
}

async function onMergeViewerDataSuccess({ result, source }) {
  showToast(
    `合并完成：新增 ${result.merged_count} 条，已写入 ${result.target_path}`,
    'success'
  );
  // 合并源是当前 gallery 对应的目录时，刷新图库让新数据可见
  const firstLib = gallery.value.images[0]?.libraryId;
  if (source.isDefault || firstLib === source.id) {
    await reloadCurrentGallery();
  }
}

async function reloadCurrentGallery() {
  // loadGallery 用 `thumbUrl: old?.thumbUrl || ''` 保留旧缩略图，hydrateThumbs 又有
  // `if (item.thumbUrl) return;` 守护 —— 对日期切换避免"视频/gif 缩略图闪一下"是好事，
  // 但 zip→gif 转换后会卡在旧 zip 缩略图上。reload 路径提前把 zip 项目的 thumbUrl
  // 清空：loadGallery 保留空值，hydrateThumbs 重新走 zip 分支的 file.exists 检查。
  const date = gallery.value.selectedDate;
  const isZipItem = (it) => (it?.filename || '').toLowerCase().endsWith('.zip');
  (gallery.value.images || []).filter(isZipItem).forEach(it => {
    it.thumbUrl = '';
    it.loaded = false;
  });
  const previousCount = gallery.value.images.length;
  await loadGallery(date, false, true);
  const addedCount = Math.max(0, gallery.value.images.length - previousCount);
  showToast(
    addedCount > 0 ? `已刷新 ${date}，新增 ${addedCount} 张` : `已刷新 ${date}`,
    'success'
  );
}

async function refreshGalleryIndex(date = gallery.value.selectedDate) {
  try {
    const data = await window.desktopAPI.gallery.getByDate(date || gallery.value.selectedDate);
    gallery.value.availableDates = Array.isArray(data.availableDates) ? data.availableDates : [];
    gallery.value.availableDateFolders = Array.isArray(data.availableDateFolders) ? data.availableDateFolders : [];
    gallery.value.availableTags = Array.isArray(data.availableTags) ? data.availableTags : [];
    gallery.value.today = data.today || gallery.value.today;
  } catch (error) {
    appendLog(`日期列表刷新失败: ${error.message}`);
  }
}

function hasGalleryIndexFolder(folder) {
  if (!folder) return true;
  if (/^\d{4}-\d{2}-\d{2}$/.test(folder)) {
    return gallery.value.availableDates.includes(folder);
  }
  if (folder.startsWith('tag_')) {
    return gallery.value.availableTags.some(item => item?.folder === folder);
  }
  return true;
}

// 已生成 caption 的集合：多图库同日期下优先按 localPath 匹配，兼容旧的 filename 字符串。
const captionedSet = ref(new Set());
async function refreshCaptionedSet(requestedDate = gallery.value.selectedDate) {
  const date = requestedDate;
  if (!date || !window.desktopAPI?.caption?.listForDate) {
    captionedSet.value = new Set();
    return;
  }
  try {
    const entries = await window.desktopAPI.caption.listForDate(date);
    const set = new Set();
    for (const entry of Array.isArray(entries) ? entries : []) {
      if (typeof entry === 'string') {
        set.add(`name:${entry}`);
      } else if (entry && entry.localPath) {
        set.add(`path:${entry.localPath}`);
        if (entry.filename) set.add(`name:${entry.filename}`);
      }
    }
    if (gallery.value.selectedDate === date) captionedSet.value = set;
  } catch {
    if (gallery.value.selectedDate === date) captionedSet.value = new Set();
  }
}
function hasCaption(item) {
  if (!item) return false;
  if (item.localPath && captionedSet.value.has(`path:${item.localPath}`)) return true;
  return !!item.filename && captionedSet.value.has(`name:${item.filename}`);
}

let statusSyncInFlight = null;
async function syncStatus() {
  if (statusSyncInFlight) return statusSyncInFlight;
  statusSyncInFlight = syncStatusOnce();
  try {
    return await statusSyncInFlight;
  } finally {
    statusSyncInFlight = null;
  }
}

async function syncStatusOnce() {
  try {
    const status = await window.desktopAPI.crawler.status();
    const wasActive = task.value.isRunning || task.value.isStopping;
    
    task.value.isRunning = !!status.is_running;
    task.value.isStopping = !!status.is_stopping;
    task.value.isPaused = !!status.is_paused;
    task.value.jobId = status.job_id || '';
    task.value.mode = status.mode || '';
    task.value.outcome = status.outcome || (status.is_running ? 'running' : 'idle');
    task.value.errorMessage = status.error_message || '';
    task.value.backendError = status.backendError || '';

    // 进度条数据：后端每帧回吐 {total, success, fail}；JobRegistry.primary() 保证新 job 启动时
    // 自动切到新 job，这里不用 prevJobId 兜底
    if (status.progress) {
      task.value.progress = {
        total: status.progress.total || 0,
        success: status.progress.success || 0,
        fail: status.progress.fail || 0,
      };
    }
    if (status.page_progress) {
      task.value.pageProgress = {
        current: status.page_progress.current || 0,
        total: status.page_progress.total || 0,
        done: status.page_progress.done || 0,
      };
    }

    // 实时失败计数：/api/status 每帧透出 failed_pages / failed_ids，运行中也累加。
    // 任务结束（is_running=false）后保留最后一次值，让用户有时间看「最后 N 页失败」再启动新任务。
    task.value.runningFailedPages = Array.isArray(status.failed_pages) ? status.failed_pages.length : 0;
    task.value.runningFailedIds = Array.isArray(status.failed_ids)
      ? status.failed_ids.reduce((sum, g) => sum + ((g.ids || []).length || 0), 0)
      : 0;

    mergeBackendLogs(status.backendLogs);
    (status.new_logs || []).forEach(appendLog);

    const targetFolder = status.target_folder || '';
    if (targetFolder && !hasGalleryIndexFolder(targetFolder)) {
      await refreshGalleryIndex(gallery.value.selectedDate);
    }

    // 失败页变化时弹出简洁的"需手动重试"提示（用户 × 关闭后同 signature 不再弹）。
    // 不再用 failedPages 横幅 + 重试队列项 —— 任务继续走 / 暂停走原暂停按钮，
    // 想重试就重新入队（带正确的 mode / pages / tag_query / filter_tags）。
    if (Array.isArray(status.failed_pages) && status.failed_pages.length) {
      const byFolder = new Map();
      for (const item of status.failed_pages) {
        const folder = item?.folder || '';
        if (!byFolder.has(folder)) byFolder.set(folder, []);
        byFolder.get(folder).push(item);
      }
      for (const [folder, list] of byFolder.entries()) {
        showRetryPagesHint({ folder, pages: list });
      }
    }

    if (status.new_images?.length) {
      // 后端返回的 new_images 是模块全局 daily_viewer_data 的增量切片 —— tag 下载期间
      // 这份全局会被切换到 tag 文件夹，new_images 里也都是 tag 文件夹下的图。
      // 用 status.target_folder（后端当前实际写入的子目录）匹配当前画廊日期，
      // 不匹配就不合并，避免 tag 新图被 unshift 进日期画廊（一旦用户点开就会通过
      // refresh_visible 在日期 viewer_data.json 里追加错位的孤立条目）。
      if (status.target_folder && gallery.value.selectedDate === status.target_folder) {
        const appended = status.new_images.map(item => ({
          ...item,
          thumbUrl: '',
          loaded: false,
          postId: extractPostId(item),
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

        // 任务运行中、且单批新图 >= 20 张时，自动跳到第 1 页让用户立刻看到新下载的图。
        // 旧逻辑只 unshift + hydrate 当前页：若用户在第 3、5 页就完全看不到顶上新增的图，
        // 必须手动点回第 1 页。20 张阈值是用户偏好的「每下载 20 张就看到」颗粒度；
        // 小批量（< 20）继续走 in-place，不打断用户浏览；任务完成/暂停时也不跳（让用户
        // 自己决定是否切到第 1 页）。
        if (task.value.isRunning && appended.length >= 20) {
          gallery.value.page = 1;
        }

        await hydrateThumbs(pagedLocalImages.value);
      }
    }

    if (wasActive && !task.value.isRunning && !task.value.isStopping) {
      if (status.outcome === 'error' || status.error_message) {
        showToast("抓取任务异常停止！", "error");
      } else if (status.outcome === 'stopped') {
        // 区分"主动停止时有 ids_data 落盘"vs"主动停止但没收集到任何 ID"——
        // 前者后端 finalize_on_stop 会把 pending_ids 写到 ids_data.json，count > 0 时
        // 显式告诉用户「已增量保存 N 个 ID 到 2026-08-22/ids_data.json」；后者保持旧文案。
        // 适用于 collect_ids / popular_collect_ids / popular_range_collect_ids / popular_recover
        // 以及 rank/popular/tags 仍处于 collect 阶段时被停止；download_ids 模式 pending_ids
        // 还有未下的 ID 时也会命中（count > 0）。
        const savedCount = Number(status.last_saved_ids_count || 0);
        if (savedCount > 0) {
          const folderLabel = status.target_folder || targetFolder || '当前目录';
          const displayPath = `${folderLabel}/ids_data.json`;
          const fullPath = status.last_ids_data_path || displayPath;
          showToast(
            `已增量保存 ${savedCount} 个 ID 到 ${displayPath}`,
            'info'
          );
          appendLog(`[stop] 已保存 ${savedCount} 个 ID → ${fullPath}`);
        } else {
          showToast("抓取任务已停止", "info");
        }
      } else if (status.outcome === 'completed_with_failures') {
        showToast("任务完成，但有页面需要重试", "warning");
      } else {
        showToast("抓取任务已完成！", "success");
      }
      // 只有「下载的日期 == 正在看的日期」才需要整盘 reload。下载 A 但在看 B 时，
      // B 的盘上数据没变，reload 只会白白跳回第 1 页 + 重排，还会打断 B 正在进行的刷新。
      // 复用上面 new_images 同款 target_folder 门控；preserveView=true 让同日期 reload
      // 也保持当前页与排序快照（新图落末尾，等用户主动「重新排序」）。
      // 同日期下走 reloadCurrentGallery（= 右侧「↻ 刷新图库」按钮），行为完全一致：
      // 清 zip 缩略图、保留当前页、追加「已刷新 ... 新增 N 张」toast。tag / popular / 按ID下载
      // 这三种 mode 的 target_date 都联动右侧画廊日期，命中这条分支即可。
      const finishedFolder = targetFolder;
      if (finishedFolder && finishedFolder === gallery.value.selectedDate) {
        await reloadCurrentGallery();
      } else {
        await refreshGalleryIndex(gallery.value.selectedDate);
      }
      // 「按ID下载」子操作 + 任务结束 + 下载目标 = 当前画廊日期：
      // 主动重读 ids_data.json，把"已下载成功"的 ID（被 resolve_pending_id 移除）剔除，
      // 剩下的写回 idsText，让用户切回表单能直接看到「还剩哪些没下」+ 一键重新入队。
      // 防竞态：fetch 期间用户又切了日期 → 丢弃过期结果。
      // 不覆盖 'error'：任务异常时 ids_data.json 状态不可信，让用户自己决定下一步。
      if (isDownloadByIdsMode.value
          && finishedFolder
          && finishedFolder === gallery.value.selectedDate
          && (status.outcome === 'completed'
              || status.outcome === 'completed_with_failures'
              || status.outcome === 'stopped')) {
        fetchCollectedIdsForDate(finishedFolder, {
          onSuccess: (fetchedDate, payload) => {
            if (gallery.value.selectedDate !== fetchedDate) return;
            const before = (form.value.idsText || '').trim();
            form.value.idsText = payload.ids.join('\n');
            const after = (form.value.idsText || '').trim();
            if (before === after) return;  // 没变化就不打扰
            showToast(
              payload.ids.length
                ? `任务完成，已刷新 ${fetchedDate} 剩余 ${payload.ids.length} 个待下载 ID`
                : `任务完成，${fetchedDate} folder 已无可下载 ID`,
              'info',
            );
          },
          onEmpty: (fetchedDate) => {
            if (gallery.value.selectedDate !== fetchedDate) return;
            if ((form.value.idsText || '').trim()) {
              form.value.idsText = '';
              showToast(`任务完成，${fetchedDate} folder 已无可下载 ID`, 'info');
            }
          },
          // onError 静默：任务结束 toast 已经提示过，ids_data 读取失败不要额外打扰
        });
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

// 根据当前 form + 子操作 算出实际传给后端的 mode
function resolveActualMode() {
  if (form.value.mode === 'rank') {
    if (rankAction.value === 'collect_only') return 'collect_ids';
    // rank 不再有 'download_by_ids'：按用户反馈"按ID下载"是针对日期 folder 的，
    // 已迁到 popularAction 下。这里不兼容旧 rank+download_by_ids 状态。
    return 'rank';
  }
  if (form.value.mode === 'popular') {
    // 日期热门 · 子操作翻译：单日/范围 + 4 档子操作 → 5 个后端 mode
    // （日期范围下不开放「补全/补齐」和「按ID下载」：前者后端 popular_recover 不支持跨日，
    // 后者按 ID 下载语义上就是针对具体日期 folder，范围下无意义；UI 已隐藏 + watch 自动重置）
    if (form.value.dateRange) {
      if (popularAction.value === 'collect_only') return 'popular_range_collect_ids';
      return 'popular_range';
    }
    if (popularAction.value === 'collect_only') return 'popular_collect_ids';
    if (popularAction.value === 'recover') return 'popular_recover';
    if (popularAction.value === 'download_by_ids') return 'download_ids';
    return 'popular';
  }
  return form.value.mode;
}

async function startTask() {
  if (queueRunning.value) {
    showToast('顺序队列运行中，请先停止队列', 'info');
    return;
  }
  // 新任务开始：清掉上一轮遗留的「需手动重试」提示和 dismiss 记录，
  // 避免把旧 job 的失败页带过来误导。新 job 跑出来的失败页会重新弹。
  retryPagesHint.value = { show: false, signature: '', pages: [], text: '' };
  dismissedHintSignatures.clear();
  try {
    // resolveActualMode() 现在已统一处理 popular / popular_range / popular_*
    // 翻译，无需再在 startTask 内额外覆盖。
    const actualMode = resolveActualMode();
    const payload = {
      start_page: Number(form.value.startPage) || 1,
      end_page: Number(form.value.endPage) || 1,
      tags: form.value.tags || '',
      mode: actualMode,
      target_date: form.value.targetDate || '',
      start_date: form.value.startDate || '',
      end_date: form.value.endDate || '',
      tag_query: form.value.tagQuery || '',
      tag_source: form.value.tagSource || 'danbooru',
      download_concurrency: downloadConcurrency.value
    };
    if (payload.mode === 'download_ids') {
      const ids = parsePastedIds(form.value.idsText);
      if (ids.length) payload.ids = ids;
      // popular·按ID下载 专用：透传复选框状态；其他 download_ids 入口不传，走后端默认 True
      if (isDownloadByIdsMode.value) {
        payload.skip_logged = !!form.value.skipLogged;
      }
    } else if (payload.mode === 'tags') {
      if (!payload.tag_query.trim()) {
        showToast('请填写 tag 查询串（例如：hatsune_miku rating:safe）', 'error');
        return;
      }
      pushRecentPage('tags', 'start', payload.start_page);
      pushRecentPage('tags', 'end', payload.end_page);
    } else {
      // 记录这次实际使用的起始页/结束页，方便下次一键填回。
      // rank/collect_ids/download_ids 三个操作共用同一组页码习惯，记到 'rank' 上避免污染。
      const habitMode = form.value.mode === 'rank' ? 'rank' : (form.value.mode === 'popular' ? 'popular' : 'rank');
      pushRecentPage(habitMode, 'start', payload.start_page);
      pushRecentPage(habitMode, 'end', payload.end_page);
    }
    const result = await window.desktopAPI.crawler.start(payload);
    if (result?.ok === false) {
      showToast(result.msg || '启动失败', 'error');
      appendLog(result.msg || '启动失败');
      return;
    }
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

// ================= 顺序任务队列 =================
// 后端 MAX_CONCURRENT=1：队列在前端顺序驱动，逐个 start → 等完成 → 下一个。
// 完成检测只读响应式 task.value.isRunning（由 1.2s 的 pollTimer/syncStatus 维护），
// 绝不在这里自己调用 crawler.status()，否则会抢走 /api/status 的破坏性 drain、吞掉日志。
let _queueIdSeq = 0;
const taskQueue = ref([]);          // [{id, mode, startPage, endPage, tags, targetDate, startDate, endDate, tagQuery, tagSource, idsText, label, status, error}]
const queueRunning = ref(false);    // 整个队列是否在跑
const queueAbort = ref(false);      // 停止队列的中断旗标
const queueSkipItemId = ref(null);  // 仅跳过当前项；与停止整个队列严格分开
const queueIndex = ref(-1);         // 当前正在跑的项下标（-1 = 没在跑）
const justAddedId = ref(null);      // 刚加入队列的项 id：驱动一次入场高亮脉冲
const pendingQueueCount = computed(() =>
  taskQueue.value.filter(item => item.status === 'pending').length
);

const MODE_NAMES = {
  rank: '排行榜', popular: '日期热门', popular_range: '日期范围',
  tags: '标签下载', collect_ids: '仅收集ID', download_ids: '按ID下载',
};
// 「排行榜」模式下的子操作：决定实际传给后端的 mode
// （已不包含 download_by_ids：该动作迁到 popularAction 下）
const RANK_ACTION_LABELS = {
  download: '下载', collect_only: '仅收集ID'
};
// 「日期热门」模式下的子操作：四档（下载 / 仅收集ID / 补全/补齐 / 按ID下载），
// popularAction 与 rankAction 互不干扰（不同 mode 各自管自己的子操作状态）。
// 「补全/补齐」和「按ID下载」仅在单日（!dateRange）时可用：
// - recover：日期范围下隐藏（与后端 popular_recover 不支持跨日一致）
// - download_by_ids：按 ID 下载语义上是针对具体日期 folder 的，范围下无意义
const POPULAR_ACTION_LABELS = {
  download: '下载', collect_only: '仅收集ID', recover: '补全/补齐', download_by_ids: '按ID下载'
};

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function queueItemLabel(it) {
  const name = MODE_NAMES[it.mode] || it.mode;
  if (it.mode === 'tags') return `${name}[${it.tagSource === 'gelbooru' ? 'Gelbooru' : 'Danbooru'}]「${it.tagQuery || ''}」 ${it.startPage}-${it.endPage}页`;
  if (it.mode === 'download_ids') return `${name} ${dateTokenLabel(it.targetDate)} (${parsePastedIds(it.idsText || '').length}个ID)`;
  if (it.mode === 'collect_ids') return `${name} ${it.startPage}-${it.endPage}页`;
  if (it.mode === 'popular') {
    // 队列里始终保留 mode='popular'，子操作通过 popularAction 字段区分。
    // buildQueuePayload 在 runQueue 阶段才翻译成后端 mode。
    const sub = POPULAR_ACTION_LABELS[it.popularAction] || '';
    const subMark = (sub && it.popularAction !== 'download') ? `·${sub}` : '';
    // 按ID下载子操作没有页码概念：直接从 idsText 解析 ID 数 + 目标日期展示
    if (!it.dateRange && it.popularAction === 'download_by_ids') {
      const idsCount = parsePastedIds(it.idsText || '').length;
      const datePart = it.targetDate ? ` · ${dateTokenLabel(it.targetDate)}` : '';
      return `${name}·${sub} (${idsCount}个ID${datePart})`;
    }
    if (it.dateRange) {
      return `${name}${subMark} ${dateTokenLabel(it.startDate)}~${dateTokenLabel(it.endDate)} ${it.startPage}-${it.endPage}页`;
    }
    return `${name}${subMark} ${dateTokenLabel(it.targetDate)} ${it.startPage}-${it.endPage}页`;
  }
  if (it.mode === 'popular_range') return `${name} ${dateTokenLabel(it.startDate)}~${dateTokenLabel(it.endDate)} ${it.startPage}-${it.endPage}页`;
  if (it.mode === 'rank') {
    // rank 不再有 'download_by_ids' 子项，落到这里只有 download / collect_only 两档
    const sub = RANK_ACTION_LABELS[it.rankAction] || '';
    return sub && it.rankAction !== 'download' ? `${name}·${sub} ${it.startPage}-${it.endPage}页` : `${name} ${it.startPage}-${it.endPage}页`;
  }
  return `${name} ${it.startPage}-${it.endPage}页`;
}

// 快照当前表单为一个队列项（日期转相对令牌，便于今天/昨天按运行时解析）
function snapshotFormToItem() {
  const f = form.value;
  return {
    id: ++_queueIdSeq,
    mode: f.mode,
    startPage: Number(f.startPage) || 1,
    endPage: Number(f.endPage) || 1,
    tags: f.tags || '',
    targetDate: tokenizeDate(f.targetDate || ''),
    startDate: tokenizeDate(f.startDate || ''),
    endDate: tokenizeDate(f.endDate || ''),
    tagQuery: f.tagQuery || '',
    tagSource: f.tagSource || 'danbooru',
    idsText: f.idsText || '',
    rankAction: rankAction.value,
    // 透传 skipLogged 供入队后跑时沿用：用户勾选/取消勾选的状态不能因为入队就丢
    skipLogged: f.skipLogged !== false,
    popularAction: popularAction.value,
    dateRange: !!f.dateRange,
    status: 'pending',
    error: '',
    failureReports: [],
  };
}

// 统一的入队反馈：插入队列 + 短暂高亮脉冲 + toast。
// 用于 addCurrentToQueue（表单快照）、downloadBrowseSelected（browse 弹窗勾选）。
// insertIndex 不传时走 push 末尾；传了合法位置（0..length）则用 splice 插入到指定下标。
function enqueueItem(item, successMessage, insertIndex) {
  item.label = item.label || queueItemLabel(item);
  if (typeof insertIndex === 'number' && insertIndex >= 0 && insertIndex <= taskQueue.value.length) {
    taskQueue.value.splice(insertIndex, 0, item);
  } else {
    taskQueue.value.push(item);
  }
  justAddedId.value = item.id;
  setTimeout(() => { if (justAddedId.value === item.id) justAddedId.value = null; }, 1100);
  showToast(successMessage || `已加入队列：${item.label}`, 'success');
}

// 读取某日期 folder 的 ids_data.json（「仅收集ID」模式的产物），返回数字 ID 列表。
// 抽出来给两处复用：
//   1) addCurrentToQueue：idsText 为空时自动消费 folder 的待下载 ID
//   2) watch(gallery.selectedDate) 在 popular+按ID下载 模式下：切日期时主动重新 fetch，
//      写回 idsText，省去"清空 + 入队"两次操作（用户频繁补日期场景）
// 回调拿到 (fetchedDate, payload/err) 以便调用方做"过期丢弃"防竞态
// —— 用户在 fetch 期间又切了日期时，丢弃当前结果让新日期的 watch 接管。
function fetchCollectedIdsForDate(date, { onSuccess, onEmpty, onError } = {}) {
  const d = (date || '').trim();
  fetch(`http://127.0.0.1:8000/api/collected_ids?date=${encodeURIComponent(d)}`)
    .then(r => r.json())
    .then(payload => {
      if (!payload?.ok || !payload.ids?.length) {
        onEmpty && onEmpty(d, payload);
        return;
      }
      onSuccess && onSuccess(d, payload);
    })
    .catch(err => onError && onError(d, err));
}

function addCurrentToQueue() {
  const f = form.value;
  // 与 startTask 一致的最小校验
  if (f.mode === 'tags' && !(f.tagQuery || '').trim()) {
    showToast('标签下载需要先填 tag 查询串', 'error');
    return;
  }
  // 「按ID下载」子操作：空 idsText 时先看 folder 自己的 ids_data.json 是否有待下载 ID
  // 1) 日期热门（popular·单日）—— 已从 rank 迁到这里
  if (isDownloadByIdsMode.value) {
    if (!parsePastedIds(f.idsText || '').length) {
      const date = (f.targetDate || '').trim();
      fetchCollectedIdsForDate(date, {
        onSuccess: (fetchedDate, payload) => {
          f.idsText = payload.ids.join('\n');
          const item = snapshotFormToItem();
          enqueueItem(item, `已加入队列：消费 ${fetchedDate} folder 的 ${payload.ids.length} 个待下载 ID`);
        },
        onEmpty: () => showToast('folder 里没有待下载 ID，请先「仅收集ID」或在文本框粘贴', 'error'),
        onError: (err) => showToast(`读取 folder ID 失败：${err.message}`, 'error'),
      });
      return;
    }
  }
  const item = snapshotFormToItem();
  enqueueItem(item, `已加入队列：${item.label || queueItemLabel(item)}`);
}

function removeQueueItem(i) {
  const item = taskQueue.value[i];
  if (!item) return;
  if (item.status === 'running') {
    showToast('正在运行的任务不能删除，请先停止队列', 'info');
    return;
  }
  taskQueue.value.splice(i, 1);
  if (queueRunning.value && i < queueIndex.value) queueIndex.value -= 1;
}
// 队列项拖拽重排：直接 splice + 插入，保留 queueIndex 指向正在跑的那一项
const dragIndex = ref(-1);
const dragOverIndex = ref(-1);
const dragPosition = ref(null); // 'top' | 'bottom'，决定插入到目标的上方还是下方

function onQueueDragStart(i, event) {
  const item = taskQueue.value[i];
  if (!item || item.status === 'running') {
    // 正在跑的项不能被拖走（包括冒泡到子元素时）
    event.preventDefault();
    return;
  }
  dragIndex.value = i;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    // Firefox 必须 setData 才会触发后续 drop
    event.dataTransfer.setData('text/plain', String(i));
  }
}

function onQueueDragOver(i, event) {
  if (dragIndex.value < 0) return;
  event.preventDefault(); // 阻止默认才能触发 drop
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
  const rect = event.currentTarget.getBoundingClientRect();
  const mid = rect.top + rect.height / 2;
  dragOverIndex.value = i;
  // 光标在上半 → 插到目标上方；下半 → 插到目标下方
  dragPosition.value = event.clientY < mid ? 'top' : 'bottom';
}

function onQueueDragLeave(i) {
  // 只有当真是离开当前高亮项才清掉，避免子元素触发的 leave 把指示器擦掉
  if (dragOverIndex.value === i) {
    dragOverIndex.value = -1;
    dragPosition.value = null;
  }
}

function onQueueDrop(i) {
  if (dragIndex.value < 0) return;
  const from = dragIndex.value;
  let to = i;
  if (dragPosition.value === 'bottom') to += 1;
  if (to > from) to -= 1; // 同一列表内 splice 之后位置回退 1
  reorderQueueItem(from, to);
  resetQueueDragState();
}

function onQueueDragEnd() {
  // 拖到非 drop 区域（取消）也要清掉视觉状态
  resetQueueDragState();
}

function resetQueueDragState() {
  dragIndex.value = -1;
  dragOverIndex.value = -1;
  dragPosition.value = null;
}

function reorderQueueItem(from, to) {
  if (from === to) return;
  if (from < 0 || from >= taskQueue.value.length) return;
  if (to < 0 || to >= taskQueue.value.length) return;
  const arr = taskQueue.value;
  const [item] = arr.splice(from, 1);
  arr.splice(to, 0, item);
  if (queueRunning.value && queueIndex.value >= 0) {
    if (from === queueIndex.value) {
      // 正在跑的就是被拖的那一项：跟随它到新位置
      queueIndex.value = to;
    } else if (from < queueIndex.value && to >= queueIndex.value) {
      // 跑的那项在 from 之后、移到 from 之前 → 索引前移
      queueIndex.value -= 1;
    } else if (from > queueIndex.value && to <= queueIndex.value) {
      // 跑的那项在 from 之前、移到 from 之后 → 索引后移
      queueIndex.value += 1;
    }
  }
}

function clearQueue() {
  if (queueRunning.value) {
    taskQueue.value = taskQueue.value.filter(item => item.status === 'running');
    queueIndex.value = taskQueue.value.length ? 0 : -1;
    return;
  }
  taskQueue.value = [];
}

function buildQueuePayload(it) {
  // rank / popular 模式根据子操作翻译成后端实际 mode
  let actualMode = it.mode || 'rank';
  if (actualMode === 'rank') {
    if (it.rankAction === 'collect_only') actualMode = 'collect_ids';
    // rank 不再有 'download_by_ids' 子项：按 ID 下载已迁到 popularAction 下
  }
  if (actualMode === 'popular') {
    if (it.dateRange) {
      if (it.popularAction === 'collect_only') actualMode = 'popular_range_collect_ids';
      else actualMode = 'popular_range';
    } else {
      if (it.popularAction === 'collect_only') actualMode = 'popular_collect_ids';
      else if (it.popularAction === 'recover') actualMode = 'popular_recover';
      else if (it.popularAction === 'download_by_ids') actualMode = 'download_ids';
      // else 'popular' (两阶段)
    }
  }
  const payload = {
    start_page: Number(it.startPage) || 1,
    end_page: Number(it.endPage) || 1,
    tags: it.tags || '',
    mode: actualMode,
    target_date: resolveDateToken(it.targetDate || ''),
    start_date: resolveDateToken(it.startDate || ''),
    end_date: resolveDateToken(it.endDate || ''),
    tag_query: it.tagQuery || '',
    tag_source: it.tagSource || 'danbooru',
    download_concurrency: downloadConcurrency.value
  };
  if (payload.mode === 'download_ids') {
    const ids = parsePastedIds(it.idsText || '');
    if (ids.length) payload.ids = ids;
    // popular·按ID下载 子项：用入队时的 skipLogged 快照；其他 download_ids 入口走默认 true
    if (it.mode === 'popular' && it.popularAction === 'download_by_ids') {
      payload.skip_logged = it.skipLogged !== false;
    }
  }
  // popular* / popular_range* 缺日期时兜底昨天（snapshot 时通常已填，这里双保险）
  if (payload.mode === 'popular' || payload.mode === 'popular_collect_ids' || payload.mode === 'popular_recover') {
    if (!payload.target_date) payload.target_date = yesterdayString();
  }
  if (payload.mode.startsWith('popular_range')) {
    if (!payload.start_date) payload.start_date = yesterdayString();
    if (!payload.end_date) payload.end_date = yesterdayString();
  }
  return payload;
}

// 等当前任务跑完：只读 task.value.isRunning（1.2s pollTimer 维护），不自己调 status。
async function waitForTaskIdle() {
  // 1) 确认任务起来了（后端 start 已同步置 is_running；兜极快完成的任务，最多等 ~4s）
  const t0 = Date.now();
  while (!task.value.isRunning && !task.value.isStopping && (Date.now() - t0) < 4000) {
    if (queueAbort.value) return;
    await sleep(300);
  }
  // 2) 等它结束
  while (task.value.isRunning || task.value.isStopping) {
    if (queueAbort.value) return;
    await sleep(500);
  }
}

async function runQueue() {
  if (queueRunning.value) return;
  if (!pendingQueueCount.value) { showToast('队列没有待执行任务', 'info'); return; }
  if (task.value.isRunning || task.value.isStopping) { showToast('已有任务在运行或收尾，请稍候', 'error'); return; }
  queueRunning.value = true;
  queueAbort.value = false;
  queueSkipItemId.value = null;
  let okCount = 0, warningCount = 0, failCount = 0, skippedCount = 0;
  try {
    while (!queueAbort.value) {
      const i = taskQueue.value.findIndex(item => item.status === 'pending');
      if (i < 0) break;
      if (queueAbort.value) break;
      const item = taskQueue.value[i];
      const n = taskQueue.value.length;
      queueIndex.value = i;
      item.status = 'running';
      try {
        const payload = buildQueuePayload(item);
        let result = await window.desktopAPI.crawler.start(payload);
        // popular_recover 专用：盘没接时后端返回 DRIVE_UNPLUGGED，弹确认框询问是否
        // 临时改写到本地 HOT_PIC_DIR。确认后重发 force_local=true，下载到本地，
        // 完成后由用户手动把新增图片 move 到原盘做增量更新。
        if (!result?.ok && result?.code === 'DRIVE_UNPLUGGED') {
          const localPath = result.local_path || '(本地默认目录)';
          const ok = confirm(
            `${result.msg}\n\n` +
            `点击「确定」将图片临时下载到本地：\n${localPath}\n\n` +
            `完成后请手动把 ${localPath} 下的新增文件 move 到原盘对应目录。\n\n` +
            `点击「取消」跳过本项。`
          );
          if (ok) {
            appendLog(`[队列 ${i + 1}/${n}] 用户确认 force_local，改写本地 ${localPath}`);
            const retryPayload = { ...payload, force_local: true };
            result = await window.desktopAPI.crawler.start(retryPayload);
          } else {
            appendLog(`[队列 ${i + 1}/${n}] 用户取消 force_local，跳过本项`);
            item.status = 'skipped';
            item.error = '用户取消（盘没接）';
            skippedCount += 1;
            continue;
          }
        }
        if (!result?.ok) {
          item.status = 'error';
          item.error = result?.msg || '启动失败';
          failCount += 1;
          appendLog(`[队列 ${i + 1}/${n}] 启动失败：${item.error}`);
          continue;
        }
        appendLog(`[队列 ${i + 1}/${n}] 启动：${item.label}`);
        pushRecentPage(payload.mode, 'start', payload.start_page);
        pushRecentPage(payload.mode, 'end', payload.end_page);
        await syncStatus();        // 置 isRunning=true
        await waitForTaskIdle();   // 等到 isRunning=false
        if (queueAbort.value) { item.status = 'pending'; break; }
        if (queueSkipItemId.value === item.id) {
          item.status = 'skipped';
          item.error = '用户跳过';
          queueSkipItemId.value = null;
          skippedCount += 1;
          appendLog(`[队列 ${i + 1}/${n}] 已跳过：${item.label}`);
        } else if (task.value.outcome === 'error' || task.value.errorMessage) {
          item.status = 'error';
          item.error = task.value.errorMessage || '任务异常中断';
          failCount += 1;
        } else if (item.failureReports?.length) {
          item.status = 'warning';
          const pageCount = item.failureReports
            .filter(r => (r.kind || 'pages') !== 'ids')
            .reduce((sum, report) => sum + report.list.length, 0);
          const idCount = item.failureReports
            .filter(r => r.kind === 'ids')
            .reduce((sum, report) => sum + report.list.length, 0);
          const parts = [];
          if (pageCount) parts.push(`${pageCount} 页`);
          if (idCount) parts.push(`${idCount} 张图片`);
          item.error = `${parts.join(' + ') || '若干项'}等待重试`;
          warningCount += 1;
        } else {
          item.status = 'done';
          okCount += 1;
        }
      } catch (e) {
        item.status = 'error';
        item.error = e?.message || String(e);
        failCount += 1;
        appendLog(`[队列 ${i + 1}/${n}] 异常：${item.error}`);
      }
      await sleep(1000);  // 项间缓一下，降低撞限流概率
    }
  } finally {
    queueRunning.value = false;
    queueIndex.value = -1;
  }
  if (queueAbort.value) {
    showToast(`队列已停止（完成 ${okCount}，待重试 ${warningCount}，失败 ${failCount}）`, 'info');
  } else {
    showToast(
      `队列完成：成功 ${okCount}，待重试 ${warningCount}，跳过 ${skippedCount}，失败 ${failCount}`,
      failCount || warningCount ? 'warning' : 'success'
    );
  }
}

// 控制行「停止」按钮统一走这里：单任务时只停任务，队列中时同时停整个队列。
// 原来队列面板里有一个重复的「停止」按钮，行为是这个的子集，已删。
async function stopTaskOrQueue() {
  if (queueRunning.value) {
    queueAbort.value = true;
    queueSkipItemId.value = null;
    showToast('正在停止队列...', 'info');
  }
  if (task.value.isRunning) await stopTask();
}

async function skipCurrentQueueItem() {
  if (!queueRunning.value || queueIndex.value < 0) return;
  const item = taskQueue.value[queueIndex.value];
  if (!item || item.status !== 'running' || !task.value.isRunning || task.value.isStopping || queueSkipItemId.value === item.id) return;
  queueSkipItemId.value = item.id;
  showToast('正在跳过当前项，队列随后继续...', 'info');
  if (task.value.isRunning) await stopTask();
}

function queueStatusIcon(it) {
  return { pending: '○', running: '⏳', done: '✓', warning: '↻', skipped: '⏭', error: '⚠' }[it.status] || '○';
}

// 复制队列项的 error 文本，方便贴到聊天里排查
async function copyQueueError(it) {
  const text = (it && it.error) || '';
  if (!text) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      // 兜底：旧 Electron / 不支持 clipboard API 时走 document.execCommand
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch { /* noop */ }
      document.body.removeChild(ta);
    }
    showToast('已复制错误信息', 'success');
  } catch (e) {
    showToast(`复制失败：${e?.message || e}`, 'error');
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
      // 不再自动打开 GIF：批量转 / 单张转 时用户通常在继续看别的卡片或勾选下一张。
      // 要预览就在画廊点该卡片（或右键「打开」）。
      showToast("转换成功，已生成 .gif 配套文件", "success");
      // 刷新当前日期图库，让该 zip 卡片立刻显示 gif 缩略图/标志位
      if (gallery.value.selectedDate) {
        await reloadCurrentGallery();
      }
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
  const localPaths = pageItems.map(it => it.localPath).filter(Boolean);

  refresh.value.isRunning = true;
  refresh.value.dateStr = date;
  refresh.value.total = localPaths.length;
  refresh.value.done = 0;
  showToast(`正在刷新当前页 ${localPaths.length} 张...`, 'info');

  try {
    const res = await fetch('http://127.0.0.1:8000/api/refresh_visible', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, local_paths: localPaths })
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
      const target = gallery.value.images.find(img => (u.local_path && img.localPath === u.local_path) || img.filename === u.filename);
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
  // 刷新可能补回/更新 rating（旧图之前没有），同步到用于筛选/徽章的 item.rating
  const newRating = ratingFromTags({ tags: u.tags || (u.rating ? { rating: u.rating } : {}) });
  if (newRating) target.rating = newRating;
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
  const localPaths = items.map(it => it.localPath).filter(Boolean);
  if (!localPaths.length) { showToast('范围内没有图片', 'info'); return; }

  closeRangeRefreshDialog();
  refresh.value.isRunning = true;
  refresh.value.dateStr = date;
  refresh.value.total = localPaths.length;
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
      const bp = sliceItems.map(it => it.localPath).filter(Boolean);
      if (bp.length) batches.push({ pStart: p, pEnd, localPaths: bp });
    }
  } else {
    batches.push({ pStart: start, pEnd: end, localPaths });
  }

  let okCount = 0;
  let failCount = 0;

  async function runBatch(b, label) {
    if (!b.localPaths.length) return;
    if (label) showToast(label, 'info');
    const res = await fetch('http://127.0.0.1:8000/api/refresh_visible', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, local_paths: b.localPaths }),
    });
    const result = await res.json();
    if (!result.ok) {
      showToast(result.msg || '刷新失败', 'error');
      return;
    }
    for (const u of result.updates || []) {
      if (!u.ok) { failCount += 1; continue; }
      const target = gallery.value.images.find(img => (u.local_path && img.localPath === u.local_path) || img.filename === u.filename);
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
      showToast(`正在刷新第 ${start}-${end} 页共 ${localPaths.length} 张...`, 'info');
    }
    for (let bi = 0; bi < batches.length; bi += 1) {
      if (!refresh.value.isRunning) break;
      const b = batches[bi];
      const label = throttled
        ? `批次 ${bi + 1}/${batches.length} · 第 ${b.pStart}-${b.pEnd} 页（${b.localPaths.length} 张）`
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

// 工具栏下拉互斥：显示 / 刷新热度 / 翻译 三个下拉菜单的触发按钮都用了 @click.stop
// （@click.stop 的初衷是：阻止冒泡到 document click 后立刻被 onDocClickFor*Menu 把自己关掉）。
// 副作用是点其他下拉的触发按钮时，document click 不会触发，原本打开的那个下拉就关不掉，
// 多个下拉同时展开会互相重叠，看着很乱。解决：每次「打开」某个下拉时，主动把另外两个收起来。
// 「关闭」时不主动收其他（用户是显式收起的，别动他的状态）；外部点击关闭仍走 onDocClickFor*Menu。
function closeOtherToolbarDropdowns(exceptKey) {
  if (exceptKey !== 'display') displayMenu.value.open = false;
  if (exceptKey !== 'refresh') refreshMenu.value.open = false;
  if (exceptKey !== 'translate') translateMenu.value.open = false;
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
  const willOpen = !refreshMenu.value.open;
  if (willOpen) closeOtherToolbarDropdowns('refresh');
  refreshMenu.value.open = willOpen;
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
  const willOpen = !translateMenu.value.open;
  if (willOpen) closeOtherToolbarDropdowns('translate');
  translateMenu.value.open = willOpen;
}
function onTranslateChoice(action) {
  translateMenu.value.open = false;
  if (action === 'character') openTranslationModal();
  else if (action === 'dictionary') openCharacterDictionary();
  else if (action === 'import') importTranslationFile();
}
function onDocClickForTranslateMenu(e) {
  if (!translateMenu.value.open) return;
  const dropdown = document.querySelector('.translate-dropdown');
  if (dropdown && !dropdown.contains(e.target)) {
    translateMenu.value.open = false;
  }
}

// 「显示 ▾」下拉菜单：把纯显示偏好（排序/格式筛选/卡片大小/缩略图分辨率/每页张数）
// 收进一个入口，避免工具栏一排控件在常规窗口宽度下参差换行。
// 「看图刷新热度」开关改放刷新热度下拉里（与"刷新范围"语义同源），这里不再放。
// 这些都绑定 gallery.*，已有 habits watcher 持久化，菜单只是换个容器展示同样的 v-model。
const displayMenu = ref({ open: false });
function toggleDisplayMenu() {
  const willOpen = !displayMenu.value.open;
  if (willOpen) closeOtherToolbarDropdowns('display');
  displayMenu.value.open = willOpen;
}
function onDocClickForDisplayMenu(e) {
  if (!displayMenu.value.open) return;
  const dropdown = document.querySelector('.display-dropdown');
  if (dropdown && !dropdown.contains(e.target)) {
    displayMenu.value.open = false;
  }
}
function onDocClickForPagePicker(e) {
  if (!pagePicker.value.open) return;
  const host = document.querySelector('.pg-picker-host');
  if (host && host.contains(e.target)) return;
  // 浮层已 Teleport 到 body，不再是 host 的后代，需单独豁免，否则点面板内部会误关
  const panel = document.querySelector('.pg-picker-panel');
  if (panel && panel.contains(e.target)) return;
  pagePicker.value.open = false;
}

async function refreshSinglePost(item) {
  if (!item?.filename) return;
  const date = gallery.value.selectedDate || '';
  if (!date) return;
  try {
    const res = await fetch('http://127.0.0.1:8000/api/refresh_visible', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date, local_paths: item.localPath ? [item.localPath] : [], filenames: [item.filename] })
    });
    const result = await res.json();
    if (!result.ok) return;
    const u = (result.updates || [])[0];
    if (u && u.ok) applyRefreshUpdate(item, u);
  } catch (_) { /* 静默失败 */ }
}

const tutorialsModal = ref({ open: false });

function openTutorials() {
  tutorialsModal.value.open = true;
}

// 教程 Modal 逻辑已抽到 ./crawler/TutorialsModal.vue（hosts/ffmpeg 帮助），这里只保留开关。

// ---------------- 角色增量翻译 ----------------
const translationModal = ref({
  open: false,
  loading: false,
  list: [],          // [{tag, post_count, fallback_name}]
  search: '',
  importing: false,
  mode: 'untranslated', // untranslated | dictionary
  targetTag: '',
});

const translateDetail = ref({
  open: false,
  tag: '',
  fallbackName: '',
  source: { description: '', other_names: [], exists: false },
  manualPrompt: '',
  matchedTranslationKey: '',
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

// ---------------- Tag 浏览：像 Danbooru 原网页一样按 tag 预览缩略图，勾选后下载 ----------------
// 只拉 posts.json 元数据（/api/browse_tags），缩略图 <img> 直连 preview_file_url（直连模式）
// 或经 /api/proxy_thumb 转发（走代理模式）。下载复用 download_ids 走 /api/start。
const browse = ref({
  open: false,
  source: 'tags',      // 'tags' = 按 tag 查询；'collected' = 查看「仅收集ID」结果的在线预览
  query: '',           // 不再记忆上次搜索：每次打开都是空的
  page: 1,
  limit: 40,
  loading: false,
  posts: [],            // [{id, preview_file_url, ...}]
  selected: new Set(),  // 选中的 post id 字符串（跨页/跨搜索累计）
  // 选中时记下的 post 对象快照（id -> post），让「跨页已选」弹窗能在切页/换搜索之后
  // 仍然渲染缩略图和 rating/score。selected 是 source of truth，selectedItems 只是
  // 给 UI 用的反查表，下载/移除时只动 selected。
  selectedItems: new Map(),
  hasMore: false,
  error: '',
  targetDate: '',       // 空 = 今天
  minScore: 0,          // 客户端筛选：最低分
  sortBy: 'default',    // 当前页排序：default(收集/搜索顺序) | score
  downloading: false,
  collectedIds: [],     // collected 模式：当前日期收集到的全部 ID（按页切片后再拉预览）
  collectedDate: '',    // collected 模式：正在查看哪个日期的收集结果
  // 「全选当前」按钮三态循环状态：
  //   0 = 当前未做任何「全选」操作（或已清空）
  //   1 = 已经按过第一次：仅选未下载
  //   2 = 已经按过第二次：含已下载全选
  // 再次按下复原到 0。手动勾选、显式清空、加入下载队列、切页/新搜索时回到 0。
  selectAllPhase: 0,
});

// 缩略图 URL / rating 分桶 / 打开原帖 的辅助逻辑已抽到 ./crawler/BrowseOverlay.vue。
// ratingFromTags 仍在本组件——它服务于本地图库，browse 那边的 ratingBucket 不一样（g 归入 s）。
function ratingFromTags(item) {
  const r = ((item?.tags?.rating) || '').toLowerCase();
  if (!r) return '';
  if (r === 'e') return 'e';
  if (r === 'q') return 'q';
  return 's'; // s / g
}

const browseFiltered = computed(() => {
  const b = browse.value;
  const minScore = Number(b.minScore) || 0;
  let list = minScore > 0 ? b.posts.filter(p => (Number(p.score) || 0) >= minScore) : b.posts;
  if (b.sortBy === 'score') {
    list = [...list].sort((a, b2) => (Number(b2.score) || 0) - (Number(a.score) || 0));
  }
  return list;
});

// 刷新当前页的 score：重拉当前页（id: / tag 查询返回的就是最新数值）
// selected / selectedItems 默认就跨页保留，无需 keepSelection 参数。
function refreshBrowsePage() {
  if (browse.value.loading) return;
  if (browse.value.source === 'collected') {
    loadCollectedPage(browse.value.page);
  } else if (browse.value.source === 'rank') {
    runRankSearch(browse.value.page);
  } else {
    runBrowseSearch(browse.value.page);
  }
}

const browseSelectedCount = computed(() => browse.value.selected.size);

function openBrowse() {
  browse.value.source = 'tags';
  browse.value.open = true;
  if (!browse.value.targetDate) browse.value.targetDate = todayString();
  // 不再记忆上次搜索：每次打开都清空，等用户主动输入
  browse.value.query = '';
  browse.value.posts = [];
  browse.value.error = '';
  browse.value.page = 1;
  // 重新打开弹窗 = 全新一次浏览会话，把上一次累积的勾选（连同 post 快照）一起清掉
  browse.value.selected = new Set();
  browse.value.selectedItems = new Map();
  browse.value.selectAllPhase = 0;
  browseSelectionListOpen.value = false;
}

// 打开「查看收集ID」的在线预览：复用同一个 browse 弹窗，source 切到 collected
function openCollectedBrowse() {
  browse.value.source = 'collected';
  browse.value.open = true;
  browse.value.posts = [];
  browse.value.error = '';
  browse.value.page = 1;
  browse.value.selected = new Set();
  browse.value.selectedItems = new Map();
  browse.value.selectAllPhase = 0;
  browseSelectionListOpen.value = false;
  browse.value.collectedIds = [];
  // 默认看今天收集的；targetDate 也指向它，下载时落到同一天
  const d = todayString();
  browse.value.collectedDate = d;
  browse.value.targetDate = d;
  loadCollectedIds(d, 1);
}

// 打开「Rank 浏览」：复用同一个 browse 弹窗，source='rank'，自动拉第 1 页 order:rank。
// 不需要 tag 查询串；类比 openCollectedBrowse 的"打开即看"行为。
function openRankBrowse() {
  browse.value.source = 'rank';
  browse.value.open = true;
  browse.value.posts = [];
  browse.value.error = '';
  browse.value.page = 1;
  // 重新打开弹窗 = 全新一次浏览会话，勾选/快照一起清掉
  browse.value.selected = new Set();
  browse.value.selectedItems = new Map();
  browse.value.selectAllPhase = 0;
  browseSelectionListOpen.value = false;
  // 显式清空 query，防止从 tag 模式切过来残留
  browse.value.query = '';
  if (!browse.value.targetDate) browse.value.targetDate = todayString();
  runRankSearch(1);
}

function closeBrowse() {
  browse.value.open = false;
}

// collected 模式：先拉某日期收集到的全部 ID，再切片拉第 page 页的在线预览
async function loadCollectedIds(date, page = 1) {
  browse.value.loading = true;
  browse.value.error = '';
  browse.value.collectedDate = date || todayString();
  browse.value.targetDate = browse.value.collectedDate;
  try {
    const res = await fetch(`http://127.0.0.1:8000/api/collected_ids?date=${encodeURIComponent(browse.value.collectedDate)}`);
    const data = await res.json();
    if (!data.ok) {
      browse.value.collectedIds = [];
      browse.value.posts = [];
      browse.value.error = data.msg || '读取收集ID失败';
      showToast(browse.value.error, 'error');
      return;
    }
    browse.value.collectedIds = data.ids || [];
    if (!browse.value.collectedIds.length) {
      browse.value.posts = [];
      browse.value.page = 1;
      browse.value.hasMore = false;
      browse.value.error = `${browse.value.collectedDate} 还没有收集到 ID（先用「仅收集ID」模式跑一次）`;
      return;
    }
    await loadCollectedPage(page);
  } catch (e) {
    browse.value.error = '请求失败：' + (e.message || e);
    showToast(browse.value.error, 'error');
  } finally {
    browse.value.loading = false;
  }
}

// collected 模式翻页：把收集到的 ID 按 limit 切片，用 id:1,2,3 语法批量拉预览元数据
// 注意：selected / selectedItems 跨页/跨搜索累积，调用方不再需要 keepSelection 参数。
async function loadCollectedPage(page = 1) {
  const ids = browse.value.collectedIds;
  const limit = browse.value.limit;
  const total = ids.length;
  const maxPage = Math.max(1, Math.ceil(total / limit));
  const p = Math.min(Math.max(1, page), maxPage);
  const slice = ids.slice((p - 1) * limit, p * limit);
  if (!slice.length) {
    browse.value.posts = [];
    browse.value.hasMore = false;
    return;
  }
  browse.value.loading = true;
  browse.value.error = '';
  try {
    // Danbooru 支持 id:1,2,3 语法一次拉多个 post
    const q = 'id:' + slice.join(',');
    const url = `http://127.0.0.1:8000/api/browse_tags?tags=${encodeURIComponent(q)}&page=1&limit=${limit}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) {
      browse.value.posts = [];
      browse.value.error = data.msg || '获取预览失败';
      showToast(browse.value.error, 'error');
      return;
    }
    // 按收集顺序排列（Danbooru 可能不按 id: 里的顺序返回）
    const byId = new Map((data.posts || []).map(pp => [String(pp.id), pp]));
    browse.value.posts = slice.map(id => byId.get(String(id))).filter(Boolean);
    browse.value.page = p;
    browse.value.hasMore = p < maxPage;
    // 跨页/跨搜索累积：不再因换页而清空 selected / selectedItems。
    // 刷新 score（keepSelection=true）时原本就保留，行为统一。
    if (!browse.value.posts.length) {
      browse.value.error = '这一页的 ID 都取不到预览（可能已被删除或需登录）';
    }
  } catch (e) {
    browse.value.error = '请求失败：' + (e.message || e);
    showToast(browse.value.error, 'error');
  } finally {
    browse.value.loading = false;
  }
}

async function runBrowseSearch(page = 1) {
  const q = (browse.value.query || '').trim();
  if (!q) {
    showToast('请填写 tag 查询串（例如：hatsune_miku rating:safe）', 'error');
    return;
  }
  pushTagSearchHistory(q);
  browse.value.loading = true;
  browse.value.error = '';
  try {
    const url = `http://127.0.0.1:8000/api/browse_tags?tags=${encodeURIComponent(q)}&page=${page}&limit=${browse.value.limit}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) {
      browse.value.posts = [];
      browse.value.error = data.msg || '获取失败';
      showToast(browse.value.error, 'error');
      return;
    }
    browse.value.posts = data.posts || [];
    browse.value.page = data.page || page;
    browse.value.hasMore = !!data.has_more;
    // 跨页/跨搜索累积：selected / selectedItems 不会因为换页或换搜索而清空。
    // 全选三态 phase 由调用方在「切页 / 新搜索」时主动清零（见 browseGoPage 和模板里的
    // run-search / load-collected handler），让按钮文案回到「全选（除已下载）」。
    // 「刷新分数」走的是同页 re-fetch，不清 phase。
    if (!browse.value.posts.length) {
      browse.value.error = '没有结果（tag 可能无效，或该页已到末尾）';
    }
  } catch (e) {
    browse.value.error = '请求失败：' + (e.message || e);
    showToast(browse.value.error, 'error');
  } finally {
    browse.value.loading = false;
  }
}

// rank 模式翻页/刷新：与 runBrowseSearch 同形，URL 走 /api/browse_rank，不需要 query
async function runRankSearch(page = 1) {
  browse.value.loading = true;
  browse.value.error = '';
  try {
    const url = `http://127.0.0.1:8000/api/browse_rank?page=${page}&limit=${browse.value.limit}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) {
      browse.value.posts = [];
      browse.value.error = data.msg || '获取失败';
      showToast(browse.value.error, 'error');
      return;
    }
    browse.value.posts = data.posts || [];
    browse.value.page = data.page || page;
    browse.value.hasMore = !!data.has_more;
    if (!browse.value.posts.length) {
      browse.value.error = `没有结果（order:rank 第 ${page} 页可能已到末尾）`;
    }
  } catch (e) {
    browse.value.error = '请求失败：' + (e.message || e);
    showToast(browse.value.error, 'error');
  } finally {
    browse.value.loading = false;
  }
}

function browseGoPage(delta) {
  const next = browse.value.page + delta;
  if (next < 1) return;
  // 切到新的一页：当前页的全选三态作废，按钮回到「全选（除已下载）」，
  // 避免文案误导（之前累积的 phase 反映的是上一页的循环进度）。
  browse.value.selectAllPhase = 0;
  if (browse.value.source === 'collected') {
    loadCollectedPage(next);
  } else if (browse.value.source === 'rank') {
    runRankSearch(next);
  } else {
    runBrowseSearch(next);
  }
}

// 直接跳到指定页（来自 Tag/Rank 浏览弹窗的「跳到 N 页」输入框）。
// 与 browseGoPage 唯一区别：参数是绝对页码而不是 ±1 增量。
// 没有上界保护 —— Danbooru 自己在超页时返回空 page 数组，hasMore=false 会自动禁用「下一页」按钮。
function browseGoToPage(page) {
  let n = Number(page);
  if (!Number.isFinite(n) || n < 1) return;
  n = Math.floor(n);
  if (n === browse.value.page) return; // 同页不重发请求
  browse.value.selectAllPhase = 0;
  if (browse.value.source === 'collected') {
    loadCollectedPage(n);
  } else if (browse.value.source === 'rank') {
    runRankSearch(n);
  } else {
    runBrowseSearch(n);
  }
}

function toggleBrowseSelect(post) {
  const s = new Set(browse.value.selected);
  const items = new Map(browse.value.selectedItems);
  if (s.has(post.id)) {
    s.delete(post.id);
    items.delete(post.id);
  } else {
    s.add(post.id);
    // 记住 post 对象本身，跨页之后弹窗还能渲染缩略图
    items.set(post.id, post);
  }
  browse.value.selected = s;
  browse.value.selectedItems = items;
  // 手动勾选破坏「全选三态」的语义，回到初始态，下一次按下重新从「只选未下载」开始
  browse.value.selectAllPhase = 0;
}

// 「全选当前」按钮：三次一循环
//   第 1 次：勾选当前页所有「未下载」的图片（post.downloaded === false）
//   第 2 次：在已有的基础上再补上「已下载」那些，得到全选
//   第 3 次：清空，回到 phase 0
// 之后再次按下又从第 1 次开始循环。
function browseSelectAllVisible() {
  const b = browse.value;
  const phase = b.selectAllPhase || 0;
  if (phase === 0) {
    const s = new Set(b.selected);
    const items = new Map(b.selectedItems);
    for (const p of browseFiltered.value) {
      if (!p.downloaded) {
        s.add(p.id);
        items.set(p.id, p);
      }
    }
    b.selected = s;
    b.selectedItems = items;
    b.selectAllPhase = 1;
  } else if (phase === 1) {
    const s = new Set(b.selected);
    const items = new Map(b.selectedItems);
    for (const p of browseFiltered.value) {
      s.add(p.id);
      items.set(p.id, p);
    }
    b.selected = s;
    b.selectedItems = items;
    b.selectAllPhase = 2;
  } else {
    // phase === 2: 复原 —— 只清掉当前页的勾选，保留其它页已经选好的
    // （selected 是跨页累加器，不能整盘清掉，否则会误伤切页前的成果）
    const currentIds = new Set(browseFiltered.value.map(p => p.id));
    const s = new Set();
    const items = new Map();
    for (const id of b.selected) {
      if (!currentIds.has(id)) {
        s.add(id);
        const post = b.selectedItems.get(id);
        if (post) items.set(id, post);
      }
    }
    b.selected = s;
    b.selectedItems = items;
    b.selectAllPhase = 0;
  }
}

function browseClearSelection() {
  browse.value.selected = new Set();
  browse.value.selectedItems = new Map();
  browse.value.selectAllPhase = 0;
}

// 按钮文案随 phase 变化，提示用户下一次按下会发生什么。
const browseSelectAllLabel = computed(() => {
  const p = browse.value.selectAllPhase || 0;
  if (p === 1) return '全选所有';
  if (p === 2) return '取消全选';
  return '全选（除已下载）';
});

// 「查看已选」弹窗开/关
const browseSelectionListOpen = ref(false);
function openBrowseSelectionList() { browseSelectionListOpen.value = true; }
function closeBrowseSelectionList() { browseSelectionListOpen.value = false; }

// 把 selectedItems 拍成数组给弹窗用，并标出哪些正好在当前页（onPage）——
// 当前页的判定就是 post.id 是否在 browse.posts 里出现。
const browseSelectedEntries = computed(() => {
  const onPageIds = new Set(browse.value.posts.map(p => p.id));
  const items = browse.value.selectedItems;
  return Array.from(browse.value.selected)
    .map(id => {
      const post = items.get(id);
      // 极端情况：selected 里有但 selectedItems 没存到（理论上不会发生，
      // 因为 toggleBrowseSelect / browseSelectAllVisible 都同步写），
      // 用占位对象兜底，避免弹窗里出现空白
      return {
        id,
        post: post || { id, rating: '?', score: 0 },
        onPage: onPageIds.has(id),
      };
    })
    .sort((a, b) => String(a.id).localeCompare(String(b.id), 'en', { numeric: true }));
});
const browseOnPageCount = computed(() => browseSelectedEntries.value.filter(e => e.onPage).length);
const browseCrossPageCount = computed(() => browseSelectedEntries.value.length - browseOnPageCount.value);

// 从已选里移除单个 id（弹窗用）。手动行为，破坏 selectAll 三态。
function browseRemoveFromSelection(id) {
  if (!browse.value.selected.has(id)) return;
  const s = new Set(browse.value.selected);
  s.delete(id);
  const items = new Map(browse.value.selectedItems);
  items.delete(id);
  browse.value.selected = s;
  browse.value.selectedItems = items;
  browse.value.selectAllPhase = 0;
}

// Tag 浏览 / 查看收集 ID 共用的「下载」入口。
// 行为：把选中的 id 包成一个 download_ids 队列项推到 taskQueue 末尾，**不**立刻启动爬虫。
// ——这样无论是哪种来源的勾选，都与左侧表单「加入队列」走同一条路，行为一致可预期。
// 队列没在跑就只是挂在那里等用户点「运行队列」；队列正在跑则会在当前项结束后按顺序处理。
function downloadBrowseSelected() {
  const ids = Array.from(browse.value.selected);
  if (!ids.length) {
    showToast('请先勾选要下载的图片', 'info');
    return;
  }
  const targetDate = browse.value.targetDate || todayString();
  const item = {
    id: ++_queueIdSeq,
    mode: 'download_ids',
    startPage: 1,
    endPage: 1,
    tags: '',
    targetDate: tokenizeDate(targetDate),
    startDate: '',
    endDate: '',
    tagQuery: '',
    tagSource: 'danbooru',
    idsText: ids.join('\n'),
    status: 'pending',
    error: '',
    failureReports: [],
  };
  enqueueItem(item, `已加入队列：${queueItemLabel(item)}`);
  // 勾选状态被消费（"这些已经在队列里了"），给用户一个明确的视觉反馈
  browse.value.selected = new Set();
  browse.value.selectedItems = new Map();
  browse.value.selectAllPhase = 0;
  appendLog(`[Tag浏览] 已加入下载队列：${ids.length} 个 ID → ${targetDate}`);
}

// ---------------- 多页批量选择（一次性，不参与三态） ----------------
// 设计意图：现有「全选(除已下载)/全选所有/取消全选」是当前页三态；
// 用户想批量处理多页时开这个弹窗，输入 [从 X 页] [到 Y 页]，三个一次性动作：
//   1) 全选(除已下载)：把区间内 post.downloaded === false 的全部加进 selected
//   2) 全选所有：把区间内所有 post 加进 selected
//   3) 清空范围内：把区间内出现过的 id 从 selected / selectedItems 里删掉
// 这三个动作是幂等的（再次按结果一样），不进三态。

const browseMultiPage = ref({
  open: false,
  from: 1,
  to: 1,
  loading: false,
  progress: '',         // 形如 "已获取 2/5 页"
  error: '',
});

// 拉取第 N 页的 posts（不修改 browse.value.posts，不动 selected，只读）。
// tag 模式走 /api/browse_tags?page=N；collected 模式按 collectedIds 切片并用 id: 语法；
// rank 模式走 /api/browse_rank?page=N。
async function fetchBrowsePagePosts(page) {
  const b = browse.value;
  const limit = b.limit;
  if (b.source === 'collected') {
    const ids = b.collectedIds || [];
    const slice = ids.slice((page - 1) * limit, page * limit);
    if (!slice.length) return [];
    const q = 'id:' + slice.join(',');
    const url = `http://127.0.0.1:8000/api/browse_tags?tags=${encodeURIComponent(q)}&page=1&limit=${limit}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) throw new Error(data.msg || '获取预览失败');
    // 按收集顺序排，缺失的丢掉
    const byId = new Map((data.posts || []).map(pp => [String(pp.id), pp]));
    return slice.map(id => byId.get(String(id))).filter(Boolean);
  }
  if (b.source === 'rank') {
    const url = `http://127.0.0.1:8000/api/browse_rank?page=${page}&limit=${limit}`;
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) throw new Error(data.msg || '获取预览失败');
    return data.posts || [];
  }
  const q = (b.query || '').trim();
  if (!q) throw new Error('没有 tag 查询串');
  const url = `http://127.0.0.1:8000/api/browse_tags?tags=${encodeURIComponent(q)}&page=${page}&limit=${limit}`;
  const res = await fetch(url);
  const data = await res.json();
  if (!data.ok) throw new Error(data.msg || '获取预览失败');
  return data.posts || [];
}

// 顺序拉取 [from, to] 范围内的每一页（Promise.all + 进度回调），合并到一个数组返回。
async function fetchBrowseRange(from, to, onProgress) {
  const pages = [];
  for (let p = from; p <= to; p++) pages.push(p);
  const all = [];
  let done = 0;
  // 串行：避免一次性打几十个并发请求把后端 / Danbooru 打爆；
  // 用户感知上看 N 页通常 1-3 秒内结束，可接受
  for (const p of pages) {
    const posts = await fetchBrowsePagePosts(p);
    all.push(...posts);
    done += 1;
    onProgress && onProgress(done, pages.length);
  }
  return all;
}

function openBrowseMultiPage() {
  const cur = browse.value.page || 1;
  // 默认 [1, 当前页]，符合「从开头到当前位置」的直觉；
  // 用户改了之后会保留在组件 state 里直到关闭
  browseMultiPage.value.from = 1;
  browseMultiPage.value.to = Math.max(cur, 1);
  browseMultiPage.value.error = '';
  browseMultiPage.value.progress = '';
  browseMultiPage.value.open = true;
}
function closeBrowseMultiPage() {
  if (browseMultiPage.value.loading) return; // 正在执行时不许关
  browseMultiPage.value.open = false;
}

// mode: 'non_downloaded' | 'all' | 'clear_range'
async function browseMultiPageAction(mode) {
  const mp = browseMultiPage.value;
  const fromRaw = Math.floor(Number(mp.from) || 0);
  const toRaw = Math.floor(Number(mp.to) || 0);
  const cur = browse.value.page || 1;
  // 兜底：from 不填默认 1；to 不填默认当前页；保证 from >= 1
  const from = Math.max(1, fromRaw || 1);
  // 多页批量只处理「已经能拿到的页」——也就是 1..cur。
  // 用户输入 to > cur 不静默裁剪，而是显式报错，避免误操作把 999 页全勾上
  const to = Math.max(from, toRaw || cur);
  if (to > cur) {
    mp.error = `结束页 ${to} 超出当前第 ${cur} 页；多页批量只能处理已经能拿到的页`;
    return;
  }
  const b = browse.value;
  if (b.source === 'tags' && !(b.query || '').trim()) {
    mp.error = '请先在搜索框输入 tag 查询串';
    return;
  }
  if (b.source === 'collected' && (!b.collectedIds || !b.collectedIds.length)) {
    mp.error = '当前没有可用的收集 ID 列表';
    return;
  }
  mp.loading = true;
  mp.error = '';
  mp.progress = `准备拉取第 ${from}-${to} 页…`;
  try {
    const posts = await fetchBrowseRange(from, to, (done, total) => {
      mp.progress = `已获取 ${done}/${total} 页`;
    });
    if (!posts.length) {
      mp.error = '该范围内没有可处理的图片';
      return;
    }
    const s = new Set(b.selected);
    const items = new Map(b.selectedItems);
    let added = 0;
    let removed = 0;
    if (mode === 'clear_range') {
      const idsInRange = new Set(posts.map(p => String(p.id)));
      for (const id of idsInRange) {
        if (s.has(id)) {
          s.delete(id);
          items.delete(id);
          removed += 1;
        }
      }
    } else {
      const includeDownloaded = mode === 'all';
      for (const p of posts) {
        if (!includeDownloaded && p.downloaded) continue;
        if (!s.has(p.id)) added += 1;
        s.add(p.id);
        items.set(p.id, p);
      }
    }
    b.selected = s;
    b.selectedItems = items;
    // 一次性批量动作不算「手动勾选」，但它改变了 selected 的内容，让 selectAll 三态
    // 回到 phase 0 比较稳——下次按「全选当前」会从「只选未下载」开始
    b.selectAllPhase = 0;
    const verb = mode === 'clear_range'
      ? `清空范围内：移除 ${removed} 个`
      : (mode === 'all' ? `全选所有：新增 ${added} 个` : `全选(除已下载)：新增 ${added} 个`);
    showToast(`多页批量 ${from}-${to} 页 ${verb}，共 ${s.size} 个已选`, 'success');
    appendLog(`[Tag浏览] 多页批量 ${from}-${to} 页 ${verb}`);
    mp.open = false;
  } catch (e) {
    mp.error = '失败：' + (e.message || e);
  } finally {
    mp.loading = false;
    mp.progress = '';
  }
}

const filteredUntranslated = computed(() => {
  const keyword = translationModal.value.search.trim().toLowerCase();
  if (!keyword) return translationModal.value.list;
  return translationModal.value.list.filter(item =>
    item.tag.toLowerCase().includes(keyword) ||
    (item.fallback_name || '').toLowerCase().includes(keyword) ||
    (item.chinese_name || '').toLowerCase().includes(keyword) ||
    (item.source_hint || '').toLowerCase().includes(keyword)
  );
});

async function openTranslationModal() {
  if (!gallery.value.selectedDate) {
    showToast('请先选择日期', 'error');
    return;
  }
  translationModal.value.open = true;
  translationModal.value.mode = 'untranslated';
  translationModal.value.targetTag = '';
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

function rawCharacterTag(item, index) {
  const raw = item?.tags?.tag_string_character || '';
  return raw.split(' ').filter(Boolean)[index] || '';
}

function rawArtistTag(item, index) {
  // artist 字符串以空格分隔，与 splitTags 一致
  const raw = item?.artist || '';
  return raw.split(' ').filter(Boolean)[index] || '';
}

async function searchCharacterDictionary(query = translationModal.value.search) {
  translationModal.value.loading = true;
  try {
    const res = await fetch(`http://127.0.0.1:8000/api/character_translations?q=${encodeURIComponent(query || '')}&limit=200`);
    const data = await res.json();
    translationModal.value.list = data.items || [];
  } catch (err) {
    showToast('搜索角色字典失败: ' + err.message, 'error');
    translationModal.value.list = [];
  } finally {
    translationModal.value.loading = false;
  }
}

async function openCharacterDictionary(rawTag = '') {
  const tag = String(rawTag || '').trim();
  translationModal.value.open = true;
  translationModal.value.mode = 'dictionary';
  translationModal.value.targetTag = tag;
  // 去掉皮肤/作品括号后搜索，可一次看到同名角色和多皮肤条目。
  translationModal.value.search = tag ? tag.replace(/_\([^)]*\)/g, '').replace(/_+$/, '') : '';
  // 不要关 viewer：translationModal 跟 viewer 都用 .viewer-overlay，z-index 同为 10000，
  // 但 modal 在模板里后渲染，会自然盖在 viewer 上面。保存后用户关掉 modal 就能直接看到
  // 翻译刷新过的 chip，不必再点一次缩略图重开。
  await searchCharacterDictionary();
}

// 角色 / 画师 chip 右键弹出的迷你菜单：编辑词条（仅角色） / 复制
// 复用一个 state，避免复制 dismiss / teleport 那套逻辑。
// 旧版本直接打开字典弹窗，缺点是用户看不到 rawTag，搜索或复制原名要去翻字典。
// 现在固定弹一个菜单，菜单上仅显示 rawTag（英文原 tag），让用户认得当前是哪个。
// isInMultiSelect / multiSelectCount 控制菜单底部多选区（复制 N 个 / 搜索 N 个），
// 仅当右键命中一个已经在多选集合里的 tag 时才显示。
const charContextMenu = ref({
  open: false, x: 0, y: 0, rawTag: '', kind: 'character',
  isInMultiSelect: false, multiSelectCount: 0,
});

// viewer 里多选 tag：跟 selection.ids: Set<string> 同款；用 raw tag 作为唯一键。
// Danbooru 查询层不区分 artist / character，所以单一 Set 足够，
// 视觉反馈（is-multi-selected 外环）由 isTagMultiSelected(rawArtistTag/charRaw) 现算。
const tagMultiSelect = ref(new Set());
const TAG_MULTISELECT_MAX_BROWSE = 2;  // tag 浏览一般只支持 2 个 tag，超出时取前 2 + 警告 toast

function onCharacterContextMenu(event, item, index) {
  // 调试：先确认 handler 有没有真的跑到
  console.debug('[char-ctx] onCharacterContextMenu fired', { hasItem: !!item, index, hasTags: !!item?.tags, hasTagsChar: !!item?.tags?.tag_string_character });
  event.preventDefault();
  event.stopPropagation();
  const rawTag = rawCharacterTag(item, index);
  if (!rawTag) {
    console.debug('[char-ctx] rawTag empty, will toast');
    showToast('找不到该角色对应的原始 tag', 'warning');
    return;
  }
  // 视口边界兜底：菜单宽约 180 / 高约 96（角色含「编辑词条」会更高），
  // 画师只有一项更矮，统一用 96 足够。预留边距 8px
  const MENU_W = 180, MENU_H = 96, MARGIN = 8;
  let x = event.clientX;
  let y = event.clientY;
  if (x + MENU_W + MARGIN > window.innerWidth) x = window.innerWidth - MENU_W - MARGIN;
  if (y + MENU_H + MARGIN > window.innerHeight) y = window.innerHeight - MENU_H - MARGIN;
  if (x < MARGIN) x = MARGIN;
  if (y < MARGIN) y = MARGIN;
  charContextMenu.value = { open: true, x, y, rawTag, kind: 'character', isInMultiSelect: tagMultiSelect.value.has(rawTag), multiSelectCount: tagMultiSelect.value.size };
  console.debug('[char-ctx] menu opened', { x, y, rawTag, kind: 'character' });
}

function onArtistContextMenu(event, item, index) {
  // 画师右键：复用同一个迷你菜单，只显示「复制」一项
  event.preventDefault();
  event.stopPropagation();
  if (event && typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
  const token = (item?.artistTokens || [])[index];
  if (!token || token === '未知') {
    showToast('该画师标签为空', 'warning');
    return;
  }
  const rawTag = rawArtistTag(item, index);
  if (!rawTag) {
    showToast('找不到该画师对应的原始 tag', 'warning');
    return;
  }
  const MENU_W = 180, MENU_H = 64, MARGIN = 8;  // 画师只有一项，更矮
  let x = event.clientX;
  let y = event.clientY;
  if (x + MENU_W + MARGIN > window.innerWidth) x = window.innerWidth - MENU_W - MARGIN;
  if (y + MENU_H + MARGIN > window.innerHeight) y = window.innerHeight - MENU_H - MARGIN;
  if (x < MARGIN) x = MARGIN;
  if (y < MARGIN) y = MARGIN;
  charContextMenu.value = { open: true, x, y, rawTag, kind: 'artist', isInMultiSelect: tagMultiSelect.value.has(rawTag), multiSelectCount: tagMultiSelect.value.size };
}

function closeCharContextMenu() {
  if (charContextMenu.value.open) charContextMenu.value.open = false;
}

function charMenuEditDictionary() {
  const tag = charContextMenu.value.rawTag;
  closeCharContextMenu();
  if (tag) openCharacterDictionary(tag);
}

async function charMenuCopyRawTag() {
  const tag = charContextMenu.value.rawTag;
  closeCharContextMenu();
  if (!tag) return;
  try {
    await navigator.clipboard.writeText(tag);
    showToast(`原名已复制：${tag}`, 'success');
  } catch (e) {
    showToast(`复制失败：${e.message}`, 'error');
  }
}

// ============== viewer tag 多选 + tag 浏览 ==============

// 多选集合：viewer 里 Ctrl/Cmd+左键 点画师/角色 chip 加入/移除
function toggleTagMultiSelect(rawTag) {
  if (!rawTag) return;
  if (tagMultiSelect.value.has(rawTag)) tagMultiSelect.value.delete(rawTag);
  else tagMultiSelect.value.add(rawTag);
  // 强制重新赋值 Set：Vue 3 对 Set/Map 原生支持 add/delete 响应式，但跟现有代码（selection.ids
  // 那种模式）保持一致用 new Set 整体赋值，行为可预期且可调试。
  tagMultiSelect.value = new Set(tagMultiSelect.value);
}

function isTagMultiSelected(rawTag) {
  return !!rawTag && tagMultiSelect.value.has(rawTag);
}

function clearTagMultiSelect() {
  if (tagMultiSelect.value.size === 0) return;
  tagMultiSelect.value = new Set();
}

// 「用这个 tag 串去 browse」统一入口：复用 openBrowse() 的全套重置（source/open/targetDate/
// selected/posts/page），再覆写 targetDate（跟当前 gallery 日期同步）+ query，最后触发首页搜索。
// openBrowse() 内部已经把 selected/selectedItems/selectAllPhase 全部清空，跟用户切到新 query 语义一致。
function openBrowseWithQuery(query) {
  const q = String(query || '').trim();
  if (!q) return;
  openBrowse();
  const d = gallery.value.selectedDate;
  if (d && /^\d{4}-\d{2}-\d{2}$/.test(d)) browse.value.targetDate = d;
  browse.value.query = q;
  runBrowseSearch(1);
}

// viewer 里画师 / 角色 chip 的统一 click handler：
// - 普通左键：原行为 applySearch + closeViewer（过滤当前日期画廊）
// - Ctrl/Cmd+左键：toggleTagMultiSelect（不关闭 viewer，让用户连点多选）
function onViewerTagClick(event, item, index, kind /* 'artist' | 'character' */) {
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    event.stopPropagation();
    const raw = kind === 'artist' ? rawArtistTag(item, index) : rawCharacterTag(item, index);
    if (!raw) { showToast('该 tag 为空', 'warning'); return; }
    toggleTagMultiSelect(raw);
    return;
  }
  const token = kind === 'artist'
    ? (item?.artistTokens?.[index] || '未知')
    : (item?.characterTokens?.[index] || '');
  applySearch(token);
  closeViewer();
}

// 单 tag：「搜索 Tag」用右键的 raw tag 打开 BrowseOverlay
function charMenuSearchTag() {
  const tag = charContextMenu.value.rawTag;
  closeCharContextMenu();
  if (tag) openBrowseWithQuery(tag);
}

// 多 tag：「复制 N 个 Tag」把所有选中 raw tag 空格分隔写剪贴板
async function charMenuCopyMultipleTags() {
  const tags = Array.from(tagMultiSelect.value);
  closeCharContextMenu();
  if (!tags.length) return;
  const text = tags.join(' ');
  try {
    await navigator.clipboard.writeText(text);
    showToast(`已复制 ${tags.length} 个 tag（空格分隔）`, 'success');
  } catch (e) {
    showToast(`复制失败：${e.message}`, 'error');
  }
}

// 多 tag：「用 N 个 Tag 搜索」打开 BrowseOverlay，>2 时只取前 2 + warning toast
function charMenuSearchMultipleTags() {
  const tags = Array.from(tagMultiSelect.value);
  closeCharContextMenu();
  if (!tags.length) return;
  let q;
  if (tags.length > TAG_MULTISELECT_MAX_BROWSE) {
    q = tags.slice(0, TAG_MULTISELECT_MAX_BROWSE).join(' ');
    showToast(`已选 ${tags.length} 个 tag，tag 浏览通常只支持 ${TAG_MULTISELECT_MAX_BROWSE} 个；只取前 ${TAG_MULTISELECT_MAX_BROWSE} 个搜索`, 'warning');
  } else {
    q = tags.join(' ');
  }
  openBrowseWithQuery(q);
}

// 任意点击 / 滚动 / Esc 都关菜单；只挂一次，组件卸载自动解绑
function onCharMenuDismiss(event) {
  // 调试：看看到底什么时候会被叫、target 是什么
  console.debug('[char-ctx] dismiss fired', { type: event?.type, open: charContextMenu.value.open, target: event?.target?.tagName, targetClass: event?.target?.className });
  if (!charContextMenu.value.open) return;
  // 点击发生在菜单内部时由菜单 stopPropagation，这里再判一次：点菜单里按钮不关（按钮自己会关）
  if (event?.target && typeof event.target.closest === 'function' && event.target.closest('.char-ctx-menu')) return;
  closeCharContextMenu();
  console.debug('[char-ctx] menu closed by dismiss');
}
function onCharMenuKey(event) { if (event.key === 'Escape') closeCharContextMenu(); }

function closeTranslationModal() {
  translationModal.value.open = false;
}

function resetTranslateDetail() {
  translateDetail.value.tag = '';
  translateDetail.value.fallbackName = '';
  translateDetail.value.source = { description: '', other_names: [], exists: false };
  translateDetail.value.manualPrompt = '';
  translateDetail.value.matchedTranslationKey = '';
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
    const entry = data.translation || {};
    translateDetail.value.matchedTranslationKey = entry.matched_key || '';
    translateDetail.value.form = {
      has_chinese: !!entry.has_chinese,
      chinese_name: entry.chinese_name || '',
      source_hint: entry.source_hint || '',
      translated_description_zh: entry.translated_description_zh || '',
    };
  } catch (err) {
    showToast('加载角色描述失败: ' + err.message, 'error');
  }
}

function closeTranslateDetail() {
  translateDetail.value.open = false;
}

async function fetchCharacterSource() {
  // 强制调用 Danbooru Wiki 重新拉取描述。即使本地已有记录也允许刷新，
  // 用于修正 character.json / supplement 中长期未更新的信息。
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
    translateDetail.value.fetchMsg = 'Wiki 信息已更新';
    showToast('已重新拉取 Wiki 信息并刷新 Prompt', 'success');
  } catch (err) {
    translateDetail.value.fetchMsg = err.message;
    showToast('在线拉描述失败: ' + err.message, 'error');
  } finally {
    translateDetail.value.fetchBusy = false;
  }
}

// 复制 Prompt / 解析 JSON 已抽到 ./crawler/TranslateDetailModal.vue（emit notify 让父组件显示 toast）

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
    showToast(data.msg || '已保存并同步到画廊', 'success');
    const tag = translateDetail.value.tag;
    if (translationModal.value.mode === 'dictionary') await searchCharacterDictionary();
    else translationModal.value.list = translationModal.value.list.filter(it => it.tag !== tag);
    closeTranslateDetail();
    await loadGallery(gallery.value.selectedDate, false, true);
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
  if (!item) return `${gallery.value.selectedDate || ''}/`;
  const lib = item.libraryId || 'default';
  if (lib && lib !== 'default' && item.localPath) return `${lib}:${item.localPath.replace(/\\/g, '/')}`;
  return `${gallery.value.selectedDate || item.date || ''}/${item.filename || ''}`;
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

// 拆分版：只判断画师 token 集合是否命中收藏（用于"仅收藏画师"/"仅非收藏画师"筛选）
// 排除"未知"画师占位符，与 isCardFavorited / favArtistBadgeTitle 行为保持一致。
function itemHasFavoritedArtist(item) {
  for (const a of (item?.artistTokens || [])) {
    if (a && a !== '未知' && favoritedArtistSet.value.has(a)) return true;
  }
  return false;
}
// 拆分版：只判断角色 token 集合是否命中收藏。
function itemHasFavoritedCharacter(item) {
  for (const c of (item?.characterTokens || [])) {
    if (c && favoritedCharacterSet.value.has(c)) return true;
  }
  return false;
}

// 收藏画师/角色角标的悬浮提示：把命中的画师 / 角色列出来，
// 让用户知道"为什么这张被标了 ★ 收藏"。
function favArtistBadgeTitle(item) {
  const parts = [];
  for (const a of (item.artistTokens || [])) {
    if (a && a !== '未知' && favoritedArtistSet.value.has(a)) parts.push(`画师：${a}`);
  }
  for (const c of (item.characterTokens || [])) {
    if (c && favoritedCharacterSet.value.has(c)) parts.push(`角色：${c}`);
  }
  return parts.length ? `命中收藏的${parts.join('、')}` : '命中收藏';
}

// AI 标签：检查 meta / general / 兜底 tag_string 里是否含 ai-assisted / ai-generated。
// Danbooru 实际把 ai-generated / ai-assisted 归到 tag_string_meta，旧条目也可能只写入
// tag_string_general（极个别老数据），所以三个字段都查一遍。返回命中的标签名（用于 title）。
function aiTagOf(item) {
  const tg = item?.tags;
  if (!tg) return null;
  // tag_string 在 Danbooru JSON 里是 general + meta 的合集；优先用它做精确匹配。
  const combined = (tg.tag_string || '') + ' ' + (tg.tag_string_meta || '') + ' ' + (tg.tag_string_general || '');
  const tokens = combined.split(/\s+/).filter(Boolean);
  if (tokens.includes('ai-generated')) return 'ai-generated';
  if (tokens.includes('ai-assisted')) return 'ai-assisted';
  return null;
}

// 整张卡片的 tooltip：把收藏画师/角色 + AI 标签都列出来。
// 视觉不再靠角标传达（已改用边缘光圈），所以 title 要把"为什么发光"讲清楚。
function cardBadgeTitle(item) {
  const parts = [];
  if (isCardFavorited(item)) parts.push(favArtistBadgeTitle(item));
  const ai = aiTagOf(item);
  if (ai) parts.push(`Danbooru 标签：${ai}`);
  return parts.join('\n');
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
    library_id: item.libraryId || 'default',
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
  setSearch(keyword);
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
  viewer.value.open = true;
  viewer.value.key = itemKey(item);   // 用稳定唯一键锁定，而非索引
  viewer.value.zoom = 1;
  viewer.value.imageUrl = '';

  // 默认不联网刷新（离线时会一直报错）；开关在工具栏「翻译」右侧，开启后才刷新该图热度
  if (gallery.value.refreshOnView) refreshSinglePost(item);

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
  clearTagMultiSelect();
}

async function copyViewerImage() {
  const item = viewerItem.value;
  if (!item?.localPath) {
    showToast('当前图片没有可复制的本地文件', 'warning');
    return;
  }
  let imagePath = item.localPath;
  if ((item.filename || '').toLowerCase().endsWith('.zip')) {
    const gifPath = imagePath.replace(/\.zip$/i, '.gif');
    if (await window.desktopAPI.file.exists(gifPath)) imagePath = gifPath;
  }
  const result = await window.desktopAPI.caption.copyImage(imagePath, 2000);
  if (result?.ok) {
    if (result.isGif) {
      const kb = Math.round((result.bytes || 0) / 1024);
      // animated=true: 动图进了 image/gif 通道 → 浏览器/IM 粘出仍是动图
      // animated=false, hasFirstFrame=true: 目标 app 只读 CF_DIB，只能粘出首帧
      // 两条都没成不会走到这里（main.cjs ok=false）
      if (result.animated) {
        showToast(`已复制完整 GIF ${result.width}×${result.height}（${kb}KB，保留多帧动画）`, 'success');
      } else if (result.hasFirstFrame) {
        showToast(`已复制 GIF 首帧 ${result.width}×${result.height}（${kb}KB，目标应用不支持 GIF 多帧）`, 'warning');
      } else {
        showToast(`复制失败：GIF 已写入但目标应用读取不到`, 'error');
      }
    } else {
      showToast(`已复制图片 ${result.width}×${result.height}（上限 2000px）`, 'success');
    }
  } else {
    showToast(`复制失败：${result?.error || '不支持的图片格式'}`, 'error');
  }
}

async function stepViewer(offset) {
  if (!viewerItems.value.length) return;
  const cur = viewerIndex.value;
  if (cur < 0) return;
  const next = Math.min(Math.max(0, cur + offset), viewerItems.value.length - 1);
  if (next === cur) return;
  clearTagMultiSelect();
  viewer.value.key = itemKey(viewerItems.value[next]);
  await syncViewerImage();
  if (gallery.value.refreshOnView) refreshSinglePost(viewerItem.value);
}

async function stepGalleryDate(offset) {
  const dates = gallery.value.availableDates || [];
  const index = dates.indexOf(gallery.value.selectedDate);
  if (index < 0) {
    showToast('当前选择的不是日期图库', 'info');
    return;
  }
  const nextIndex = index + offset;
  if (nextIndex < 0 || nextIndex >= dates.length) return;
  await loadGallery(dates[nextIndex]);
}

function onViewerWheel(event) {
  if (!event.ctrlKey) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.1 : 0.9;
  viewer.value.zoom = Math.min(8, Math.max(0.2, viewer.value.zoom * factor));
}

async function onKeyDown(event) {
  // viewer 打开时，Esc 优先清多选态（不清 viewer，避免用户误按 Esc 丢失上下文）
  if (viewer.value.open && tagMultiSelect.value.size > 0 && event.key === 'Escape') {
    event.preventDefault();
    clearTagMultiSelect();
    return;
  }
  if (viewer.value.open && event.key === 'Escape') {
    closeViewer();
    return;
  }

  const tag = event.target?.tagName?.toLowerCase();
  if (['input', 'textarea', 'select', 'video'].includes(tag)) return;

  if (viewer.value.open) {
    if ((event.ctrlKey || event.metaKey) && (event.key === 'c' || event.key === 'C')) {
      event.preventDefault();
      await copyViewerImage();
    } else if (event.key === 'ArrowLeft') {
      await stepViewer(-1);
    } else if (event.key === 'ArrowRight') {
      await stepViewer(1);
    }
    return;
  }

  if (event.ctrlKey && event.key === 'ArrowLeft') {
    event.preventDefault();
    await stepGalleryDate(1);
    return;
  }
  if (event.ctrlKey && event.key === 'ArrowRight') {
    event.preventDefault();
    await stepGalleryDate(-1);
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
  if (idx === viewerIndex.value) return;
  viewer.value.key = itemKey(viewerItems.value[idx]);
  await syncViewerImage();
}

watch(() => gallery.value.search, () => { gallery.value.page = 1; });
// 分级筛选多选：点按钮在数组里增删该分级；空数组 = 显示全部
function toggleRatingFilter(rating) {
  const arr = gallery.value.filterRatings;
  const idx = arr.indexOf(rating);
  if (idx >= 0) arr.splice(idx, 1);
  else arr.push(rating);
}

watch(() => gallery.value.filterFormat, () => { gallery.value.page = 1; });
watch(() => gallery.value.filterRatings, () => { gallery.value.page = 1; }, { deep: true });
// 切日期：清空 tag 多选态（新日期的 tag 跟旧日期无关）
watch(() => gallery.value.selectedDate, () => { clearTagMultiSelect(); });
watch(() => gallery.value.sortBy, (newSort) => {
  gallery.value.page = 1;
  if (newSort === 'score' || newSort === 'fav') {
    rebuildSortSnapshot();
  }
});
watch(activePage, n => { jumpInput.value = n; });

watch(localTotalPages, total => {
  if (gallery.value.page > total) gallery.value.page = total;
});

watch(pagedLocalImages, async items => {
  await hydrateThumbs(items);
});

// 切换缩略图分辨率：清掉已生成的 thumbUrl，按新尺寸重建当前页（其余页翻到时惰性重建）
watch(() => gallery.value.thumbSize, () => {
  gallery.value.images.forEach(it => { it.thumbUrl = ''; it.loaded = false; });
  hydrateThumbs(pagedLocalImages.value);
});

onMounted(async () => {
  await loadGallery();
  await ensureService();
  // Python 后端就绪后立刻把本地保存的 SFW 偏好推过去，确保即使是当前会话第一次请求也用对 host
  await syncSafeModeToBackend();
  // 读取后端真实代理状态校正按钮（后端启动时已按探测结果自动决定走代理/直连）
  await loadProxyState();
  // 静默加载：Python 后端就绪后在后台刷新翻译，不再显示”正在读取”遮罩，消除闪烁
  await loadGallery(gallery.value.selectedDate, true);
  await syncStatus();
  loadFavSnapshot();
  pollTimer = window.setInterval(syncStatus, 1200);
  window.addEventListener('keydown', onKeyDown);
  document.addEventListener('click', onDocClickForRefreshMenu);
  document.addEventListener('click', onDocClickForTranslateMenu);
  document.addEventListener('click', onDocClickForDisplayMenu);
  document.addEventListener('click', onDocClickForPagePicker);
  // 角色 chip 右键菜单的全局 dismiss 监听
  console.debug('[char-ctx] register dismiss listeners');
  document.addEventListener('mousedown', onCharMenuDismiss, true);
  document.addEventListener('scroll', onCharMenuDismiss, true);
  window.addEventListener('blur', onCharMenuDismiss);
  window.addEventListener('resize', onCharMenuDismiss);
  window.addEventListener('keydown', onCharMenuKey);
});

onBeforeUnmount(() => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
  if (pollTimer) window.clearInterval(pollTimer);
  window.removeEventListener('keydown', onKeyDown);
  document.removeEventListener('click', onDocClickForRefreshMenu);
  document.removeEventListener('click', onDocClickForTranslateMenu);
  document.removeEventListener('click', onDocClickForDisplayMenu);
  document.removeEventListener('click', onDocClickForPagePicker);
  window.removeEventListener('resize', positionPagePicker);
  window.removeEventListener('scroll', positionPagePicker, true);
  // 角色 chip 右键菜单清理
  document.removeEventListener('mousedown', onCharMenuDismiss, true);
  document.removeEventListener('scroll', onCharMenuDismiss, true);
  window.removeEventListener('blur', onCharMenuDismiss);
  window.removeEventListener('resize', onCharMenuDismiss);
  window.removeEventListener('keydown', onCharMenuKey);
});

const modeDescription = computed(() => {
  if (form.value.mode === 'rank') {
    if (rankAction.value === 'collect_only') return '网络状况不佳时的极速模式：仅拉取排行榜列表和元数据，不下载图片本体。';
    return '获取当日 Danbooru 排行榜：先按页收集所有 ID，再按 ID 批量下载（中间可暂停/停止）。';
  }
  if (form.value.mode === 'popular') {
    const sub = popularAction.value;
    if (form.value.dateRange) {
      if (sub === 'collect_only') return '日期范围仅收集 ID：按日期迭代逐日收齐所有 ID，不下载图片。';
      return '日期范围两阶段：每个日期 folder 都先收齐所有 ID，再按 ID 批量下载，跨日自动落盘并防风控。';
    }
    if (sub === 'recover') return '热门·补全/补齐：按文件存在性判定热门页前 N 页。本地文件在 → 跳过；本地文件不在 → 下载（log 缓存 URL 优先）。可入队、暂停/继续，过程写 ids_data.json。';
    if (sub === 'collect_only') return '日期热门仅收集 ID：只拉取该日热门列表，不下载图片本体。';
    if (sub === 'download_by_ids') return '日期热门·按ID下载：粘贴 ID 列表或消费目标日期 folder 已收集的待下载 ID，针对该日期 folder 批量下载。';
    return '日期热门两阶段：先按页收齐所有 ID，再按 ID 批量下载（中间可暂停/停止）。';
  }
  if (form.value.mode === 'tags') {
    return `按 ${form.value.tagSource === 'gelbooru' ? 'Gelbooru' : 'Danbooru'} tag 查询下载到 tag 文件夹。`;
  }
  return '选择模式后配置参数，点击加入队列追加任务。';
});

// 前端按和后端 sanitize_tag_folder 同样的规则预览将要生成的文件夹名
// （后端会再 sanitize 一次，这里只是 UI 提示）
const tagFolderPreview = computed(() => {
  const q = (form.value.tagQuery || '').trim();
  if (!q) return '';
  const SPACE_MARK = '__';
  const COLON_MARK = '__c__';
  let s = q.replace(/:/g, COLON_MARK);
  s = s.replace(/[<>"/\\|?* -]/g, '');
  s = s.replace(/\s+/g, SPACE_MARK);
  s = s.replace(/^[. ]+|[. ]+$/g, '');
  if (!s) return '';
  return ('tag_' + s).slice(0, 80);
});
</script>

<template>
  <div class="crawler-layout" :class="{ 'gallery-hidden': !showGalleryPanel }">
    <section class="panel card control-panel">
      <div class="panel-head compact-head">
        <div>
          <h2>抓图任务</h2>
          <p class="inline-note">{{ modeDescription }}</p>
        </div>
        <div class="crawler-head-actions">
          <!-- 第 1 行：设置类（教程 / SFW / 直连 / 隐藏图库）。
               这 4 个都属于「开关 / 配置」类语义，把它们放在第一行；
               第 2 行放「浏览 / 收集 / 协程」三件套，让两行的功能有清晰分组。 -->
          <button class="ghost hosts-btn" @click="openTutorials" title="教程：修改 hosts 直连 Danbooru / 安装 ffmpeg（用于 zip→gif）">教程</button>
          <button
            class="ghost safe-mode-btn"
            :class="{ 'is-safe': safeMode, 'is-unsafe': !safeMode }"
            @click="toggleSafeMode"
            :title="safeMode ? '当前走 safebooru.donmai.us（无 R-18）。点击切换为完整 danbooru' : '当前走 danbooru.donmai.us（含 NSFW）。点击切回 SFW'"
          >{{ safeMode ? 'SFW' : 'NSFW' }}</button>
          <button
            class="ghost proxy-mode-btn"
            :class="{ 'is-proxy': useProxy, 'is-direct': !useProxy }"
            @click="toggleProxy"
            :title="useProxy ? '当前走代理下载。关掉代理软件后请点这里切到「直连」，否则下载会连不上死代理端口' : '当前直连下载（不走代理）。开了代理软件可点这里切回「走代理」'"
          >{{ useProxy ? '走代理' : '直连' }}</button>
          <button
            class="ghost gallery-toggle-btn"
            @click="showGalleryPanel = !showGalleryPanel"
            :title="showGalleryPanel ? '隐藏右侧本地图库' : '显示右侧本地图库'"
          >{{ showGalleryPanel ? '隐藏图库' : '显示图库' }}</button>
          <!-- 第 2 行：浏览 / 收集 / 协程 -->
          <button
            class="ghost"
            @click="openBrowse"
            title="按 tag 像 Danbooru 原网页一样预览缩略图，勾选后下载到指定日期"
          >Tag</button>
          <button
            class="ghost"
            @click="openRankBrowse"
            title="按 Danbooru 排行榜 order:rank 分页预览缩略图，勾选后下载到指定日期"
          >Rank</button>
          <button
            class="ghost"
            @click="openCollectedBrowse"
            title="查看「仅收集ID」模式收集到的 ID 的在线预览图，勾选后下载"
          >收集ID</button>
          <label class="concurrency-field concurrency-field-inline" title="下载协程数：1=最稳，4=平衡，8+=速度优先但易撞风控。rank/日期/标签 都用它">
            <span>并发</span>
            <input type="number" min="1" max="16" step="1" v-model.number="downloadConcurrency" class="concurrency-input" />
          </label>
        </div>
      </div>

      <div class="mode-selector">
        <button class="mode-chip" :class="{ active: form.mode === 'rank' }" @click="form.mode = 'rank'"
          title="按 Danbooru 排行榜：先收集所有 ID，再按 ID 批量下载">排行榜</button>
        <button class="mode-chip" :class="{ active: form.mode === 'popular' }" @click="form.mode = 'popular'"
          title="按指定日期（或日期范围）获取热门帖子并下载">日期热门</button>
        <button class="mode-chip" :class="{ active: form.mode === 'tags' }" @click="form.mode = 'tags'"
          title="按 tag 查询下载到独立的 tag_xxx 文件夹，与日期文件夹并行">标签下载</button>
      </div>

      <!-- 排行榜子操作栏：默认「下载」（先收 ID 再下）；点「仅收集ID」可只跑 ID 收集阶段。
           排行榜的目标文件夹是「今日」（没有日期语义），
           「按ID下载」语义上是针对日期 folder 的，已迁到「日期热门」栏下。 -->
      <div v-if="form.mode === 'rank'" class="rank-action-bar">
        <div class="seg-group">
          <button type="button" class="seg-btn" :class="{ active: rankAction === 'download' }"
            @click="rankAction = 'download'" title="默认：先按页收齐所有 ID，再按 ID 批量下载">下载</button>
          <button type="button" class="seg-btn" :class="{ active: rankAction === 'collect_only' }"
            @click="rankAction = 'collect_only'" title="只跑 ID 收集阶段：网不好时把 ID 先存到 folder 里，等网好再回来按 ID 下载">仅收集ID</button>
        </div>
      </div>

      <!-- 中间可滚动区：表单/按钮/失败横幅/多任务队列/状态/错误条都放这里，超高时内部滚动，永不溢出卡片 -->
      <div class="control-scroll">
      <!-- 「按 ID 下载」子操作没有页码概念：数据源是 ID 列表/folder；其它 rank 子操作和 popular 都还要页码 -->
      <div class="field-grid pages-grid" v-if="(form.mode === 'rank') || (form.mode === 'popular' && !isDownloadByIdsMode)">
        <label class="page-field">
          <span>起始页</span>
          <div class="page-field-row">
            <input v-model.number="form.startPage" type="number" min="1" class="page-input" />
            <div class="recent-pages" v-if="recentStartPages.length" title="最近使用的起始页">
              <span
                v-for="p in recentStartPages"
                :key="`rs-${form.mode}-${p}`"
                class="recent-page-chip"
                :class="{ active: p === form.startPage }"
                @click="applyRecentPage('start', p)"
                :title="`点击设为 ${p}`"
              >
                {{ p }}
                <button
                  type="button"
                  class="recent-page-x"
                  @click.stop="deleteRecentPage(form.mode, 'start', p)"
                  title="删除这条记录"
                >×</button>
              </span>
            </div>
          </div>
        </label>
        <label class="page-field">
          <span>结束页</span>
          <div class="page-field-row">
            <input v-model.number="form.endPage" type="number" min="1" class="page-input" />
            <div class="recent-pages" v-if="recentEndPages.length" title="最近使用的结束页">
              <span
                v-for="p in recentEndPages"
                :key="`re-${form.mode}-${p}`"
                class="recent-page-chip"
                :class="{ active: p === form.endPage }"
                @click="applyRecentPage('end', p)"
                :title="`点击设为 ${p}`"
              >
                {{ p }}
                <button
                  type="button"
                  class="recent-page-x"
                  @click.stop="deleteRecentPage(form.mode, 'end', p)"
                  title="删除这条记录"
                >×</button>
              </span>
            </div>
          </div>
        </label>
      </div>

      <!-- 日期热门 / 日期范围：合到一个 mode（popular），用 dateRange 切单日/范围。
           操作（popularAction）四档，默认「下载」（两阶段）。
           「补全/补齐」和「按ID下载」仅在单日（!dateRange）时可用：
             - recover：后端 popular_recover 不支持跨日
             - download_by_ids：按 ID 下载语义上针对具体日期 folder，范围下无意义
           下载协程数统一在头部，与 rank/tags 共享。 -->
      <div v-if="form.mode === 'popular'" class="date-mode-row">
        <div class="seg-group" title="单日 vs 范围：单日=一个日期 folder，范围=跨日按天迭代">
          <button type="button" class="seg-btn" :class="{ active: !form.dateRange }" @click="form.dateRange = false">单日</button>
          <button type="button" class="seg-btn" :class="{ active: form.dateRange }" @click="form.dateRange = true">日期范围</button>
        </div>
        <div class="seg-group">
          <button type="button" class="seg-btn" :class="{ active: popularAction === 'download' }"
            @click="popularAction = 'download'" title="默认：先按页收齐所有 ID，再按 ID 批量下载">下载</button>
          <button type="button" class="seg-btn" :class="{ active: popularAction === 'collect_only' }"
            @click="popularAction = 'collect_only'" title="只跑 ID 收集阶段：网不好时把 ID 先存到 folder 里，等网好再按 ID 下载">仅收集ID</button>
          <button v-if="!form.dateRange" type="button" class="seg-btn" :class="{ active: popularAction === 'recover' }"
            @click="onPickPopularRecover" title="按文件存在性补全热门页前 N 页：本地文件在 → 跳过；本地文件不在 → 下载（log 缓存 URL 优先）。可入队、暂停/继续，过程写 ids_data.json。仅在单日时可用">补全/补齐</button>
          <button v-if="!form.dateRange" type="button" class="seg-btn" :class="{ active: popularAction === 'download_by_ids' }"
            @click="popularAction = 'download_by_ids'" title="针对日期热门 folder 按 ID 下载：粘贴 ID 列表，或消费 folder 之前收集的待下载 ID。目标日期跟随右侧 GalleryCalendar 联动。仅在单日时可用">按ID下载</button>
        </div>
      </div>
      <label class="field-full" v-if="form.mode === 'popular' && !form.dateRange">
        <span>目标日期 <span class="muted compact-text">（默认昨天，可改）</span></span>
        <TaskDatePicker
          v-model="form.targetDate"
          placeholder="默认昨天"
          :available-dates="gallery.availableDates"
          :date-folders="gallery.availableDateFolders"
        />
      </label>
      <div class="field-grid" v-if="form.mode === 'popular' && form.dateRange">
        <label>
          <span>起始日期</span>
          <TaskDatePicker
            v-model="form.startDate"
            placeholder="起始日期"
            :available-dates="gallery.availableDates"
            :date-folders="gallery.availableDateFolders"
          />
        </label>
        <label>
          <span>结束日期</span>
          <TaskDatePicker
            v-model="form.endDate"
            placeholder="结束日期"
            :available-dates="gallery.availableDates"
            :date-folders="gallery.availableDateFolders"
          />
        </label>
      </div>

      <!-- 标签下载无独立协程行：协程数在头部共享 -->

      <!-- 日期热门·按ID下载：粘贴/消费 folder 的 ID（已从排行榜栏迁到这里） -->
      <label class="field-full" v-if="isDownloadByIdsMode">
        <span>粘贴 ID 列表</span>
        <textarea
          v-model="form.idsText"
          rows="2"
          placeholder="1) 压缩：dbids:6tewdt.dw&#10;2) 明文： 行分隔 / 逗号分隔 或 URL"
          style="font-family: Consolas, monospace; font-size: 12px; resize: vertical; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line); background: rgba(255,255,255,0.6);"
        />
        <div class="muted compact-text" style="margin-top: 4px;">
          已解析到 <strong>{{ parsedPastedIds.length }}</strong> 个 ID
          <span v-if="isPastedCompressed"> · 🗜 识别为压缩格式</span>
          <span v-if="parsedPastedIds.length"> · 将下载到 <strong style="color: var(--accent-deep);">{{ downloadByIdsTargetDateLabel }}</strong> 的图库（跟右侧画廊日期联动）</span>
        </div>
      </label>
      <!-- log.json 去重策略：和上面的 ID 列表同级成块，套用项目现成的 seg-group 模式（顶部
           popularAction 都在用），和子操作栏视觉完全一致。独立的 field-full 而非
           嵌套 label：避免点击文本框区域误触切换。 -->
      <div class="field-full dl-strategy-row" v-if="isDownloadByIdsMode">
        <span>下载策略</span>
        <div class="seg-group">
          <button type="button" class="seg-btn" :class="{ active: form.skipLogged }"
            @click="form.skipLogged = true" title="默认：log 命中即跳过（去重）">已下载则跳过</button>
          <button type="button" class="seg-btn" :class="{ active: !form.skipLogged }"
            @click="form.skipLogged = false" title="补齐 50 页时遇到「log 里有记录但文件其实丢了」切到这档重新拉一次">强制重下</button>
        </div>
      </div>
      <label class="field-full" v-if="form.mode === 'tags'">
        <span>Tag 来源</span>
        <select v-model="form.tagSource">
          <option value="danbooru">Danbooru</option>
          <option value="gelbooru">Gelbooru</option>
        </select>
      </label>
      <label class="field-full" v-if="form.mode === 'tags'">
        <span>
          Tag 查询串
        </span>
        <input
          v-model="form.tagQuery"
          type="text"
          placeholder="例如：hatsune_miku rating:safe -comic"
          style="font-family: Consolas, monospace; font-size: 12.5px;"
        />
        <div class="muted compact-text" style="margin-top: 4px; line-height: 1.5;">
          {{ form.tagSource === 'gelbooru' ? 'Gelbooru' : 'Danbooru' }} 下载到独立文件夹 <strong style="color: var(--accent-deep); font-family: Consolas, monospace;">{{ tagFolderPreview || 'tag_...' }}</strong>
        </div>
      </label>
      <label class="field-full">
        <span>过滤标签</span>
        <input v-model="form.tags" type="text" />
      </label>

      <div class="button-row">
        <button class="tq-add-btn" @click="addCurrentToQueue" title="把当前配置追加到顺序队列">入队</button>
        <!-- 暂停/继续/停止 已搬到下方「顺序队列」面板的 actions 区，与运行/跳过/清除
             排在一起（都属于"任务运行时控制"，与"入队"这种"配置提交"语义不同） -->
      </div>

      <!-- 顺序任务队列：常驻展开，运行中可继续追加任务，可删除未运行任务 -->
      <div class="task-queue-panel">
        <div class="tq-header">
          <span class="tq-title">
            顺序队列<span v-if="queueRunning" class="tq-running-badge">运行中 {{ queueIndex + 1 }}/{{ taskQueue.length }}</span>
            <span v-else-if="taskQueue.length" class="tq-count-badge">{{ taskQueue.length }}项</span>
          </span>
        </div>

        <TransitionGroup v-if="taskQueue.length" name="tq" tag="div" class="tq-list">
          <div
            v-for="(it, i) in taskQueue"
            :key="it.id"
            class="tq-item"
            :class="[it.status, {
              current: queueRunning && i === queueIndex,
              'just-added': it.id === justAddedId,
              'tq-dragging': dragIndex === i,
              'tq-drop-above': dragOverIndex === i && dragPosition === 'top' && dragIndex !== i,
              'tq-drop-below': dragOverIndex === i && dragPosition === 'bottom' && dragIndex !== i
            }]"
            :title="it.error || queueItemLabel(it)"
            :draggable="it.status !== 'running'"
            @dragstart="onQueueDragStart(i, $event)"
            @dragover="onQueueDragOver(i, $event)"
            @dragleave="onQueueDragLeave(i)"
            @drop="onQueueDrop(i)"
            @dragend="onQueueDragEnd"
          >
            <span class="tq-item-status">{{ queueStatusIcon(it) }}</span>
            <span class="tq-item-label">{{ it.label }}</span>
            <span class="tq-item-ops">
              <button v-if="it.status === 'error' && it.error" class="tq-mini tq-copy" @click.stop="copyQueueError(it)" title="复制错误信息">⎘</button>
              <button class="tq-mini tq-del" @click="removeQueueItem(i)" :disabled="it.status === 'running'" title="移除">×</button>
            </span>
            <!-- 错误 / 等待重试的说明直接内联显示，Electron 里 :title tooltip 几乎不可见 -->
            <div v-if="(it.status === 'error' || it.status === 'warning') && it.error" class="tq-item-error">
              {{ it.error }}
            </div>
          </div>
        </TransitionGroup>
        <div v-else class="tq-empty">
          上方“加入队列”会把当前配置追加到这里，任务按列表顺序依次执行。
        </div>

        <div v-if="task.isRunning || task.isPaused || taskQueue.length" class="tq-actions">
          <!-- 任务运行控制：暂停 / 继续 / 停止。这组按钮单条任务跑（即使没入队）时也要显示，
               所以 v-if 条件放宽为「任务在跑 / 已暂停 / 队列非空」三个之一。 -->
          <div class="tq-action-group">
            <span class="tq-action-label">任务控制</span>
            <button class="secondary" @click="pauseTask" :disabled="!task.isRunning || task.isPaused">暂停</button>
            <button class="secondary" @click="resumeTask" :disabled="!task.isRunning || !task.isPaused">继续</button>
            <button class="ghost" @click="stopTaskOrQueue" :disabled="!task.isRunning" :title="queueRunning ? '结束当前任务并停止整个顺序队列' : '结束当前任务'">停止</button>
          </div>
          <!-- 队列控制：运行 / 跳过 / 清除。仅队列里有项时启用，否则 disabled 也不隐藏
               —— 让用户随时知道"队列存在但还没运行"也能点"清除"清空。 -->
          <div class="tq-action-group" v-if="taskQueue.length">
            <span class="tq-action-label">队列</span>
            <button class="tq-run" @click="runQueue" :disabled="queueRunning || task.isRunning || task.isStopping || !pendingQueueCount" :title="`按顺序依次执行队列里的待执行任务（${pendingQueueCount} 项）`">运行</button>
            <button class="secondary" @click="skipCurrentQueueItem" :disabled="!queueRunning || queueIndex < 0 || !task.isRunning || task.isStopping || queueSkipItemId !== null" title="结束当前任务，但保留队列并继续下一项">跳过</button>
            <button class="ghost" @click="clearQueue" :disabled="queueRunning && taskQueue.every(item => item.status === 'running')" :title="queueRunning ? '清除尚未执行的项（不影响正在跑的任务）' : '清空整个队列'">清除</button>
          </div>
        </div>
      </div>

      <div class="status-pills">
        <span class="pill" :class="{ active: task.isRunning }">运行中: {{ task.isRunning ? '是' : '否' }}</span>
        <span class="pill" :class="{ warning: task.isStopping }">收尾中: {{ task.isStopping ? '是' : '否' }}</span>
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
      </div>
      <!-- /control-scroll -->

      <div class="modern-log-wrapper">
        <div class="modern-log-header">
          <div class="log-header-left">
            <span class="status-dot" :class="{ 'is-active': task.isRunning && !task.isPaused }"></span>
            <span class="log-title">运行动态</span>
          </div>
        </div>
        <div class="progress-panel">
          <!-- 阶段提示：仅在任务运行/暂停/停止中显示，区分"抓取ID"与"下载" -->
          <div v-if="runningPhaseText || runningFailureHint" class="progress-phase">
            <span>{{ runningPhaseText }}</span>
            <span v-if="runningFailureHint" class="running-failure-hint">{{ runningFailureHint }}</span>
          </div>
          <!-- 页进度条：仅在 collect 阶段（pageProgress.total>0 且 progress.total=0）显示，
               单一色块，没有"失败"分段 —— 抓取页只有成功/抓取中两种状态。
               旧版有"重试中"琥珀色微闪效果（isRetrying class），整个机制已下线。 -->
          <div
            v-if="task.pageProgress.total > 0 && task.progress.total === 0"
            class="progress-bar"
            :title="`第 ${task.pageProgress.current} / ${task.pageProgress.total} 页`"
          >
            <div
              class="progress-seg progress-seg-page"
              :style="{ width: pct(task.pageProgress.current, task.pageProgress.total) + '%' }"
            ></div>
          </div>
          <!-- 需手动重试的页范围：失败页变化时由 syncStatusOnce 驱动更新，× 关闭。
               比之前的"重试这些页"按钮 + auto-pause 横幅简洁：不做任何 auto 行为，
               只在运行动态日志区给一行提示，用户自决。 -->
          <div
            v-if="retryPagesHint.show"
            class="retry-pages-hint"
            role="status"
          >
            <span class="retry-pages-text">{{ retryPagesHint.text }}</span>
            <button
              type="button"
              class="retry-pages-close"
              title="关闭"
              aria-label="关闭提示"
              @click="dismissRetryPagesHint"
            >×</button>
          </div>
          <!-- 单条分段 bar：仅当后端给了 total（task_download_ids 模式）才渲染；
               单阶段 rank / popular / tags 走 _process_posts_concurrent 没有"总目标数"概念，
               total 一直是 0，bar 不显示，下方汇总文字照常更新。 -->
          <div
            v-if="task.progress.total > 0"
            class="progress-bar"
            :title="progressTooltip"
          >
            <div
              class="progress-seg progress-seg-success"
              :style="{ width: pct(task.progress.success) + '%' }"
            ></div>
            <div
              v-if="task.progress.fail > 0"
              class="progress-seg progress-seg-fail"
              :style="{ width: pct(task.progress.fail) + '%' }"
            ></div>
          </div>
          <div class="progress-summary">
            <template v-if="task.progress.total > 0">
              <!-- 下载阶段：显示「成功+失败 / 总数」 -->
              <span>{{ task.progress.success + task.progress.fail }}/{{ task.progress.total }} 完成</span>
              <span v-if="task.progress.fail" class="progress-fail-tail">，{{ task.progress.fail }} 失败</span>
            </template>
            <template v-else-if="task.pageProgress.total > 0">
              <!-- 抓 ID 阶段（collect）：页进度 + 页级成功/失败数。
                   之前只显示"扫到 N/M 页"：用户看不到这一波抓下来有多少页成功、多少页放弃，
                   要等任务结束看失败横幅才知道。
                   pageFetchSucceeded = done - failed：
                     - done 来自后端 page_done_count（grabber 返回后 +1，含成功 + 失败）
                     - failed 来自后端 failed_pages 数组长度（record_failed_page 后才计入）
                   故意不算 image-level success_count/fail_count（那是下载阶段的图级统计）。 -->
              <span>扫到 {{ task.pageProgress.current }} / {{ task.pageProgress.total }} 页</span>
              <span v-if="pageFetchSucceeded" class="progress-success-tail"> · 成功 {{ pageFetchSucceeded }} 页</span>
              <span v-if="task.runningFailedPages" class="progress-fail-tail"> · 失败 {{ task.runningFailedPages }} 页</span>
            </template>
            <template v-else>
              <span>{{ task.progress.success }} 已完成</span>
              <span v-if="task.progress.fail" class="progress-fail-tail">，{{ task.progress.fail }} 失败</span>
            </template>
          </div>
        </div>
      </div>
    </section>

    <section v-if="showGalleryPanel" class="panel card gallery-panel">
      <div class="gallery-head">
        <div class="gallery-title-row">
          <!-- 统一选择器：日期日历 + tag 文件夹列表（搜索 + 最近使用置顶）。
               提到第 1 行：触发按钮本身已显示当前日期，替代原 H2，省掉单独一行。
               内部根据 selectedDate 是否以 'tag_' 开头决定默认 tab -->
          <GalleryCalendar
            class="title-row-calendar"
            :available-dates="gallery.availableDates"
            :date-folders="gallery.availableDateFolders"
            :available-tags="gallery.availableTags"
            :selected-date="gallery.selectedDate"
            :today="gallery.today"
            @select="loadGallery"
          />
          <span class="gallery-stats-inline">
            共 {{ galleryStats.total }} 张<span v-if="galleryStats.filtered !== galleryStats.total"> · 已筛选 {{ galleryStats.filtered }} 张</span>
            <span class="border-legend" aria-label="图片边框颜色含义">
              <span class="legend-item" title="含有已收藏的画师或角色">
                <span class="legend-dot legend-dot-fav" aria-hidden="true"></span>收藏画师/角色
              </span>
              <span class="legend-item" title="含 ai-generated / ai-assisted 标签">
                <span class="legend-dot legend-dot-ai" aria-hidden="true"></span>AI 图
              </span>
            </span>
          </span>
        </div>
        <div class="gallery-tools">
          <button
            class="secondary tool-btn"
            @click="reloadCurrentGallery"
            :disabled="!gallery.selectedDate || loadingGallery"
            title="重新读取当前日期目录，显示下载任务刚写入的新图片，并保留当前页"
          >{{ loadingGallery ? '刷新中…' : '↻ 刷新图库' }}</button>
          <button
            class="secondary tool-btn"
            @click="convertAllZipsToGif"
            :disabled="!gallery.selectedDate || loadingGallery || convertingZips"
            title="用 ffmpeg 把当前日期文件夹里的所有 .zip 动画批量转成 .gif（已存在 .gif 的会自动跳过），完成后自动刷新图库"
            style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;"
          >{{ convertingZips ? '转 GIF 中…' : '批量转 GIF' }}</button>
          <button
            class="secondary tool-btn"
            @click="clearThumbCache"
            :disabled="clearingThumbCache"
            title="清空 .thumb_cache 和 .browse_thumb_cache：清掉旧的 MD5 残骸、ffmpeg 失败留下的 0 字节缩略图，或单纯想腾空间。已生成的 mp4/gif 首帧 JPEG 会全部重新生成"
          >{{ clearingThumbCache ? '清缓存中…' : '清缩略图缓存' }}</button>
          <button
            class="secondary tool-btn"
            @click="mergeViewerData"
            :disabled="!gallery.selectedDate || task.isRunning || task.isStopping"
            title="跨盘合并 viewer_data.json：把源 root（默认 hot_pic 或某外置盘）的 viewer_data 增量同步到目标 root。post_url 去重，重写 local_path 到目标盘路径，幂等可重复执行"
          >🔗 合并 viewer_data</button>
          <button
            class="secondary tool-btn select-mode-btn"
            :class="{ active: selection.enabled }"
            @click="setSelectionEnabled(!selection.enabled)"
            :title="selection.enabled ? '退出选择模式（已选记录会保留）' : '进入选择模式：可勾选多张图片分享；也可按 Ctrl+点击图片快速选择'"
          >{{ selection.enabled ? `✓ 选择中 (${selection.ids.size})` : (selection.ids.size ? `☐ 选择模式 (${selection.ids.size})` : '☐ 选择模式') }}</button>
          <button
            v-if="gallery.sortBy === 'score' || gallery.sortBy === 'fav'"
            class="secondary tool-btn"
            @click="rebuildSortSnapshot"
            title="按当前最新 score / 收藏数 重新排序（默认锁定排序，避免点开卡片刷新热度时位置乱跳）"
          >🔃 排序</button>
          <div class="display-dropdown">
            <button
              class="secondary tool-btn"
              :class="{ 'menu-open': displayMenu.open }"
              @click.stop="toggleDisplayMenu"
              title="显示设置：排序 / 筛选 / 卡片大小 / 缩略图 / 每页张数"
            >显示 ▾</button>
            <div v-if="displayMenu.open" class="display-menu" @click.stop>
              <div class="display-menu-row" title="排序方式">
                <span class="display-menu-label">排序</span>
                <div class="seg-group">
                  <button type="button" class="seg-btn" :class="{ active: gallery.sortBy === 'default' }" @click="gallery.sortBy = 'default'">默认</button>
                  <button type="button" class="seg-btn" :class="{ active: gallery.sortBy === 'score' }" @click="gallery.sortBy = 'score'">Score</button>
                  <button type="button" class="seg-btn" :class="{ active: gallery.sortBy === 'fav' }" @click="gallery.sortBy = 'fav'">收藏数</button>
                </div>
              </div>
              <label class="display-menu-row" title="按格式 / 收藏 / Caption 筛选">
                <span class="display-menu-label">筛选</span>
                <select v-model="gallery.filterFormat" class="display-menu-control">
                  <option value="all">全部格式</option>
                  <option value="image">图片</option>
                  <option value="video">视频</option>
                  <option value="zip">动图ZIP</option>
                  <option value="favorited_artist">仅收藏画师</option>
                  <option value="favorited_character">仅收藏角色</option>
                  <option value="not_favorited_artist">仅非收藏画师</option>
                  <option value="not_favorited_character">仅非收藏角色</option>
                  <option value="captioned">仅已生成 Caption</option>
                  <option value="not_captioned">仅未生成 Caption</option>
                </select>
              </label>
              <div class="display-menu-row" title="按 Danbooru 分级筛选（可多选）。S 安全含 general / Q 存疑 / E 限制。全部不选 = 显示全部。旧图需先刷新热度补全分级">
                <span class="display-menu-label">分级</span>
                <div class="seg-group">
                  <button type="button" class="seg-btn" :class="{ active: gallery.filterRatings.includes('s') }" @click="toggleRatingFilter('s')">S · 安全</button>
                  <button type="button" class="seg-btn" :class="{ active: gallery.filterRatings.includes('q') }" @click="toggleRatingFilter('q')">Q · 存疑</button>
                  <button type="button" class="seg-btn" :class="{ active: gallery.filterRatings.includes('e') }" @click="toggleRatingFilter('e')">E · 限制</button>
                </div>
              </div>
              <label class="display-menu-row" title="卡片大小">
                <span class="display-menu-label">卡片大小</span>
                <select v-model.number="gallery.cardSize" class="display-menu-control">
                  <option :value="120">紧凑</option>
                  <option :value="150">小</option>
                  <option :value="180">默认</option>
                  <option :value="220">大</option>
                </select>
              </label>
              <label class="display-menu-row" title="缩略图分辨率：越低越省内存、翻页越流畅；点开看大图仍是原图">
                <span class="display-menu-label">缩略图</span>
                <select v-model.number="gallery.thumbSize" class="display-menu-control">
                  <option :value="240">省流</option>
                  <option :value="360">标准</option>
                  <option :value="540">高清</option>
                  <option :value="0">原图</option>
                </select>
              </label>
              <label class="display-menu-row" title="每页显示张数（大屏可调高，默认 15）。过高会一次渲染很多卡片，翻页变卡">
                <span class="display-menu-label">每页张数</span>
                <input
                  type="number" min="1" max="120" step="1"
                  class="display-menu-control page-size-input"
                  :value="gallery.pageSize"
                  @change="onPageSizeInput"
                  @keyup.enter="onPageSizeInput"
                />
              </label>
            </div>
          </div>
          <div class="refresh-dropdown">
            <button
              :class="['refresh-btn', { active: refresh.isRunning, 'menu-open': refreshMenu.open }]"
              @click.stop="toggleRefreshMenu"
              :disabled="!gallery.selectedDate && !refresh.isRunning"
              :title="refresh.isRunning ? '点击停止刷新' : '选择刷新范围（本页 / 指定范围 / 全部），或切换『看图刷新热度』'"
            >
              <span v-if="!refresh.isRunning">刷新热度 ▾</span>
              <span v-else>{{ refresh.done }}/{{ refresh.total }}</span>
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
              <div class="refresh-menu-divider" role="separator"></div>
              <button
                class="refresh-menu-item is-toggle"
                :class="{ active: gallery.refreshOnView }"
                @click="gallery.refreshOnView = !gallery.refreshOnView"
                title="开启后，点开 / 切换大图会联网刷新该图 score / 收藏数；离线时建议保持关闭"
              >
                <span class="refresh-menu-label">看图刷新热度</span>
                <span class="refresh-menu-state">{{ gallery.refreshOnView ? '开' : '关' }}</span>
              </button>
            </div>
          </div>
          <div class="translate-dropdown">
            <button
              class="secondary translate-trigger tool-btn"
              :class="{ 'menu-open': translateMenu.open }"
              @click.stop="toggleTranslateMenu"
              title="翻译角色 / 导入翻译字典"
            >翻译 ▾</button>
            <div v-if="translateMenu.open" class="translate-menu" @click.stop>
              <button
                class="translate-menu-item"
                :disabled="!gallery.selectedDate"
                @click="onTranslateChoice('character')"
              >
                <span class="translate-menu-label">手动翻译角色</span>
                <span class="translate-menu-meta">{{ gallery.selectedDate ? '复制 Prompt，粘贴 JSON' : '先选日期' }}</span>
              </button>
              <button
                class="translate-menu-item"
                @click="onTranslateChoice('dictionary')"
              >
                <span class="translate-menu-label">角色字典</span>
                <span class="translate-menu-meta">搜索和修正同名/多皮肤</span>
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
          <span class="search-input-wrap">
            <input
              v-model="searchInput"
              class="search-input search-input-with-clear"
              type="text"
              placeholder="搜索作者 / 角色"
              @focus="showSearchHistory = true"
              @blur="onSearchHistoryBlur"
              @keyup.enter="commitGallerySearch"
              @keydown="searchHistoryRef?.handleKeydown?.($event)"
            />
            <button
              v-if="searchInput"
              class="search-clear-btn"
              @click="setSearch('')"
              title="清空搜索"
              type="button"
            >×</button>
            <SearchHistoryDropdown
              ref="searchHistoryRef"
              :items="searchHistory"
              :open="showSearchHistory"
              header-label="最近搜索"
              @pick="onPickSearchHistory"
              @remove="removeSearchHistoryEntry"
              @clear="clearSearchHistory"
              @close="showSearchHistory = false"
            />
          </span>
          <button class="secondary" @click="commitGallerySearch">搜索</button>
          <input type="file" ref="translationFileInput" style="display: none" accept=".json" @change="onTranslationFileSelected" />
        </div>
      </div>

      <!-- 统一选择器：日期日历 + tag 文件夹列表（搜索 + 最近使用置顶），
           内部根据 selectedDate 是否以 'tag_' 开头决定默认 tab -->
      <!-- 旧位置：第 3 行；现已在第 1 行 .gallery-title-row 顶部和 H2 合并，省一行。 -->

      <div v-if="selection.enabled" class="selection-bar inline-bar">
        <span class="selection-count">已选 <strong>{{ selection.ids.size }}</strong> 张</span>
        <button
          class="secondary"
          :class="{ active: showOnlySelected }"
          @click="showOnlySelected = !showOnlySelected"
          :disabled="!showOnlySelected && !selection.ids.size"
          title="切换：只显示当前日期里已选的图片"
        >只看已选</button>
        <button
          class="secondary"
          @click="selectionListOpen = true"
          :disabled="!selection.ids.size"
          title="查看所有已选 ID（可逐个跳转/移除）"
        >已选清单</button>
        <button class="secondary" @click="copySelectedIds" :disabled="!selection.ids.size" title="复制选中图片的 IDs 到剪贴板（明文逗号分隔）">复制 IDs</button>
        <button class="secondary" @click="openCryptoTool" title="打开加密工具：把任意 IDs 文本压缩成短字符串方便分享">加密工具</button>
        <button class="ghost" @click="clearSelection" :disabled="!selection.ids.size">清空</button>
        <button class="ghost" @click="setSelectionEnabled(false)" title="退出选择模式（已选记录会保留）">退出</button>
      </div>

      <div v-if="loadingGallery" class="gallery-switch-status" role="status">
        正在切换到 {{ galleryPendingDate || gallery.selectedDate }}…
      </div>
      <div v-if="!activeItems.length && !loadingGallery" class="gallery-empty">
        {{ showOnlySelected ? '当前日期没有已选图片，切换日期试试' : '当前日期没有图片' }}
      </div>
      <div v-else-if="activeItems.length" class="gallery-grid" :class="{ 'is-switching': loadingGallery }" :style="`--card-min-w: ${gallery.cardSize}px`" :aria-busy="loadingGallery">
        <article v-for="item in activeItems" :key="item.localPath || item.filename" class="image-card" :class="{ 'is-favorited': isCardFavorited(item), 'is-img-favorited': isImageFavorited(item), 'is-selected': isItemSelected(item), 'has-caption': hasCaption(item), 'has-ai-badge': !!aiTagOf(item) }" :title="cardBadgeTitle(item)">
          <div class="thumb-wrap" :class="{ 'is-broken': item.thumbBroken }">
            <img
              class="thumb clickable-thumb"
              :class="{ 'is-loaded': item.loaded }"
              :src="item.thumbUrl"
              :alt="item.filename"
              loading="lazy"
              decoding="async"
              @load="onThumbLoad(item)"
              @error="onThumbError(item)"
              @click="onThumbClick($event, item)"
            />
            <div v-if="!item.loaded" class="thumb-skeleton" aria-hidden="true"></div>
            <span
              v-if="isAnimatedCard(item)"
              class="video-format-watermark"
              :class="`format-${cardFormatLabel(item)}`"
              :aria-label="cardFormatLabel(item).toUpperCase()"
            >
              <svg v-if="isVideoItem(item)" class="format-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M8 5v14l11-7z"/>
              </svg>
              <span v-else class="format-text">GIF</span>
            </span>
            <button
              v-if="selection.enabled"
              class="img-select-toggle"
              :class="{ active: isItemSelected(item) }"
              @click.stop="toggleItemSelection(item)"
              :title="isItemSelected(item) ? '取消选择' : '加入选择'"
            >{{ isItemSelected(item) ? '✓' : '' }}</button>
            <span v-if="hasCaption(item)" class="caption-badge" title="已生成 Caption，点击查看/编辑">📝</span>
            <!-- 收藏画师/角色命中 → 见 .image-card.is-favorited 金色光圈（CSS 实现）。
                 AI 标签 → 见 .image-card.has-ai-badge 紫色光圈。
                 鼠标悬停整张卡片的 title 由 cardBadgeTitle() 汇总，缩略图区不再堆角标。 -->
            <span v-if="item.rating" class="rating-badge" :class="`rating-${item.rating}`" :title="`Danbooru 分级：${item.rating.toUpperCase()}`">{{ item.rating.toUpperCase() }}</span>
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
            <button v-if="item.filename?.toLowerCase().endsWith('.zip') && !item.hasGifCompanion" class="secondary" @click="convertGif(item)" style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;" title="ZIP 动画转 GIF">转GIF</button>
          </div>
        </article>
      </div>

      <div class="pagination-bar cr-pg-bar" v-if="activeCount">
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
        <span class="pg-jump pg-picker-host" ref="pagePickerHost">
          <!-- 块导航（每50页一块）并入页码选择组，与跳转/页码列表同一行 -->
          <template v-if="hasMultipleBlocks">
            <button class="pg-jump-btn pg-block-mini" @click="prevBlock" :disabled="currentBlock <= 0" title="上一块（50页）">‹‹</button>
            <select class="pg-block-select" :value="currentBlock" @change="onBlockSelect" title="选择页码区块（每50页）">
              <option v-for="b in blockLabels" :key="`blk-${b.index}`" :value="b.index">{{ b.label }}</option>
            </select>
            <button class="pg-jump-btn pg-block-mini" @click="nextBlock" :disabled="currentBlock >= totalBlocks - 1" title="下一块（50页）">››</button>
          </template>
          <button class="pg-jump-btn" @click="doJump" title="跳转到输入的页码">跳转</button>
          <input type="number" min="1" :max="activeTotalPages" v-model.number="jumpInput" @keyup.enter="doJump" />
          <button
            class="pg-jump-btn pg-jump-go"
            :class="{ active: pagePicker.open }"
            @click.stop="pagePicker.open = !pagePicker.open"
            :title="pagePicker.open ? '关闭页码列表' : '展开页码列表（10列/行）'"
          >页码</button>
          / {{ activeTotalPages }}
          <Teleport to="body">
          <div v-if="pagePicker.open" class="pg-picker-panel" :style="pagePickerStyle" @click.stop>
            <div class="pg-picker-head">
              <div v-if="hasMultipleBlocks" class="pg-picker-block-nav">
                <button class="ghost pg-block-btn" @click="prevBlock" :disabled="currentBlock <= 0" title="上一块">‹</button>
                <select class="pg-picker-block-select" :value="currentBlock" @change="onBlockSelect">
                  <option v-for="b in blockLabels" :key="`pblk-${b.index}`" :value="b.index">{{ b.label }} 页</option>
                </select>
                <button class="ghost pg-block-btn" @click="nextBlock" :disabled="currentBlock >= totalBlocks - 1" title="下一块">›</button>
                <span class="pg-picker-meta">第 {{ currentBlock + 1 }} / {{ totalBlocks }} 块</span>
              </div>
              <span v-else>共 {{ activeTotalPages }} 页 · 点击跳转</span>
              <button class="ghost" @click="pagePicker.open = false">×</button>
            </div>
            <div class="pg-picker-grid">
              <button
                v-for="n in blockPageNumbers"
                :key="`picker-${n}`"
                class="pg-picker-cell"
                :class="{ active: n === activePage }"
                @click="gotoPage(n); pagePicker.open = false"
              >{{ n }}</button>
            </div>
          </div>
          </Teleport>
        </span>
      </div>
    </section>

    <!-- 角色 / 画师 chip 右键弹出的迷你菜单：编辑词条（仅角色） / 复制
         Teleport 到 body 避免被 viewer overlay / translate modal 的 z-index 遮住。
         mousedown / scroll / Esc / blur / resize 都已在 onMounted 里 dismiss。 -->
    <Teleport to="body">
      <div
        v-if="charContextMenu.open"
        class="char-ctx-menu"
        :style="{ left: charContextMenu.x + 'px', top: charContextMenu.y + 'px' }"
        @click.stop
        @contextmenu.prevent
      >
        <div class="char-ctx-raw" :title="charContextMenu.rawTag">{{ charContextMenu.rawTag }}</div>
        <button v-if="charContextMenu.kind === 'character'" class="char-ctx-item" @click="charMenuEditDictionary">编辑词条</button>
        <button class="char-ctx-item" @click="charMenuCopyRawTag">复制</button>
        <button class="char-ctx-item" @click="charMenuSearchTag">搜索 Tag</button>

        <!-- 多选区：仅当右键命中的 tag 已在 tagMultiSelect 集合里时显示。
             集合大小 >=1 时出现 divider + 复制 N / 搜索 N。 -->
        <template v-if="charContextMenu.isInMultiSelect && charContextMenu.multiSelectCount > 0">
          <div class="char-ctx-divider"></div>
          <button class="char-ctx-item" @click="charMenuCopyMultipleTags">复制 {{ charContextMenu.multiSelectCount }} 个 Tag</button>
          <button class="char-ctx-item" @click="charMenuSearchMultipleTags">用 {{ charContextMenu.multiSelectCount }} 个 Tag 搜索</button>
        </template>
      </div>
    </Teleport>

    <SelectionListModal
      v-model="selectionListOpen"
      :selection-size="selection.ids.size"
      :current-entries="selectionInCurrentDate"
      :other-ids="selectionOtherDates"
      @jump-to="jumpToSelected"
      @remove="removeFromSelection"
    />

    <CryptoToolModal
      :state="cryptoTool"
      :selection-count="selection.ids.size"
      @update:open="closeCryptoTool"
      @load-from-selection="loadSelectionToCryptoInput"
      @notify="({ message, type }) => showToast(message, type)"
    />

    <div v-if="viewer.open" class="viewer-overlay" @click.self="closeViewer" @mousemove="onViewerMouseMove" @mouseleave="viewer.toolbarHovered = false">
      <div
        ref="viewerToolbarRef"
        class="viewer-toolbar"
        :class="{ 'is-hidden': !viewerToolbarVisible }"
        @mouseenter="viewer.toolbarHovered = true"
        @mouseleave="viewer.toolbarHovered = false"
      >
        <div class="viewer-toolbar-info">
          <div class="viewer-meta-block">
            <span class="viewer-meta-label">画师</span>
            <template v-for="(token, artistIndex) in (viewerItem?.artistTokens?.length ? viewerItem.artistTokens : ['未知'])" :key="`v-artist-${token}-${artistIndex}`">
              <button
                class="meta-link author-link token-chip viewer-token-chip"
                :class="{
                  'is-favorited-chip': favoritedArtistSet.has(token),
                  'is-multi-selected': isTagMultiSelected(rawArtistTag(viewerItem, artistIndex))
                }"
                @click="onViewerTagClick($event, viewerItem, artistIndex, 'artist')"
                @contextmenu="onArtistContextMenu($event, viewerItem, artistIndex)"
                :title="token === '未知' ? `搜索同画师作品：${token}` : `左键搜索同画师；Ctrl+左键加入多选；右键菜单：${token}`"
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
            <template v-for="(token, charIndex) in viewerItem.characterTokens" :key="`v-char-${token}-${charIndex}`">
              <button
                class="meta-link token-chip viewer-token-chip"
                :class="{
                  'is-favorited-chip': favoritedCharacterSet.has(token),
                  'is-multi-selected': isTagMultiSelected(rawCharacterTag(viewerItem, charIndex))
                }"
                @click="onViewerTagClick($event, viewerItem, charIndex, 'character')"
                @contextmenu="onCharacterContextMenu($event, viewerItem, charIndex)"
                :title="`左键搜索同角色；Ctrl+左键加入多选；右键菜单：${token}`"
              >{{ token.includes(' [') ? token.split(' [')[0] : token }}</button>
              <button
                class="meta-link author-fav-btn viewer-fav-star"
                @click.stop="openCharacterFavoriteDialog(token)"
                title="加入角色收藏（按 source_hint 合并分组）"
              >★</button>
            </template>
          </div>
        </div>
        <!-- 多选态指示器：仅在 tagMultiSelect 非空时显示；附"清空"按钮和 Esc 提示 -->
        <div v-if="tagMultiSelect.size > 0" class="viewer-multiselect-indicator">
          <span>已选 <strong>{{ tagMultiSelect.size }}</strong> 个 tag</span>
          <button class="ghost mini" @click="clearTagMultiSelect">清空</button>
          <span class="hint">Esc 退出选择</span>
        </div>
        <div class="button-row compact viewer-actions">
          <button class="secondary" @click="stepViewer(-1)" :disabled="viewerIndex <= 0">上一张</button>
          <button class="secondary" @click="stepViewer(1)" :disabled="viewerIndex >= viewerItems.length - 1">下一张</button>
          <button
            class="viewer-fav-btn"
            :class="{ active: viewerItem && isImageFavorited(viewerItem) }"
            @click="viewerItem && toggleImageFavorite(viewerItem)"
            :disabled="!viewerItem"
            :title="viewerItem && isImageFavorited(viewerItem) ? '取消图片收藏' : '加入图片收藏'"
          >{{ viewerItem && isImageFavorited(viewerItem) ? '♥ 已收藏' : '♡ 收藏' }}</button>
          <button v-if="viewerItem?.filename?.toLowerCase().endsWith('.zip') && !viewerItem?.hasGifCompanion" class="secondary" @click="convertGif(viewerItem)" style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;">转GIF</button>
          <button class="secondary" @click="copyViewerImage" :disabled="!viewerItem?.localPath" title="复制图片到剪贴板，最长边不超过 2000px（Ctrl+C）">复制图片</button>
          <button
            @click="viewerItem && emit('caption-image', viewerItem)"
            :style="hasCaption(viewerItem)
              ? 'background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;'
              : 'background: linear-gradient(135deg, #8b5cf6, #6d28d9); border: none; color: white;'"
            :title="hasCaption(viewerItem) ? '已生成 Caption，点击查看/编辑' : '为这张图生成 AI 描述'"
          >{{ hasCaption(viewerItem) ? 'Caption ✓' : 'Caption' }}</button>
          <button @click="editItem(viewerItem)" style="background: linear-gradient(135deg, var(--accent), var(--accent-deep)); border: none; color: white;">编辑图片</button>
        </div>
      </div>

      <!-- 始终显示的右上角信息小栏：计数 / 适应窗口 / 固定信息栏。
           独立于顶部置顶 toolbar，不随 viewerToolbarVisible 收起 -->
      <div class="viewer-corner-info">
        <button class="viewer-corner-row viewer-corner-btn viewer-corner-close" @click="closeViewer" title="关闭大图（Esc）">× 关闭</button>
        <div class="viewer-corner-row viewer-corner-counter">
          <span class="viewer-corner-counter-label">第</span>
          <input
            class="viewer-jump-input viewer-corner-jump"
            type="number"
            min="1"
            :max="viewerItems.length"
            :value="viewerIndex + 1"
            @keyup.enter="onViewerJump($event)"
            @change="onViewerJump($event)"
            title="输入并回车跳转到指定张数"
          />
          <span class="viewer-corner-counter-label">/ {{ viewerItems.length }} 张</span>
          <span v-if="(viewerItem?.score || 0) > 0" class="viewer-score">★ {{ viewerItem.score }}</span>
          <span v-if="(viewerItem?.favCount || 0) > 0" class="viewer-fav">♥ {{ viewerItem.favCount }}</span>
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

      <!-- 左右切换箭头：默认半透明，悬浮变明显；在边界自动隐藏 -->
      <button
        v-show="viewerIndex > 0"
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
        v-show="viewerIndex < viewerItems.length - 1"
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

    <div class="toast-stack" aria-live="polite">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="toast-overlay"
        :class="t.type"
      >
        <span class="toast-msg">{{ t.msg }}</span>
        <button class="toast-close" type="button" @click="dismissToast(t.id)" aria-label="关闭提示">×</button>
      </div>
    </div>

    <!-- 教程 Modal -->
    <TutorialsModal
      v-model="tutorialsModal.open"
      :safe-mode="safeMode"
      @notify="({ message, type }) => showToast(message, type)"
    />

    <!-- Tag Browse Overlay: 按 tag 预览缩略图，勾选后下载。
         ⚠ 模板里 browse 是 ref 自动解包后的对象本身，不要写 .value（会 TypeError 把 runBrowseSearch 整个截断） -->
    <BrowseOverlay
      :state="browse"
      :filtered="browseFiltered"
      :selected-count="browseSelectedCount"
      :safe-mode="safeMode"
      :select-all-label="browseSelectAllLabel"
      :cross-page-count="browseCrossPageCount"
      :multi-page="browseMultiPage"
      :tag-search-history="tagSearchHistory"
      :saved-tags="savedTags"
      @update:open="closeBrowse"
      @load-collected="(d, p = 1) => { browse.selectAllPhase = 0; loadCollectedIds(d, p); }"
      @run-search="(p = 1) => { browse.selectAllPhase = 0; runBrowseSearch(p); }"
      @refresh="refreshBrowsePage"
      @go-page="browseGoPage"
      @go-to-page="browseGoToPage"
      @toggle-select="toggleBrowseSelect"
      @select-all-visible="browseSelectAllVisible"
      @clear-selection="browseClearSelection"
      @open-selection-list="openBrowseSelectionList"
      @open-multi-page="openBrowseMultiPage"
      @close-multi-page="closeBrowseMultiPage"
      @multi-page-action="browseMultiPageAction"
      @update:multi-from="v => (browseMultiPage.from = v)"
      @update:multi-to="v => (browseMultiPage.to = v)"
      @download-selected="downloadBrowseSelected"
      @pick-tag-history="pickTagSearchHistory"
      @remove-tag-history="removeTagSearchHistoryEntry"
      @clear-tag-history="clearTagSearchHistory"
      @add-saved-tag="addSavedTag"
      @remove-saved-tag="removeSavedTag"
    />

    <!-- Tag 浏览的跨页已选清单（缩略图 + 单条移除） -->
    <BrowseSelectionModal
      v-model="browseSelectionListOpen"
      :selected-entries="browseSelectedEntries"
      :total-count="browseSelectedCount"
      :on-page-count="browseOnPageCount"
      :cross-page-count="browseCrossPageCount"
      @remove="browseRemoveFromSelection"
    />

    <!-- Translation Modal (list of untranslated characters) -->
    <TranslationModal
      :state="translationModal"
      :filtered="filteredUntranslated"
      :selected-date="gallery.selectedDate"
      @update:open="closeTranslationModal"
      @search="searchCharacterDictionary"
      @open-detail="openTranslateDetail"
      @import="importTranslationDict"
    />

    <!-- Translate Detail Modal (single character) -->
    <TranslateDetailModal
      :state="translateDetail"
      @update:open="closeTranslateDetail"
      @refresh-wiki="fetchCharacterSource"
      @save="saveTranslation"
      @notify="({ message, type }) => showToast(message, type)"
    />

    <!-- 刷新范围 Modal -->
    <RefreshRangeModal
      :state="rangeRefresh"
      :active-total-pages="activeTotalPages"
      :active-page="activePage"
      :page-size="gallery.pageSize"
      :count="rangeRefreshCount"
      :is-running="refresh.isRunning"
      @update:open="closeRangeRefreshDialog"
      @confirm="startRefreshScoresRange"
    />

    <!-- 跨盘合并 viewer_data Modal（替代 Electron 不支持的 window.prompt/confirm） -->
    <MergeViewerDataModal
      :state="mergeViewerDataModal"
      :is-busy="task.isRunning || task.isStopping"
      @update:open="closeMergeViewerDataModal"
      @success="onMergeViewerDataSuccess"
    />

    <!-- 加入画师收藏 Modal -->
    <ArtistFavoriteModal
      :state="favoriteDialog"
      :group-list="favGroupList"
      @update:open="closeFavoriteDialog"
      @toggle-group="toggleFavGroup"
      @create-group="createFavGroupInline"
      @save="saveFavoriteDialog"
    />

    <!-- 加入角色收藏 Modal -->
    <CharacterFavoriteModal
      :state="charFavoriteDialog"
      :group-list="charFavGroupList"
      @update:open="closeCharacterFavoriteDialog"
      @toggle-group="toggleCharFavGroup"
      @create-group="createCharFavGroupInline"
      @save="saveCharacterFavoriteDialog"
    />
  </div>
</template>

<style scoped>
/* 左栏由全局 340px 缩到 300px，把多出来的 40px 让给右侧图片网格 */
.crawler-layout {
  grid-template-columns: 300px minmax(0, 1fr);
}
.crawler-layout.gallery-hidden {
  /* 隐藏图库后只剩左栏一列：列宽固定为 300px（与有图库时一致），
     不再扩展成 minmax(320,440px)，避免「点隐藏图库后整列突然变宽」、
     头排按钮也跟着视觉变宽的违和感。justify-content: center 仍保留，
     让单一列在整行里居中、两侧留白对称。 */
  grid-template-columns: 300px;
  justify-content: center;
}
@media (max-width: 1400px) {
  .crawler-layout {
    grid-template-columns: 270px minmax(0, 1fr);
  }
  .crawler-layout.gallery-hidden {
    grid-template-columns: 270px;
    justify-content: center;
  }
}

.gallery-toggle-btn {
  font-size: 12px;
  padding: 4px 9px;
  white-space: nowrap;
  /* 与 safe-mode / proxy 按钮统一成胶囊外形，颜色保持中性一致（不随显示/隐藏切换换色，
     只在 hover 时轻微加深），避免这颗按钮在头部按钮行里显得格格不入 */
  border-radius: 999px;
  font-weight: 600;
  border: 1px solid rgba(var(--accent-rgb), 0.35);
  background: rgba(var(--accent-rgb), 0.1);
  color: var(--accent-deep);
  transition: background 0.18s, border-color 0.18s;
}
.gallery-toggle-btn:hover:not(:disabled) {
  background: rgba(var(--accent-rgb), 0.2);
  border-color: rgba(var(--accent-rgb), 0.45);
}
.control-panel .compact-head {
  flex-direction: column;
  align-items: stretch;
}
.control-panel .compact-head > div:first-child {
  min-width: 0;
}
.crawler-head-actions {
  display: flex;
  /* 头排 6 个按钮 + 1 个下载协程输入框。在 270–300px 的窄左栏里根本塞不下，
     用 flex-wrap 让它们自然换行；justify-content: flex-end 让短行（最后一行不满时）
     整体靠右，避免出现"前面几个按钮孤零零顶在左边"的视觉断裂感。 */
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 6px 8px;
  margin-top: 10px;
  row-gap: 8px;
}
.crawler-head-actions button {
  min-width: 0;
  font-size: 12px;
  padding: 5px 11px;
  border-radius: 999px;
  white-space: nowrap;
  flex: 0 0 auto;
}
/* 下载协程输入框用 inline label 形式（.concurrency-field-inline），
   在 flex 行里也保持自然宽度。 */
.crawler-head-actions .concurrency-field-inline {
  flex: 0 0 auto;
}
.crawler-head-actions .hosts-btn {
  color: #ff9800;
}

/* 教程弹窗里的卡片已随 TutorialsModal.vue 一起搬走 */

.mode-selector {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
}
.mode-chip {
  min-width: 0;
  min-height: 44px;
  padding: 8px 4px;
  font-size: 13px;
  line-height: 1.15;
  white-space: nowrap;
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
  /* 内容超高时不外溢到圆角卡片外：中段 .control-scroll 内部滚动消化；
     放大态日志用 absolute inset:0 仍正常（overflow:hidden 不影响子级绝对定位铺满） */
  overflow: hidden;
}

/* 三段式左栏：.panel-head + .mode-selector 固定在顶部，.modern-log-wrapper 固定在底部，
   中间所有表单/按钮/失败横幅/多任务队列/状态/错误条都进 .control-scroll，超高时内部滚动 */
.control-panel > .panel-head,
.control-panel > .mode-selector,
.control-panel > .modern-log-wrapper {
  flex: 0 0 auto;
}
.control-scroll {
  flex: 1 1 auto;
  min-height: 0;          /* flex 子项可滚动的关键，否则会撑破父级 */
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  margin-top: 10px;
  padding-right: 4px;     /* 给滚动条留位，避免压住内容 */
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
.control-panel .panel-head h2 {
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
  transition: box-shadow 0.14s ease, border-color 0.14s ease, filter 0.14s ease;
}
/* 卡片悬停效果对齐按钮：不做上浮位移，只用亮度 + 阴影 + 边框的轻微变化 */
.image-card:hover {
  transform: none;
  filter: brightness(0.97);
  border-color: rgba(var(--accent-rgb), 0.28);
  box-shadow: 0 6px 16px rgba(30, 41, 82, 0.10);
}
.image-card:active {
  filter: brightness(0.94);
}

/* 卡片最小宽度由 gallery.cardSize 通过 inline --card-min-w 注入，
   覆盖全局 .gallery-grid 的 minmax(180px, 1fr)。 */
.gallery-grid {
  grid-template-columns: repeat(auto-fill, minmax(var(--card-min-w, 180px), 1fr));
  transition: opacity 0.14s ease, filter 0.14s ease;
}
.gallery-grid.is-switching {
  opacity: 0.46;
  filter: saturate(0.72);
  pointer-events: none;
}
.gallery-switch-status {
  flex: 0 0 auto;
  margin: 4px 0 8px;
  padding: 7px 11px;
  border: 1px solid rgba(59, 130, 160, 0.28);
  border-radius: 9px;
  background: rgba(59, 130, 160, 0.1);
  color: #2a6f8e;
  font-size: 12px;
  font-weight: 700;
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
.video-card-thumb {
  display: block;
  object-fit: cover;
  background: linear-gradient(135deg, #252a31, #11151a);
}
.video-format-watermark {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 4;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 54px;
  min-height: 36px;
  padding: 6px 12px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.28);
  color: #fff;
  pointer-events: none;
  backdrop-filter: blur(4px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
}
.video-format-watermark .format-icon {
  width: 22px;
  height: 22px;
  color: #fff;
}
.video-format-watermark .format-text {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.12em;
  line-height: 1;
  color: #fff;
}

/* gallery-head 改成两行竖排：
   第 1 行 = 标题行（日历触发按钮在左，统计文本贴右上角）
            —— 日历的 trigger 本身就显示当前日期，替代了原来的 h2，省一行
   第 2 行 = 工具栏（所有筛选/排序/刷新/搜索/翻译） */
.gallery-head {
  flex-direction: column;
  align-items: stretch;
  gap: 5px;
}
.gallery-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
/* 日历提到 title-row 内部：原 .calendar / .calendar-toolbar 各自的 margin-bottom
   是为外置布局（toolbar 下方有日历格子 / 列表）准备的，放进 flex 行后会撑高行高，
   这里清掉。位置由父 flex 控制，自身不需要再贡献外边距。 */
.gallery-title-row .calendar { margin-bottom: 0; }
.gallery-title-row .calendar-toolbar { margin-bottom: 0; gap: 6px; }
/* 标题行里的日历：日期是这里的主元素，字号 / 高度比工具栏略大一档（12→13/14px、30→34px），
   撑得起"主标题"的视觉重量。min-width 也加宽一档避免文字贴边。 */
.title-row-calendar .small-btn,
.title-row-calendar .today-btn,
.title-row-calendar .calendar-trigger {
  font-size: 13px;
  font-weight: 600;
  padding: 7px 14px;
  height: 34px;
  min-width: 86px;
}
.title-row-calendar .calendar-trigger {
  font-size: 14px;
  font-weight: 700;
  min-width: 184px;
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
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 卡片边框颜色含义图例：与 image-card.is-favorited / .has-ai-badge 的 box-shadow 同色 */
.border-legend {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin-left: 4px;
  color: var(--muted);
  font-size: 11px;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: help;
}
.legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: 0 0 auto;
}
.legend-dot-fav {
  background: #f59e0b;
  box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.35), 0 0 6px rgba(245, 158, 11, 0.55);
}
.legend-dot-ai {
  background: #a855f7;
  box-shadow: 0 0 0 1px rgba(168, 85, 247, 0.35), 0 0 6px rgba(168, 85, 247, 0.55);
}

/* 工具栏紧凑化：尽量一行装下所有控件 */
.gallery-tools {
  gap: 6px;
  justify-content: flex-start;
  font-size: 12px;
  align-items: center;
}
.gallery-tools select.search-input,
.gallery-tools .search-input-with-clear,
.gallery-tools .tool-btn,
.gallery-tools .hot-toggle,
.gallery-tools .refresh-btn {
  font-size: 12px;
  padding: 5px 11px;
  height: 30px;
  line-height: 1;
  white-space: nowrap;
  flex: 0 0 auto;
}
.gallery-tools .gallery-sort-select {
  font-weight: 600;
}

/* 搜索框：静态宽度足够显示占位/输入，focus 再拉宽；可伸缩填充剩余空间 */
.gallery-tools .search-input-wrap {
  flex: 1 1 180px;
  min-width: 150px;
  max-width: 280px;
}
.gallery-tools .search-input-wrap .search-input {
  width: 100%;
  transition: none;
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
  max-height: min(46vh, 390px);
  border-radius: 18px;
}
.viewer-toolbar-info {
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
/* 多选态：淡蓝半透明底 + 白字 + 淡蓝描边，整体不抢眼但能跟白底/金底明显区分 */
.viewer-token-chip.is-multi-selected {
  background: rgba(var(--accent-rgb), 0.22);
  color: #fff;
  border: 1.5px solid rgba(var(--accent-rgb), 0.45);
  font-weight: 700;
  padding: 1.5px 7.5px;
}
.viewer-token-chip.is-multi-selected:hover {
  background: rgba(var(--accent-rgb), 0.38);
  color: #fff;
  border-color: rgba(var(--accent-rgb), 0.7);
}
.viewer-token-chip.is-multi-selected.is-favorited-chip {
  /* 收藏 + 多选：多选色为主（金底被多选淡蓝半透明叠加 → 浅金蓝混合色） */
  background: rgba(var(--accent-rgb), 0.22);
  color: #fff;
  border-color: rgba(var(--accent-rgb), 0.45);
}

/* viewer 工具栏里的多选态指示器（"已选 N 个 tag"）—— 跟多选 chip 同款淡蓝 */
.viewer-multiselect-indicator {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: 10px;
  padding: 3px 10px;
  background: rgba(var(--accent-rgb), 0.22);
  border: 1px solid rgba(var(--accent-rgb), 0.45);
  border-radius: 12px;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}
.viewer-multiselect-indicator .hint {
  color: rgba(255, 255, 255, 0.7);
  font-size: 11px;
}
/* 指示器里的"清空"按钮：在淡蓝底上用白字 + 半透明白边，跟指示器一个色系 */
.viewer-multiselect-indicator button {
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.35);
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 600;
  box-shadow: none;
  transform: none;
}
.viewer-multiselect-indicator button:hover {
  background: rgba(255, 255, 255, 0.3);
  color: #fff;
  border-color: rgba(255, 255, 255, 0.55);
  filter: none;
  box-shadow: none;
  transform: none;
}

/* 角色右键菜单里的分隔线（单 tag 区 / 多选区 之间） */
.char-ctx-divider {
  height: 1px;
  margin: 4px 6px;
  background: var(--line, rgba(0, 0, 0, 0.1));
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

/* 角色 chip 右键迷你菜单：Teleport 到 body，position: fixed 走视口坐标
   风格跟 .pg-picker-panel / .selection-list-card 一致（淡米黄卡片 + 圆角） */
.char-ctx-menu {
  position: fixed;
  z-index: 10050;
  min-width: 160px;
  background: var(--panel, #fdf6e3);
  border: 1px solid var(--line, rgba(0, 0, 0, 0.12));
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.22), 0 2px 4px rgba(0, 0, 0, 0.08);
  padding: 4px;
  font-size: 13px;
  user-select: none;
  animation: char-ctx-pop 0.12s ease-out;
}
@keyframes char-ctx-pop {
  from { opacity: 0; transform: translateY(-4px) scale(0.98); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
.char-ctx-raw {
  padding: 5px 10px 6px;
  font-family: Consolas, monospace;
  font-size: 11.5px;
  color: var(--muted, #806a4a);
  border-bottom: 1px solid var(--line, rgba(0, 0, 0, 0.08));
  margin-bottom: 2px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;  /* 截断保留尾部有意义部分 */
  text-align: left;
}
.char-ctx-item {
  display: block;
  width: 100%;
  padding: 6px 10px;
  background: transparent;
  border: 0;
  border-radius: 5px;
  color: var(--ink, #2a1f10);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition: background 0.12s;
}
.char-ctx-item:hover {
  background: rgba(var(--accent-rgb, 196 130 60), 0.14);
}

.viewer-counter-block {
  margin-left: auto;
  gap: 8px;
}

/* 操作按钮行：第 2 行，居中横排，绝不换行 */
.viewer-actions {
  flex-wrap: nowrap !important;
  justify-content: center;
  gap: 10px;
  flex: 0 0 auto;
  overflow-x: auto;
  padding-bottom: 2px;
}
.viewer-actions button {
  flex: 0 0 auto;
  white-space: nowrap;
  /* 加大按钮以适配新增的 Caption 按钮，防止挤压换行 */
  padding: 8px 16px;
  font-size: 13.5px;
  min-height: 36px;
}
.viewer-toolbar {
  padding: 12px 18px;
}

/* ---------------- Caption 悬浮窗口 ---------------- */
.caption-panel.floating {
  position: fixed;
  width: min(480px, 92vw);
  max-height: 88vh;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(250, 246, 239, 0.98));
  border-radius: 14px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(0, 0, 0, 0.08);
  border: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  overflow: hidden;
  /* 高于 viewer overlay 的 9999 / 默认值，确保浮窗在图像之上 */
  z-index: 10025;
  backdrop-filter: blur(6px);
}
/* 浮窗可滚动主体（改动1）：标题栏(header)留在外面当固定拖动把手，其余内容放进这里；
   内容超高时本容器内部滚动，子项 flex-shrink:0 不再被压扁，修复 Stage 2/3 被挤压。 */
.caption-panel-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 4px;
}
.caption-panel-scroll > * {
  flex-shrink: 0;
}
.caption-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: move;
  user-select: none;
  padding: 2px 0 6px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
}
.caption-panel-header h3 {
  margin: 0;
  font-size: 15px;
  color: var(--accent-deep);
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}
.caption-drag-hint {
  font-size: 11px;
  font-weight: normal;
  color: rgba(0, 0, 0, 0.4);
}
.caption-panel-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  font-size: 12px;
  color: var(--ink);
  background: rgba(0, 0, 0, 0.04);
  padding: 6px 10px;
  border-radius: 6px;
}
.caption-panel-meta b {
  color: var(--accent-deep);
}
.caption-panel-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.caption-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  cursor: pointer;
  user-select: none;
}
.caption-panel-actions {
  display: flex;
  gap: 6px;
}
.caption-panel-actions button {
  padding: 6px 12px;
  font-size: 12.5px;
}
.caption-manual {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.5);
  overflow: hidden;
}
.caption-manual-toggle {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 10px;
  background: rgba(139, 92, 246, 0.05);
  border: none;
  font-size: 12.5px;
  color: var(--accent-deep);
  cursor: pointer;
  transition: background 0.15s;
}
.caption-manual-toggle:hover {
  background: rgba(139, 92, 246, 0.12);
}
.caption-manual-arrow {
  font-size: 10px;
  color: rgba(0, 0, 0, 0.45);
}
.caption-manual-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 10px 10px;
  border-top: 1px dashed rgba(0, 0, 0, 0.08);
}
.caption-manual-hint {
  margin: 0;
  font-size: 11.5px;
  color: rgba(0, 0, 0, 0.55);
  line-height: 1.55;
}
.caption-manual-buttons {
  display: flex;
  gap: 6px;
}
.caption-manual-buttons button {
  padding: 6px 10px;
  font-size: 12px;
  flex: 1;
}
.caption-manual-paste {
  width: 100%;
  font-family: Consolas, monospace;
  font-size: 12.5px;
  resize: vertical;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.7);
  outline: none;
  line-height: 1.5;
}
.caption-manual-paste:focus {
  border-color: var(--accent);
}
.caption-manual-apply {
  align-self: flex-end;
  padding: 6px 14px;
  font-size: 12.5px;
  background: linear-gradient(135deg, #10b981, #059669);
  border: none;
  color: white;
  border-radius: 6px;
  cursor: pointer;
}
.caption-manual-apply:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ---------------- Pipeline 手动模式（3 阶段 stepper） ---------------- */
.pipeline-manual {
  background: rgba(139, 92, 246, 0.04);
  border-color: rgba(139, 92, 246, 0.25);
  padding: 8px 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pipeline-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.pipeline-title {
  font-size: 13px;
  color: var(--accent-deep);
  font-weight: 600;
}
.pipeline-badge {
  font-size: 10.5px;
  background: var(--accent);
  color: white;
  padding: 1px 6px;
  border-radius: 4px;
  margin-left: 4px;
  font-weight: 500;
}
.pipeline-reset {
  padding: 3px 8px;
  font-size: 11.5px;
}
.pipeline-progress {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: rgba(0, 0, 0, 0.55);
  padding: 4px 2px;
}
.pipeline-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.08);
  color: rgba(0, 0, 0, 0.45);
  font-size: 11px;
  font-weight: 600;
}
.pipeline-dot.active {
  background: var(--accent);
  color: white;
}
.pipeline-dot.done {
  background: #10b981;
  color: white;
}
.pipeline-dot-label {
  margin-right: 4px;
}
.pipeline-dash {
  color: rgba(0, 0, 0, 0.25);
  margin: 0 2px;
}
.pipeline-tip {
  margin: 0;
  font-size: 11.5px;
  color: rgba(0, 0, 0, 0.6);
  line-height: 1.5;
}
.pipeline-stage {
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.65);
  overflow: hidden;
  transition: border-color 0.15s, opacity 0.15s;
}
.pipeline-stage.active {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.12);
}
.pipeline-stage.done {
  border-color: rgba(16, 185, 129, 0.4);
  background: rgba(16, 185, 129, 0.04);
}
.pipeline-stage.locked {
  opacity: 0.55;
}
.pipeline-stage-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  font-size: 12.5px;
}
.pipeline-stage-title {
  font-weight: 600;
  color: rgba(0, 0, 0, 0.75);
}
.pipeline-stage-status {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
}
.pipeline-stage-status.active {
  background: var(--accent);
  color: white;
}
.pipeline-stage-status.done {
  background: #10b981;
  color: white;
}
.pipeline-stage-status.locked {
  background: rgba(0, 0, 0, 0.08);
  color: rgba(0, 0, 0, 0.5);
}
.pipeline-stage-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 10px 10px;
  border-top: 1px dashed rgba(0, 0, 0, 0.08);
}
.pipeline-stage-buttons {
  display: flex;
  gap: 6px;
}
.pipeline-stage-buttons button {
  flex: 1;
  padding: 6px 10px;
  font-size: 12px;
}
.pipeline-stage-hint {
  margin: 0;
  font-size: 11.5px;
  color: rgba(0, 0, 0, 0.55);
  line-height: 1.5;
}
.pipeline-stage-summary {
  padding: 6px 10px 8px;
  font-size: 11.5px;
  color: rgba(0, 0, 0, 0.55);
  border-top: 1px dashed rgba(0, 0, 0, 0.06);
}
.pipeline-error {
  font-size: 11.5px;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
  padding: 4px 8px;
  border-radius: 4px;
  border-left: 3px solid #dc2626;
}
.pipeline-finish {
  background: linear-gradient(135deg, #6366f1, #4f46e5) !important;
}
.caption-structured {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 6px 0 2px;
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(99, 102, 241, 0.05);
  border: 1px solid rgba(99, 102, 241, 0.15);
}
.caption-structured-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.caption-structured-label {
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: rgba(79, 70, 229, 0.85);
  text-transform: uppercase;
}
.caption-structured-en {
  margin: 0;
  font-family: Consolas, monospace;
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  color: rgba(0, 0, 0, 0.78);
  background: rgba(255, 255, 255, 0.7);
  padding: 6px 8px;
  border-radius: 4px;
}
.caption-structured-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.caption-chip {
  font-size: 11px;
  font-family: Consolas, monospace;
  padding: 2px 7px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(99, 102, 241, 0.25);
  color: rgba(0, 0, 0, 0.7);
}
.caption-chip-editable {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.caption-chip-x {
  border: none;
  background: transparent;
  color: rgba(220, 38, 38, 0.7);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  padding: 0 1px;
}
.caption-chip-x:hover { color: #dc2626; }
.caption-chip-review {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.45);
  color: rgba(146, 64, 14, 0.95);
}
.caption-chip-add {
  border: none;
  background: transparent;
  color: rgba(5, 150, 105, 0.9);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  padding: 0 1px;
}
.caption-chip-add:hover { color: #059669; }
.caption-tags-empty {
  font-size: 11px;
  color: rgba(0, 0, 0, 0.4);
}
.caption-tag-add {
  display: flex;
  gap: 6px;
  margin-top: 4px;
}
.caption-tag-input {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  font-family: Consolas, monospace;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid rgba(99, 102, 241, 0.25);
  background: rgba(255, 255, 255, 0.9);
  color: rgba(0, 0, 0, 0.8);
  outline: none;
}
.caption-tag-input:focus { border-color: rgba(99, 102, 241, 0.09); }
.caption-hint {
  font-size: 11.5px;
  color: rgba(0, 0, 0, 0.55);
  background: rgba(139, 92, 246, 0.08);
  padding: 5px 9px;
  border-radius: 6px;
  border-left: 3px solid #8b5cf6;
}
.caption-empty {
  padding: 22px 12px;
  text-align: center;
  color: rgba(0, 0, 0, 0.5);
  font-size: 13px;
  background: rgba(0, 0, 0, 0.03);
  border-radius: 8px;
}
.caption-text-area {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  line-height: 1.8;
  font-size: 13.5px;
  color: var(--ink);
  white-space: pre-wrap;
  word-break: break-word;
  overflow-y: auto;
  max-height: 40vh;
  user-select: text;
  cursor: text;
}
.caption-error {
  background: rgba(239, 68, 68, 0.18);
  color: #b91c1c;
  border-bottom: 2px solid #ef4444;
  border-radius: 3px;
  padding: 0 2px;
  cursor: pointer;
}
.caption-error:hover {
  background: rgba(239, 68, 68, 0.3);
}
.caption-error-list {
  background: rgba(239, 68, 68, 0.06);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  max-height: 16vh;
  overflow-y: auto;
}
.caption-error-list-title {
  font-weight: 700;
  color: #b91c1c;
  margin-bottom: 3px;
}
.caption-error-list ol {
  margin: 0;
  padding-left: 18px;
}
.caption-error-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 2px 0;
}
.caption-error-list li button {
  font-size: 11px;
  padding: 1px 6px;
  color: #b91c1c;
}
.caption-message {
  font-size: 12px;
  color: var(--accent-deep);
  background: rgba(0, 0, 0, 0.04);
  border-radius: 6px;
  padding: 5px 9px;
}

/* 卡片上的「已生成 caption」角标 */
.caption-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 8px;
  box-shadow: 0 2px 6px rgba(16, 185, 129, 0.4);
  pointer-events: none;
  z-index: 3;
  user-select: none;
}
.image-card.has-caption {
  outline: 2px solid rgba(16, 185, 129, 0.55);
  outline-offset: -2px;
}

/* 收藏画师/角色命中：卡片边缘金色光圈（光晕 + 内色环 + 慢呼吸）。
   不用 outline 的原因：与 .image-card.has-caption 的绿色 outline 互斥，
   而 box-shadow 各层独立、可叠加，同时能再扩出"远场柔光"。
   overflow: hidden 不会裁掉 box-shadow（shadow 在 element 外部）。 */
.image-card.is-favorited {
  box-shadow:
    0 0 0 2px rgba(245, 158, 11, 0.95),
    0 0 18px 2px rgba(245, 158, 11, 0.55),
    0 0 36px 6px rgba(245, 158, 11, 0.18);
  animation: fav-halo-pulse 3.2s ease-in-out infinite;
}

/* AI 标签（ai-assisted / ai-generated）：紫色光圈。
   颜色刻意与 .image-card.has-caption 绿、收藏金、rating 红/橙/绿全部分开。 */
.image-card.has-ai-badge {
  box-shadow:
    0 0 0 2px rgba(168, 85, 247, 0.95),
    0 0 18px 2px rgba(168, 85, 247, 0.6),
    0 0 36px 6px rgba(168, 85, 247, 0.2);
  animation: ai-halo-pulse 3.2s ease-in-out infinite;
}

/* 同时命中：内金环 + 紧贴的紫外环 + 紫主光 + 金远光。
   两种属性都一眼可辨；特异性更高（两个 class 同时命中），
   覆盖上面两个单色规则的 box-shadow / animation。 */
.image-card.is-favorited.has-ai-badge {
  box-shadow:
    0 0 0 2px rgba(245, 158, 11, 0.95),
    0 0 0 4px rgba(168, 85, 247, 0.85),
    0 0 20px 3px rgba(168, 85, 247, 0.55),
    0 0 40px 6px rgba(245, 158, 11, 0.22);
  animation: dual-halo-pulse 3.4s ease-in-out infinite;
}

/* 收藏/AI 卡片悬停：保留 .image-card:hover 的 brightness/filter 反馈，
   不在这里重写 box-shadow —— 动画期间动画的 box-shadow 永远胜过 :hover 的静态声明，
   写两套只会互相覆盖毫无意义。 */

/* 呼吸动画：仅微调外发光强度，色环始终保持可见（不缩成 0）。
   prefers-reduced-motion 关闭动画避免对前庭敏感用户造成困扰。 */
@keyframes fav-halo-pulse {
  0%, 100% {
    box-shadow:
      0 0 0 2px rgba(245, 158, 11, 0.92),
      0 0 16px 2px rgba(245, 158, 11, 0.5),
      0 0 32px 6px rgba(245, 158, 11, 0.16);
  }
  50% {
    box-shadow:
      0 0 0 2px rgba(245, 158, 11, 1),
      0 0 22px 3px rgba(245, 158, 11, 0.72),
      0 0 44px 8px rgba(245, 158, 11, 0.28);
  }
}
@keyframes ai-halo-pulse {
  0%, 100% {
    box-shadow:
      0 0 0 2px rgba(168, 85, 247, 0.92),
      0 0 16px 2px rgba(168, 85, 247, 0.55),
      0 0 32px 6px rgba(168, 85, 247, 0.18);
  }
  50% {
    box-shadow:
      0 0 0 2px rgba(168, 85, 247, 1),
      0 0 22px 3px rgba(168, 85, 247, 0.78),
      0 0 44px 8px rgba(168, 85, 247, 0.3);
  }
}
@keyframes dual-halo-pulse {
  0%, 100% {
    box-shadow:
      0 0 0 2px rgba(245, 158, 11, 0.92),
      0 0 0 4px rgba(168, 85, 247, 0.82),
      0 0 18px 3px rgba(168, 85, 247, 0.5),
      0 0 36px 6px rgba(245, 158, 11, 0.2);
  }
  50% {
    box-shadow:
      0 0 0 2px rgba(245, 158, 11, 1),
      0 0 0 4px rgba(168, 85, 247, 0.95),
      0 0 24px 4px rgba(168, 85, 247, 0.7),
      0 0 46px 9px rgba(245, 158, 11, 0.32);
  }
}
@media (prefers-reduced-motion: reduce) {
  .image-card.is-favorited,
  .image-card.has-ai-badge,
  .image-card.is-favorited.has-ai-badge {
    animation: none;
  }
}

/* Danbooru 分级角标：左下角，配色与 Tag 浏览的 rating 徽章一致 */
.rating-badge {
  position: absolute;
  left: 6px;
  bottom: 6px;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 7px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
  pointer-events: none;
  z-index: 3;
  user-select: none;
}
.rating-badge.rating-e { background: rgba(220, 50, 50, 0.9); }
.rating-badge.rating-q { background: rgba(220, 150, 40, 0.9); }
.rating-badge.rating-s { background: rgba(50, 160, 90, 0.9); }

/* 模式切换（标记 / 输入） */
.caption-mode-switch {
  display: inline-flex;
  background: rgba(0, 0, 0, 0.05);
  border-radius: 8px;
  padding: 2px;
  gap: 2px;
}
.caption-mode-switch button {
  border: none;
  background: transparent;
  padding: 5px 10px;
  font-size: 12px;
  border-radius: 6px;
  cursor: pointer;
  color: rgba(0, 0, 0, 0.6);
}
.caption-mode-switch button.active {
  background: #fff;
  color: var(--accent-deep);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
  font-weight: 700;
}
/* 输入模式：可直接编辑的文本框（改动3，替代旧的绿字替换机制） */
.caption-text-edit {
  width: 100%;
  box-sizing: border-box;
  min-height: 160px;
  resize: vertical;
  background: linear-gradient(180deg, #fff, #f0fdf4);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  line-height: 1.8;
  font-size: 13.5px;
  font-family: inherit;
  color: var(--ink);
  outline: none;
}
.caption-text-edit:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(77, 145, 90, 0.15);
}

/* 标记汇总区（仅红色错误标记；绿色替换记录已随输入模式重做移除） */
.caption-marks-summary {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.caption-error-list-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-weight: 700;
  margin-bottom: 3px;
  color: #b91c1c;
}
.caption-error-list-title button {
  font-size: 11px;
  padding: 1px 6px;
  font-weight: normal;
}

/* Modern Log UI — 进度条版本（替代旧文本日志） */
.modern-log-wrapper {
  margin-top: 12px;
  background: rgba(26, 20, 15, 0.96);
  border-radius: 16px;
  border: 1px solid rgba(0, 0, 0, 0.1);
  box-shadow: 0 8px 32px rgba(30, 41, 82, 0.15);
  display: flex;
  flex-direction: column;
}

.modern-log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0) 100%);
  user-select: none;
  border-radius: 16px;
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
  transition: background 0.2s, box-shadow 0.2s;
}
.status-dot.is-active {
  background: #51cf66;
  box-shadow: 0 0 0 2px rgba(81, 207, 102, 0.2);
}

.log-title {
  color: #fff;
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0.5px;
}

/* 进度条面板：紧贴 header 下方，单条横向 bar + 1 行汇总文本。
   灰色"未下载"段由 bar 容器自身背景承担，不渲染独立 div（节省一个空节点）。 */
.progress-panel {
  padding: 4px 16px 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.progress-phase {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.7);
  letter-spacing: 0.3px;
}
.progress-phase .running-failure-hint {
  color: #fbbf24;
  font-weight: 600;
  padding: 1px 6px;
  margin-left: 2px;
  border-radius: 4px;
  background: rgba(251, 191, 36, 0.12);
}

/* 「需手动重试的页」小提示：简洁不抢眼，比之前的"重试这些页"按钮 + auto-pause 横幅轻很多。
   失败页变化时 syncStatusOnce 驱动更新，× 关闭（dismissedHintSignatures 记忆）。 */
.retry-pages-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  margin-top: 8px;
  padding: 6px 10px 6px 10px;
  border: 1px solid rgba(251, 191, 36, 0.45);
  border-radius: 8px;
  background: rgba(251, 191, 36, 0.08);
  color: #fcd34d;
  font-size: 11.5px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  letter-spacing: 0.2px;
  line-height: 1.3;
}
.retry-pages-hint .retry-pages-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}
.retry-pages-hint .retry-pages-close {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.10);
  color: inherit;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}
.retry-pages-hint .retry-pages-close:hover {
  background: rgba(255, 255, 255, 0.22);
}
.retry-pages-hint .retry-pages-close:focus-visible {
  outline: 1px solid currentColor;
  outline-offset: 1px;
}

.progress-bar {
  position: relative;
  height: 8px;
  width: 100%;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  overflow: hidden;
  display: flex;
}
.progress-seg {
  height: 100%;
  transition: width 0.25s cubic-bezier(0.25, 0.8, 0.25, 1);
}
.progress-seg-success {
  background: #10b981;
  border-radius: 999px 0 0 999px;
}
/* 唯一一段（100% 成功 / 100% 失败）时让它仍带圆角，避免左/右边角方掉 */
.progress-seg-success:first-child:last-child { border-radius: 999px; }
.progress-seg-fail {
  background: #dc2626;
  border-radius: 0 999px 999px 0;
}
.progress-seg-fail:last-child:not(:first-child) { border-radius: 0 999px 999px 0; }
/* 抓取 ID 阶段的页进度条：单色（蓝/青），与下载阶段的绿色 success 段区分。 */
.progress-seg-page {
  background: #38bdf8;
  border-radius: 999px;
}

.progress-summary {
  font-size: 11.5px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: rgba(255, 255, 255, 0.75);
  letter-spacing: 0.3px;
  line-height: 1.4;
}
.progress-fail-tail { color: #fca5a5; }
.progress-success-tail { color: #86efac; }

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

@keyframes slideIn {
  from { opacity: 0; transform: translateX(-5px); }
  to { opacity: 1; transform: translateX(0); }
}

/* Toast 置顶：贴在程序最顶端，多条垂直堆叠，每条带 X 手动关。
   旧版是单条 + 24px 顶距 + 3s 自动消失，新事件会盖掉旧事件，用户容易漏看。 */
.toast-stack {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  /* 必须高于所有 modal：合并/翻译/收藏等 modal 用的是 10020，10030 留出缓冲 */
  z-index: 10030;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  pointer-events: none;
}
.toast-overlay {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  max-width: min(720px, calc(100vw - 24px));
  padding: 8px 14px;
  border-radius: 10px;
  font-weight: 600;
  font-size: 13px;
  line-height: 1.4;
  box-shadow: 0 6px 20px rgba(0,0,0,0.18);
  animation: fadeInDown 0.25s ease-out;
  pointer-events: auto;
}
.toast-overlay .toast-msg { flex: 1 1 auto; min-width: 0; }
.toast-overlay .toast-close {
  flex: 0 0 auto;
  background: transparent;
  border: none;
  font-size: 18px;
  line-height: 1;
  padding: 0 4px;
  cursor: pointer;
  color: inherit;
  opacity: 0.65;
  border-radius: 6px;
}
.toast-overlay .toast-close:hover { opacity: 1; background: rgba(0,0,0,0.06); }
.toast-overlay.success { background: rgba(212, 237, 218, 0.95); color: #155724; border: 1px solid #c3e6cb; }
.toast-overlay.error   { background: rgba(248, 215, 218, 0.95); color: #721c24; border: 1px solid #f5c6cb; }
.toast-overlay.info    { background: rgba(209, 236, 241, 0.95); color: #0c5460; border: 1px solid #bee5eb; }
.toast-overlay.warning { background: rgba(255, 243, 205, 0.95); color: #856404; border: 1px solid #ffeeba; }

@keyframes fadeInDown {
  from { opacity: 0; transform: translateY(-12px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ---------------- 角色增量翻译弹窗 ---------------- */
.character-dict-search-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  margin-bottom: 10px;
}
.character-dict-search-row .search-input { width: 100%; }
.character-dict-target {
  width: 100%;
  margin-bottom: 10px;
  padding: 8px 11px;
  background: rgba(59, 130, 160, 0.12);
  border: 1px solid rgba(59, 130, 160, 0.32);
  color: #2a6f8e;
  text-align: left;
  overflow-wrap: anywhere;
}
/* .translation-* 共享样式已抽到 src/styles/translation.css（TranslationModal + TranslateDetailModal 共用） */

/* ---------------- 画师 chip ★ 按钮 + 加入收藏弹窗 ---------------- */
.author-fav-btn {
  padding: 2px 6px;
  font-size: 11px;
  color: #b46e16;
  background: rgba(99, 102, 241, 0.45);
  border: 1px solid rgba(212, 143, 47, 0.35);
}
.author-fav-btn:hover {
  background: rgba(255, 188, 86, 0.3);
  color: #8a5a14;
  border-color: rgba(212, 143, 47, 0.55);
}

/* .fav-add-* 样式已抽到 src/styles/fav-add.css（两个 favorite modal 共享） */

/* ---------------- 刷新热度下拉菜单（合并原本 3 个按钮） ---------------- */
.refresh-dropdown {
  position: relative;
  display: inline-block;
}
.refresh-btn.menu-open {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
}

/* ---------------- 「显示 ▾」下拉菜单（排序/筛选/卡片/缩略图/每页） ---------------- */
.display-dropdown {
  position: relative;
  display: inline-block;
}
.display-dropdown .tool-btn.menu-open {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border-color: transparent;
}
.display-menu {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 200;
  min-width: 300px;
  padding: 6px;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  gap: 3px;
  animation: refresh-menu-in 0.12s ease-out;
}
.display-menu-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  min-height: 34px;
  padding: 5px 10px;
  border-radius: 7px;
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
}
.display-menu-row:hover {
  background: rgba(99, 102, 241, 0.09);
}
.display-menu-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
}
.display-menu-control {
  width: auto;
  min-width: 108px;
  height: 30px;
  padding: 4px 8px;
  font-size: 12.5px;
  border-radius: 8px;
  cursor: pointer;
}
.display-menu-control.page-size-input {
  min-width: 0;
  width: 72px;
  text-align: center;
}.display-menu-control.page-size-input::-webkit-outer-spin-button,
.display-menu-control.page-size-input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.display-menu-control.page-size-input[type="number"] {
  -moz-appearance: textfield;
}
/* 开关行：整行是按钮，右侧「开/关」pill 反映状态 */
.display-menu-toggle {
  width: 100%;
  font-family: inherit;
}
.display-menu-state {
  padding: 3px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
  background: var(--surface-muted);
}
.display-menu-toggle.active {
  background: rgba(255, 138, 61, 0.12);
  border-color: rgba(224, 90, 23, 0.28);
}
.display-menu-toggle.active .display-menu-state {
  color: #fff;
  background: linear-gradient(135deg, #ff8a3d, #e05a17);
}

/* 分段按钮组：用于「排序」「分级」多按钮切换，替代原来的下拉框。
   排序为单选（点哪个亮哪个）；分级为多选（可同时点亮 S/Q/E）。 */
.seg-group {
  display: inline-flex;
  gap: 4px;
  flex-wrap: nowrap;
  justify-content: flex-end;
}
.seg-btn {
  padding: 4px 8px;
  height: 28px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--muted);
  background: var(--surface-muted);
  border: 1px solid rgba(30, 41, 82, 0.1);
  border-radius: 8px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.14s ease, color 0.14s ease, border-color 0.14s ease;
}
.seg-btn:hover {
  border-color: rgba(var(--accent-rgb), 0.35);
  color: var(--ink);
}
.seg-btn.active {
  color: #fff;
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  border-color: transparent;
}

/* ---------------- 排行榜子操作栏 + 日期模式切换 ---------------- */
.rank-action-bar,
.date-mode-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  padding: 6px 8px;
  background: rgba(var(--accent-rgb), 0.05);
  border: 1px solid rgba(var(--accent-rgb), 0.15);
  border-radius: 10px;
}
.rank-action-bar .rab-label,
.date-mode-row .rab-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-deep);
  letter-spacing: 0.4px;
}
/* 日期模式行：第二个 seg-group（下载 / 仅收集ID / 补全/补齐 / 按ID下载）强制换行到第二行，
   避免窄面板下被挤回第一行跟「单日/日期范围」混在一起。seg-group 自身有 justify-content:flex-end，
   flex-basis:100% 会让按钮贴到第二行最右边 —— 这里 override 成 flex-start 让按钮贴左对齐。 */
.date-mode-row .seg-group + .seg-group {
  flex-basis: 100%;
  justify-content: flex-start;
}
.concurrency-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--muted);
  margin: 0;
  white-space: nowrap;
}
/* 头部控制行版：与 ghost 按钮同高（26px）。从 30×~88 → 26×~67，让出 ~21px 给新按钮。 */
.concurrency-field-inline {
  height: 26px;
  padding: 0 8px;
  gap: 4px;
  border-radius: 999px;
  border: 1px solid rgba(var(--accent-rgb), 0.2);
  background: rgba(var(--accent-rgb), 0.06);
  color: var(--accent-deep);
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  transition: background 0.18s, border-color 0.18s;
}
.concurrency-field-inline:hover {
  background: rgba(var(--accent-rgb), 0.12);
  border-color: rgba(var(--accent-rgb), 0.35);
}
.concurrency-field-inline > span {
  font-size: 11px;
  letter-spacing: 0.2px;
}
.concurrency-field-inline .concurrency-input {
  /* 头部版输入框：宽 30 × 高 20，与胶囊外框同色；去边框让数字像嵌在外壳里 */
  width: 30px;
  height: 20px;
  font-size: 11px;
  padding: 1px 2px;
  background: transparent;
  border-color: transparent;
  color: var(--accent-deep);
}
.concurrency-field-inline .concurrency-input:focus {
  background: rgba(255, 255, 255, 0.9);
  border-color: var(--accent-deep);
}
.concurrency-input {
  width: 42px;
  height: 26px;
  padding: 2px 4px;
  font-size: 12px;
  font-family: inherit;
  font-weight: 600;
  color: var(--accent-deep);
  text-align: center;
  border: 1px solid rgba(var(--accent-rgb), 0.3);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.85);
  -moz-appearance: textfield;
}
.concurrency-input::-webkit-outer-spin-button,
.concurrency-input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.concurrency-input:focus {
  outline: none;
  border-color: var(--accent-deep);
  box-shadow: 0 0 0 2px rgba(var(--accent-rgb), 0.18);
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
  background: rgba(255, 255, 255, 0.98);
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
  background: rgba(99, 102, 241, 0.1);
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
  background: rgba(255, 255, 255, 0.98);
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
  background: rgba(99, 102, 241, 0.1);
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

/* 刷新菜单里的开关型行：左侧 label，右侧「开/关」pill；
   用 .is-toggle 修饰而不是另起元素，避免和动作行 class 冲突。 */
.refresh-menu-item.is-toggle {
  cursor: pointer;
}
.refresh-menu-item.is-toggle .refresh-menu-state {
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
  background: var(--surface-muted);
  transition: background 0.14s ease, color 0.14s ease;
}
.refresh-menu-item.is-toggle.active .refresh-menu-state {
  color: #fff;
  background: linear-gradient(135deg, #ff8a3d, #e05a17);
}
.refresh-menu-divider {
  height: 1px;
  margin: 4px 8px;
  background: var(--line);
  opacity: 0.7;
}

/* 刷新范围 Modal 样式已随 RefreshRangeModal.vue 一起搬走 */

/* ---------------- 收藏卡片 / chip 异色高亮 ---------------- */
.search-input-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.search-input-with-clear { padding-right: 36px; }
.search-clear-btn {
  position: absolute;
  right: 6px;
  /* 用 top/bottom + margin:auto 做垂直居中，避免依赖 transform，
     从而不与全局 button:active 的 translateY(1px) 冲突导致抖动 */
  top: 0;
  bottom: 0;
  margin: auto 0;
  transform: none;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 1px solid var(--line, rgba(0, 0, 0, 0.18));
  border-radius: 50%;
  background: var(--surface, rgba(255, 255, 255, 0.5));
  color: var(--muted, #6b5a3c);
  /* 关键：× 是 U+00D7 (multiplication sign)，em-box 偏上。
     - 用 display:grid + place-items:center 让 glyph 在 22×22 box 内严格居中
     - line-height 锁 1 防止被父级 input 上下文拉偏
     - 22px box 配 14px × ：em-box 略偏上但视觉中心和 box 中心基本重合 */
  font-size: 14px;
  line-height: 1;
  font-family: inherit;
  cursor: pointer;
  box-shadow: none;
  display: grid;
  place-items: center;
  /* 只过渡背景色 / 边框 / 颜色，不过渡 transform，杜绝悬停/点击时的位移抖动 */
  transition: background-color 0.14s ease, border-color 0.14s ease, color 0.14s ease, opacity 0.14s ease;
}
.search-clear-btn:hover:not(:disabled) {
  background: rgba(157, 44, 44, 0.12);
  border-color: rgba(157, 44, 44, 0.5);
  color: #9d2c2c;
  filter: none;
  box-shadow: none;
  transform: none;
}
.search-clear-btn:active:not(:disabled) {
  transform: none;
  filter: none;
}

/* 搜索历史下拉样式已迁到 ./SearchHistoryDropdown.vue（避免 scoped CSS 跨组件失效） */

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
  transition: background 0.2s, color 0.2s, box-shadow 0.2s, filter 0.2s;
  backdrop-filter: blur(4px);
}
.img-fav-toggle:hover { background: rgba(0, 0, 0, 0.65); }
/* 已收藏态 hover：要保持「红色身份」，不能被 :hover 的深色背景覆盖。
   显式提高特异性，覆盖默认 :hover 的 background 切换（避免 0.2s 过渡里短暂闪一下深色），
   只用 filter 微暗 + 加强 box-shadow 提示「点一下就取消收藏」
   （去掉 scale 放大，跟其他普通按钮的悬浮行为保持一致）。
   transition 也要把 background 排除：父规则的 `transition: background 0.2s ...` 仍生效，
   即使 initial/hover 都是粉色，浏览器仍可能启动一次粉→深灰→粉的过渡造成「无色闪一下」。 */
.img-fav-toggle.active {
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  color: #fff;
  box-shadow: 0 2px 8px rgba(209, 40, 105, 0.5);
}
.img-fav-toggle.active:hover {
  background: linear-gradient(135deg, #ff5b8a, #d12869);
  filter: brightness(0.92) saturate(1.08);
  box-shadow: 0 4px 12px rgba(209, 40, 105, 0.6);
  transition: box-shadow 0.2s, filter 0.2s;
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
  aspect-ratio: 1 / 1;          /* 容器自身锁 1:1；图片未到也不塌 */
  line-height: 0;
  overflow: hidden;
}
.thumb-wrap > .thumb {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
/* 缩略图淡入：thumbUrl 是异步逐张回填的，若不做过渡就会一张张"啪"地蹦出来，
   多张同时到达时更像画面撕裂。先透明，图片真正解码完成(@load)后加 .is-loaded 淡入。 */
.thumb-wrap .thumb {
  opacity: 0;
  transition: opacity 0.32s ease;
}
.thumb-wrap .thumb.is-loaded {
  opacity: 1;
}
/* 骨架屏：图片解出来之前铺一层轻微流光的占位；
   容器已有 aspect-ratio 1:1，骨架屏铺满即可。 */
.thumb-skeleton {
  position: absolute;
  inset: 0;
  border-radius: 0;
  background: linear-gradient(100deg,
    #eef1f8 30%,
    #f6f8fd 50%,
    #eef1f8 70%);
  background-size: 220% 100%;
  animation: thumb-shimmer 1.25s ease-in-out infinite;
  pointer-events: none;
  z-index: 1;
}
/* MP4 / GIF thumb 失败（ffmpeg 不可用、404、5xx）时保持骨架屏常驻，避免露出 broken icon */
.thumb-wrap.is-broken .thumb-skeleton {
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
.thumb-wrap.is-broken .thumb-skeleton::after {
  content: 'NO PREVIEW';
}
@keyframes thumb-shimmer {
  0% { background-position: 140% 0; }
  100% { background-position: -40% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .thumb-skeleton { animation: none; }
  .thumb-wrap .thumb { transition: none; }
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
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.12), rgba(168, 85, 247, 0.1));
  color: var(--ink);
  border: 1px solid rgba(79, 118, 224, 0.2);
  box-shadow: 0 2px 10px rgba(30, 41, 82, 0.08);
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
  border: 1px solid rgba(30, 41, 82, 0.18);
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
  /* Teleport 到 body + position:fixed：彻底脱离分页栏(.cr-pg-bar 的 overflow 裁剪)
     与图片卡片(content-visibility:auto 形成的层叠上下文)，确保浮层永远在最上层、
     不被图片盖住。right/bottom 由 JS 按 .pg-picker-host 的视口位置实时计算
     （见 positionPagePicker）。 */
  position: fixed;
  z-index: 9000;
  background: #fff;
  border: 1px solid rgba(30, 41, 82, 0.18);
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
/* 两级页码：块导航控件（分页栏块簇 + 页码面板头部块切换） */
.pg-picker-block-nav {
  display: flex;
  align-items: center;
  gap: 6px;
}
.pg-block-select,
.pg-picker-block-select {
  padding: 4px 6px;
  font-size: 12px;
  font-weight: 600;
  border-radius: 8px;
  background: var(--surface-muted);
  color: var(--ink);
  border: 1px solid rgba(30, 41, 82, 0.12);
  cursor: pointer;
}
.pg-picker-block-select {
  flex: 1;
  min-width: 90px;
}
/* CrawlerPage 分页栏：强制单行，避免块控件让分页栏换行而压缩上方图片网格高度 */
.cr-pg-bar {
  flex-wrap: nowrap;
  overflow-x: auto;
  /* 只允许横向滚动兜底；不显式关掉纵向，overflow-y 会被浏览器算成 auto，
     横向滚动条一出现就吃掉行高 → 冒出一条无意义的纵向滚动条（最右侧上下箭头） */
  overflow-y: hidden;
}
.pg-block-mini {
  padding: 4px 7px;
  font-size: 13px;
}
.pg-block-btn {
  padding: 0 6px;
}
.pg-picker-meta {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
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
  background: var(--surface-muted);
  color: var(--ink);
  border: 1px solid rgba(30, 41, 82, 0.08);
  cursor: pointer;
  transition: background 0.15s, transform 0.1s;
}
.pg-picker-cell:hover {
  background: var(--soft-violet);
}
.pg-picker-cell.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  box-shadow: 0 2px 6px rgba(79, 118, 224, 0.25);
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
/* .selection-list-* 样式已随 SelectionListModal.vue 一起搬走 */
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
  background: rgba(99, 102, 241, 0.25);
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

/* 大图浏览器底部的「收藏」切换按钮
   base：暗色胶囊 + 金色字；active：粉色渐变 + 白字 + 红色光晕
   hover 拆分：未收藏态用白底叠加（让暗胶囊更亮一点），已收藏态保持红色
   身份但加 brightness(0.9) 微暗一档，告诉用户「点一下就取消收藏」，
   不要把 active 按钮的渐变整个洗成白色——会瞬间失忆「这图还在不在收藏里」
   （去掉 scale 放大，跟其他普通按钮的悬浮行为保持一致） */
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
/* 已收藏态的 hover：要保持「红色身份」，只用 filter 微暗，不要用 background 白底
   把渐变洗掉。
   这里要显式 lock 住 background = 粉色渐变，并 override transition 去掉 background 项
   —— 父规则的 transition: background 0.2s 仍生效，若不显式声明，浏览器仍可能启动
   一次「粉→白→粉」的过渡出现「已收藏按钮变无色」的闪烁。
   顺便把 box-shadow 加强一档，让悬浮时的粉色外光晕更明显，提示「点一下就取消收藏」
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
/* 代理「走代理 / 直连」开关（改动5）：复用 safe-mode-btn 的胶囊外形 */
.proxy-mode-btn {
  padding: 4px 12px;
  border-radius: 999px;
  font-weight: 600;
  font-size: 12px;
  border: 1px solid transparent;
  white-space: nowrap;
  transition: background 0.18s, color 0.18s, border-color 0.18s;
}
.proxy-mode-btn.is-proxy {
  background: rgba(59, 130, 160, 0.15);
  color: #2a6f8e;
  border-color: rgba(59, 130, 160, 0.4);
}
.proxy-mode-btn.is-proxy:hover {
  background: rgba(59, 130, 160, 0.25);
}
.proxy-mode-btn.is-direct {
  background: rgba(217, 119, 6, 0.18);
  color: #b45309;
  border-color: rgba(217, 119, 6, 0.45);
}
.proxy-mode-btn.is-direct:hover {
  background: rgba(217, 119, 6, 0.3);
}

/* ---------------- 顺序任务队列 ---------------- */
.task-queue-panel {
  margin-top: 12px;
  /* 左右 padding 设为 0：让任务条撑到面板边缘，最大化利用左栏宽度；
     任务条自己仍保留 9px 内部 padding 保证状态图标/标签不贴边。 */
  padding: 12px 0;
  border: 1px solid var(--line);
  border-radius: 14px;
  background:
    linear-gradient(180deg, rgba(var(--accent-rgb), 0.05), rgba(var(--accent-rgb), 0));
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.5);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tq-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  /* 面板 padding 设为 0 后，标题直接顶到左边；补一个 9px 左 padding 跟 tq-item 内 padding 对齐。 */
  padding-left: 9px;
  padding-right: 9px;
}
.tq-count-badge {
  font-weight: 600;
  font-size: 11px;
  color: var(--accent-deep);
  background: rgba(var(--accent-rgb), 0.12);
  border-radius: 999px;
  padding: 1px 9px;
}
.tq-title {
  font-weight: 700;
  font-size: 13px;
  color: var(--accent-deep);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  letter-spacing: 0.2px;
}
.tq-running-badge {
  font-weight: 600;
  font-size: 11px;
  color: #fff;
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  border-radius: 999px;
  padding: 2px 9px;
  box-shadow: 0 1px 4px rgba(var(--accent-rgb), 0.35);
}
.tq-running-badge::before {
  content: "";
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 5px;
  border-radius: 50%;
  background: #fff;
  vertical-align: middle;
}
@keyframes tq-pulse-dot {
  0%, 100% { opacity: 0.45; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.05); }
}

/* 入队按钮：功能入口，做成柔和主色描边 + 悬停填充 */
.tq-add-btn {
  font-size: 12.5px;
  font-weight: 600;
  padding: 5px 13px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.tq-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  /* 最多显示约 3 行任务，再多用滚轮纵向收纳，避免把下方日志挤出去 */
  max-height: 108px;
  /* 允许用户像「粘贴 ID 列表」textarea 那样从右下角拖动纵向 resize，
     队列项多的时候能看到更多；flex 列项 + overflow-y: auto 是 Chromium 支持的前提。 */
  resize: vertical;
  overflow-y: auto;
  overflow-x: hidden;
  /* 滚动条样式走全局（style.css）—— 跟画廊上下滚动条同款 */
}
.tq-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 9px;
  border-radius: 9px;
  background: var(--surface);
  border: 1px solid var(--line);
  font-size: 12.5px;
  flex-shrink: 0;            /* 在滚动容器里保持每行高度，不被压扁 */
  flex-wrap: wrap;           /* 让 .tq-item-error 用 flex-basis:100% 强制换行 */
  cursor: grab;              /* 提示整行可拖动 */
  user-select: none;         /* 拖动时不要选中文字 */
  -webkit-user-drag: element;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease, background 0.18s ease, opacity 0.18s ease;
}
/* 左侧状态色条：默认透明，各状态点亮 */
.tq-item::before {
  content: "";
  position: absolute;
  left: 0;
  top: 6px;
  bottom: 6px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: transparent;
  transition: background 0.18s ease;
}
.tq-item:hover { border-color: rgba(var(--accent-rgb), 0.4); box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06); }
.tq-item.current {
  border-color: rgba(var(--accent-rgb), 0.55);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.16);
}
.tq-item.running { border-color: rgba(var(--accent-rgb), 0.5); background: rgba(var(--accent-rgb), 0.06); }
.tq-item.running::before { background: var(--accent); }
.tq-item.done { border-color: rgba(16, 185, 129, 0.45); background: rgba(16, 185, 129, 0.05); }
.tq-item.done::before { background: #10b981; }
.tq-item.warning { border-color: rgba(216, 140, 27, 0.5); background: rgba(216, 140, 27, 0.07); }
.tq-item.warning::before { background: #e0982a; }
.tq-item.skipped { border-color: rgba(100, 116, 139, 0.4); background: rgba(100, 116, 139, 0.06); }
.tq-item.skipped::before { background: #94a3b8; }
.tq-item.error { border-color: rgba(239, 68, 68, 0.45); background: rgba(239, 68, 68, 0.05); }
.tq-item.error::before { background: #ef4444; }
.tq-item-status { width: 16px; flex: 0 0 auto; text-align: center; font-size: 13px; }
.tq-item.running .tq-item-status { display: inline-block; }
.tq-item.done .tq-item-status { color: #059669; }
.tq-item.warning .tq-item-status { color: #b46b08; }
.tq-item.skipped .tq-item-status { color: #64748b; }
.tq-item.error .tq-item-status { color: #dc2626; }
/* 错误 / 等待重试说明：内联在 label 下面，Electron 里 :title tooltip 几乎不可见。 */
.tq-item-error {
  flex-basis: 100%;
  margin: 4px 0 2px 22px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11.5px;
  line-height: 1.45;
  color: var(--muted, #6b7280);
  background: rgba(0, 0, 0, 0.04);
  word-break: break-all;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.tq-item.error .tq-item-error { color: #b91c1c; background: rgba(239, 68, 68, 0.08); }
.tq-item.warning .tq-item-error { color: #92400e; background: rgba(217, 119, 6, 0.08); }
/* 复制错误按钮 */
.tq-copy {
  background: rgba(0, 0, 0, 0.05);
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1;
  padding: 0 6px;
  height: 20px;
  cursor: pointer;
  color: inherit;
}
.tq-copy:hover { background: rgba(0, 0, 0, 0.1); }
/* 拖拽中：原位半透明，光标切换为 grabbing */
.tq-item.tq-dragging {
  cursor: grabbing;
  opacity: 0.45;
  border-style: dashed;
  border-color: rgba(var(--accent-rgb), 0.5);
}
/* 落点指示：在目标项的上沿 / 下沿画一条主题色高亮线 */
.tq-item.tq-drop-above::after,
.tq-item.tq-drop-below::after {
  content: "";
  position: absolute;
  left: 6px;
  right: 6px;
  height: 2px;
  border-radius: 1px;
  background: var(--accent);
  box-shadow: 0 0 4px rgba(var(--accent-rgb), 0.45);
  pointer-events: none;
}
.tq-item.tq-drop-above::after { top: -1px; }
.tq-item.tq-drop-below::after { bottom: -1px; }
.tq-item-label {
  flex: 1;
  min-width: 0;             /* 允许收缩到比内容更窄，才能触发横向滚动 */
  color: var(--ink);
  font-weight: 500;
  white-space: nowrap;      /* 每个任务只占一行，不再换行撑高 */
  overflow-x: auto;         /* 文字过长时左右滚动查看 */
  overflow-y: hidden;
}
/* tq-item-label 滚动条走全局（style.css），跟画廊上下滚动条同款 */
.tq-item-ops { display: inline-flex; gap: 4px; flex: 0 0 auto; }
.tq-mini {
  font-size: 12px;
  line-height: 1;
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-radius: 6px;
  border: 1px solid transparent;
  background: rgba(var(--accent-rgb), 0.06);
  color: var(--muted);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.tq-mini:hover:not(:disabled) { background: rgba(var(--accent-rgb), 0.14); color: var(--accent-deep); border-color: rgba(var(--accent-rgb), 0.3); }
.tq-mini:disabled { opacity: 0.35; cursor: not-allowed; }
.tq-del:hover:not(:disabled) { color: #dc2626; border-color: rgba(239, 68, 68, 0.4); background: rgba(239, 68, 68, 0.1); }
.tq-empty {
  font-size: 12px;
  color: var(--muted);
  padding: 12px 10px;
  line-height: 1.6;
  text-align: center;
  border: 1px dashed var(--line);
  border-radius: 9px;
  background: rgba(var(--accent-rgb), 0.02);
}
.tq-actions { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; }
.tq-action-group { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; }
.tq-action-label {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.04em;
  padding: 0 4px 0 2px;
  border-left: 2px solid rgba(var(--accent-rgb), 0.35);
  margin-right: 2px;
  user-select: none;
}
.tq-actions button {
  font-size: 12px;
  padding: 5px 13px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.tq-actions .tq-run {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  border: none;
  color: #fff;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.28);
}
.tq-actions .tq-run:hover:not(:disabled) { filter: brightness(1.06); box-shadow: 0 3px 12px rgba(var(--accent-rgb), 0.38); }
.tq-actions .tq-run:disabled { opacity: 0.5; box-shadow: none; }

/* ---- 队列项入场 / 离场动画（TransitionGroup name="tq"）---- */
.tq-enter-from { opacity: 0; transform: translateY(-8px) scale(0.97); }
.tq-enter-to { opacity: 1; transform: translateY(0) scale(1); }
.tq-enter-active { transition: opacity 0.28s ease, transform 0.28s cubic-bezier(0.22, 1, 0.36, 1); }
.tq-leave-active { transition: opacity 0.2s ease, transform 0.2s ease; position: absolute; width: calc(100% - 3px); }
.tq-leave-to { opacity: 0; transform: translateX(12px); }
.tq-move { transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1); }

/* 刚加入的项：一次高亮脉冲，让用户明确看到"加进来了" */
.tq-item.just-added { animation: tq-just-added 1.05s cubic-bezier(0.22, 1, 0.36, 1); z-index: 1; }
@keyframes tq-just-added {
  0% { box-shadow: 0 0 0 0 rgba(var(--accent-rgb), 0.5); border-color: var(--accent); background: rgba(var(--accent-rgb), 0.14); }
  60% { box-shadow: 0 0 0 6px rgba(var(--accent-rgb), 0); border-color: rgba(var(--accent-rgb), 0.5); }
  100% { box-shadow: 0 0 0 0 rgba(var(--accent-rgb), 0); }
}
/* ---------------- 起始页 / 结束页：缩小输入框 + 右侧最近使用记录 ---------------- */
/* 注意：类名不能用 .page-grid —— 全局 style.css 里 .page-grid 是应用整体布局
   （340px + 1fr + height:100%），命中后会把这一行撑到面板满高，下方出现大块空白 */
.pages-grid {
  /* 两栏等分，input + 芯片紧挨着，剩下的空间让给芯片 */
  grid-template-columns: 1fr 1fr;
}
.page-field {
  min-width: 0;
}
.page-field-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.page-input {
  /* 进一步缩小：3 位数字也能完整显示。多余空间让给芯片，不再把整行撑高 */
  width: 44px;
  flex: 0 0 auto;
  padding: 4px 4px;
  font-size: 12px;
  text-align: center;
}
/* number 输入自带的上下箭头按钮在窄输入框里很占位置，隐藏掉以免数字被挤出 */
.page-input::-webkit-outer-spin-button,
.page-input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.page-input[type="number"] {
  -moz-appearance: textfield;
}
.recent-pages {
  display: flex;
  /* 不允许换行：换行会把整行撑高，让下面的"过滤标签"和上面差距过大 */
  flex-wrap: nowrap;
  gap: 3px;
  min-width: 0;
  flex: 1 1 auto;
}
.recent-page-chip {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  padding: 1px 3px 1px 6px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--accent-deep);
  background: var(--soft);
  border: 1px solid rgba(30, 41, 82, 0.12);
  border-radius: 999px;
  cursor: pointer;
  user-select: none;
  line-height: 1.3;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.recent-page-chip:hover {
  background: rgba(99, 102, 241, 0.13);
  border-color: rgba(79, 118, 224, 0.35);
}
.recent-page-chip.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border-color: transparent;
}
.recent-page-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
  padding: 0;
  margin-left: 1px;
  border: none;
  border-radius: 50%;
  background: rgba(30, 41, 82, 0.18);
  color: #fff;
  font-size: 10px;
  line-height: 1;
  cursor: pointer;
}
.recent-page-chip.active .recent-page-x {
  background: rgba(255, 255, 255, 0.35);
}
.recent-page-x:hover {
  background: rgba(157, 44, 44, 0.7);
}

/* 缩窄 page-field label：base style 给的是 margin-bottom: 10px + gap: 5px，
   配合上面的紧凑布局可以再小一点，让"起始页/结束页"和下面"过滤标签"挨得更近 */
.pages-grid .page-field {
  margin-bottom: 4px;
  gap: 3px;
}

/* ---------------- 大图查看器右上角始终显示的信息小栏 ----------------
   独立于顶部 .viewer-toolbar，不会随 viewerToolbarVisible 收起；
   包含 3 行：第 x/y 张（含 ★/♥）/ 适应窗口 / 固定信息栏 */
.viewer-corner-info {
  position: absolute;
  top: 20px;
  right: 20px;
  z-index: 45;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  pointer-events: none;  /* 让外层不挡鼠标，子元素再各自接 pointer-events: auto */
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
  /* 计数行更紧凑些，让数字 + ★ + ♥ 都能一目了然 */
  padding: 5px 12px;
  /* 默认半透明降低存在感，鼠标悬浮上去后恢复完全不透明便于阅读 */
  opacity: 0.35;
  transition: opacity 0.18s ease;
}
.viewer-corner-counter:hover {
  opacity: 1;
}
.viewer-corner-btn.viewer-corner-close {
  align-self: flex-end;
  background: rgba(125, 32, 32, 0.66);
  border-color: rgba(255, 180, 180, 0.34);
  opacity: 0.58;
  transition: opacity 0.18s ease, background 0.18s ease;
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
  /* 复用全局 .viewer-jump-input 的暗色样式，但宽度调小一点适配右上小栏 */
  width: 48px;
  padding: 2px 6px;
  font-size: 12px;
  text-align: center;
}
.viewer-corner-btn {
  cursor: pointer;
  transition: background 0.18s, border-color 0.18s, color 0.18s, opacity 0.18s ease;
  font-family: inherit;
  /* 与计数行保持一致：默认半透明，悬浮恢复 */
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
  /* 固定状态是用户明确选择的「常驻可见」语义，保持不透明 */
  opacity: 1;
}

/* Lightweight anime theme */
.control-panel,
.gallery-panel { border-color: var(--line); background: rgba(255, 255, 255, 0.92); }
.mode-chip { border-color: transparent; background: var(--surface-muted); color: var(--ink); box-shadow: none; }
.mode-chip:hover:not(.active) { background: var(--soft-violet); }
.mode-chip.active { background: var(--accent-gradient); box-shadow: 0 5px 12px rgba(var(--accent-rgb), 0.18); }
.crawler-head-actions button { box-shadow: none; }
.task-queue-panel,
.tq-item,
.modern-log-wrapper { border-color: var(--line); }
.tq-item { background: var(--surface); }
.tq-item.current { border-color: rgba(var(--accent-rgb), 0.55); background: rgba(var(--accent-rgb), 0.06); }
.recent-page-chip { border-color: rgba(var(--violet-rgb), 0.12); background: var(--soft-violet); color: var(--accent-deep); }

/* log.json 下载策略行：field-full + 内嵌 seg-group，跟上方子操作栏完全同款。
   唯一微调：默认 seg-btn 是按右对齐（justify-content: flex-end），这里 field-full
   是全宽文本，标签在左、按钮在左下更顺。「强制重下」激活态用同款 accent 渐变，
   不另起 warning 色，避免引入新色相。 */
.dl-strategy-row .seg-group {
  justify-content: flex-start;
}
.gallery-tools select,
.gallery-tools input { background: rgba(255, 255, 255, 0.92); }
.viewer-corner-btn.is-active { box-shadow: 0 4px 16px rgba(var(--accent-rgb), 0.35); }

/* .browse-* 样式已随 BrowseOverlay.vue 一起搬走 */
</style>
