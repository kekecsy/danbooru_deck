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
  idsText: '',
  tagQuery: typeof habits.tagQuery === 'string' ? habits.tagQuery : ''
});

watch(() => form.value.mode, (newMode) => {
  if (['rank', 'collect_ids'].includes(newMode)) {
    form.value.startPage = habits[`${newMode}_start`] || 1;
    form.value.endPage = habits[`${newMode}_end`] || 16;
  } else if (['popular', 'popular_range'].includes(newMode)) {
    form.value.startPage = habits[`${newMode}_start`] || 1;
    form.value.endPage = habits[`${newMode}_end`] || 35;
  } else if (newMode === 'tags') {
    form.value.startPage = habits.tags_start || 1;
    form.value.endPage = habits.tags_end || 5;
  }
  applyDefaultDatesForMode(newMode);
  recentStartPages.value = loadRecentPages(newMode, 'start');
  recentEndPages.value = loadRecentPages(newMode, 'end');
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
// 队列 / 组合里把日期存成相对令牌，跑时再解析成「当时的」昨天/今天 ——
// 这样同一个常用组合每天跑都对应新的昨天，而不是被钉死在某个旧日期。
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
    if (!form.value.targetDate) form.value.targetDate = yesterdayString();
  } else if (mode === 'popular_range') {
    if (!form.value.startDate) form.value.startDate = yesterdayString();
    if (!form.value.endDate) form.value.endDate = yesterdayString();
  }
}
// 初始模式若是 popular / popular_range，进来就把日期填成昨天
applyDefaultDatesForMode(form.value.mode);

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
  // 标签文件夹：[{folder: "tag_xxx", display: "xxx"}] —— 与日期文件夹并行，由后端扫描 hot_pic 提供
  availableTags: [],
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
  // 缩略图长边像素：默认 360（远小于原图，省解码/内存）；0 = 原图。点开看大图仍走原图。
  thumbSize: habits.thumbSize != null ? habits.thumbSize : 360,
  // 点开/切换大图是否联网刷新该图 score/收藏数：默认关，离线不报错；开关在工具栏「翻译」右侧
  refreshOnView: habits.refreshOnView ?? false,
  page: 1
});

watch(() => [gallery.value.sortBy, gallery.value.hotThreshold, gallery.value.cardSize, gallery.value.thumbSize, gallery.value.refreshOnView], () => {
  habits.sortBy = gallery.value.sortBy;
  habits.hotThreshold = gallery.value.hotThreshold;
  habits.cardSize = gallery.value.cardSize;
  habits.thumbSize = gallery.value.thumbSize;
  habits.refreshOnView = gallery.value.refreshOnView;
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

// ---------------- Caption 悬浮窗口 ----------------
const caption = ref({
  open: false,
  saving: false,
  withArtist: habits.captionWithArtist === true,
  mode: 'mark',          // 'mark' 标记模式（红，拖选标红） | 'edit' 输入模式（直接编辑文本框）
  text: '',              // = caption_zh 中文散文（红框标注作用于此）
  captionEn: '',         // 训练用英文分区模板 caption（只读展示）
  verifiedTags: [],      // booru 风格已校验标签数组（可增删）
  tagInput: '',          // verified_tags 添加框的输入
  errors: [],            // [{start, end, text}]
  meta: null,            // {artist, characters, copyright}
  message: '',
  imagePath: '',
  loaded: false,
  dirty: false,
  pos: { x: 0, y: 0, initialized: false },
  // 手动模式：API 不可用 / 不想用 API 时，把 3 阶段 pipeline 的每一轮提示词
  // 复制到任意 chat LLM（Claude/ChatGPT/Gemini Web），在同一对话里连续粘贴 3
  // 段，把每轮返回粘回到对应粘贴框；最后一轮的结构化 JSON 解析出中英 caption。
  manual: {
    open: true,            // 主入口，默认展开
    promptBusy: false,
    stage: 1,              // 1 | 2 | 3 | 'done'
    metaUsed: null,        // 拉过 prompt 后置为 true/false
    s1: { combined: '', pasted: '', parsed: null, parseError: '' },
    s2: { user: '',     pasted: '', parsed: null, parseError: '' },
    s3: { user: '',     pasted: '', parsed: null, parseError: '' }
  }
});
const captionTextRef = ref(null);
const captionDrag = ref({ active: false, dx: 0, dy: 0 });

const PUNCT_RE = /[，。、；：！？,.;:!?…—]/u;

function openCaptionWindow() {
  const item = viewerItem.value;
  if (!item?.localPath) return;
  caption.value.imagePath = item.localPath;
  caption.value.message = '';
  caption.value.errors = [];
  caption.value.text = '';
  caption.value.captionEn = '';
  caption.value.verifiedTags = [];
  caption.value.tagInput = '';
  caption.value.meta = null;
  caption.value.loaded = false;
  caption.value.dirty = false;
  resetManualPipeline();
  caption.value.manual.open = true;
  caption.value.open = true;
  if (!caption.value.pos.initialized) {
    const panelWidth = 480;
    caption.value.pos.x = Math.max(20, window.innerWidth - panelWidth - 32);
    caption.value.pos.y = 80;
    caption.value.pos.initialized = true;
  }
  loadExistingCaption();
}

function closeCaptionWindow() {
  caption.value.open = false;
}

// 浮窗拖动：mousedown 在 header 上启动
function onCaptionDragStart(e) {
  // 忽略关闭按钮等子元素上的点击
  if (e.target.closest('button')) return;
  captionDrag.value.active = true;
  captionDrag.value.dx = e.clientX - caption.value.pos.x;
  captionDrag.value.dy = e.clientY - caption.value.pos.y;
  window.addEventListener('mousemove', onCaptionDragMove);
  window.addEventListener('mouseup', onCaptionDragEnd);
  e.preventDefault();
}
function onCaptionDragMove(e) {
  if (!captionDrag.value.active) return;
  const maxX = window.innerWidth - 120;
  const maxY = window.innerHeight - 60;
  caption.value.pos.x = Math.max(-200, Math.min(maxX, e.clientX - captionDrag.value.dx));
  caption.value.pos.y = Math.max(0, Math.min(maxY, e.clientY - captionDrag.value.dy));
}
function onCaptionDragEnd() {
  captionDrag.value.active = false;
  window.removeEventListener('mousemove', onCaptionDragMove);
  window.removeEventListener('mouseup', onCaptionDragEnd);
}

async function loadExistingCaption() {
  try {
    const entry = await window.desktopAPI.caption.read(caption.value.imagePath);
    if (entry) {
      caption.value.text = entry.caption_zh || entry.caption || '';
      caption.value.captionEn = entry.caption_en || '';
      caption.value.verifiedTags = Array.isArray(entry.verified_tags) ? entry.verified_tags : [];
      caption.value.errors = Array.isArray(entry.errors) ? entry.errors : [];
      caption.value.meta = {
        artist: entry.artist,
        characters: entry.characters,
        copyright: entry.copyright
      };
      caption.value.withArtist = !!entry.with_artist;
      caption.value.loaded = true;
      // 恢复 Pipeline 每一步内容（保存时写入 entry.pipeline）。按字段合并，
      // 保留默认形状；prompt（combined/user）不存盘，需要时再点复制重新拉。
      const p = entry.pipeline;
      if (p && typeof p === 'object') {
        const m = caption.value.manual;
        if (p.stage !== undefined && p.stage !== null) m.stage = p.stage;
        if (p.s1) m.s1 = { ...m.s1, ...p.s1 };
        if (p.s2) m.s2 = { ...m.s2, ...p.s2 };
        if (p.s3) m.s3 = { ...m.s3, ...p.s3 };
      }
    }
  } catch (e) {
    // 忽略
  }
}

// 标点吸附：仅吸附尾部紧贴的标点，首部不再吸附
function snapToPunctuation(text, start, end) {
  while (end < text.length && PUNCT_RE.test(text[end])) end += 1;
  return [start, end];
}

function markCurrentSelection() {
  const el = captionTextRef.value;
  if (!el) return;
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
  const range = sel.getRangeAt(0);
  if (!el.contains(range.startContainer) || !el.contains(range.endContainer)) return;
  const preRange = document.createRange();
  preRange.selectNodeContents(el);
  preRange.setEnd(range.startContainer, range.startOffset);
  const start0 = preRange.toString().length;
  const end0 = start0 + range.toString().length;
  const text = caption.value.text;
  let [start, end] = snapToPunctuation(text, start0, end0);
  if (start >= end) return;

  // 标记模式：拖选标红，合并相邻 error。（输入模式走可编辑文本框，不触发本函数）
  const next = [];
  let merged = { start, end };
  for (const e of caption.value.errors) {
    if (e.end < merged.start || e.start > merged.end) {
      next.push(e);
    } else {
      merged.start = Math.min(merged.start, e.start);
      merged.end = Math.max(merged.end, e.end);
    }
  }
  next.push({ start: merged.start, end: merged.end, text: text.slice(merged.start, merged.end) });
  next.sort((a, b) => a.start - b.start);
  caption.value.errors = next;
  caption.value.dirty = true;
  sel.removeAllRanges();
}

function removeErrorSpan(idx) {
  caption.value.errors = caption.value.errors.filter((_, i) => i !== idx);
  caption.value.dirty = true;
}

function clearAllErrors() {
  caption.value.errors = [];
  caption.value.dirty = true;
}

// ---- verified_tags 人工增删（tag 现在是发色/瞳色/锚点的唯一载体，人工可直接改） ----
function normalizeTag(raw) {
  return String(raw == null ? '' : raw).trim().toLowerCase().replace(/\s+/g, '_');
}
function addVerifiedTag(tag) {
  const fromInput = tag == null;
  const t = normalizeTag(fromInput ? caption.value.tagInput : tag);
  if (!t) return;
  if (!Array.isArray(caption.value.verifiedTags)) caption.value.verifiedTags = [];
  if (!caption.value.verifiedTags.includes(t)) {
    caption.value.verifiedTags.push(t);
    caption.value.dirty = true;
  }
  if (fromInput) caption.value.tagInput = '';
}
function removeVerifiedTag(idx) {
  caption.value.verifiedTags.splice(idx, 1);
  caption.value.dirty = true;
}
// Stage 2 校验里模型存疑、danbooru 未标的高显著锚点（如误判的 cat_ears）；
// 已在 verified_tags 里的过滤掉，剩下的作为「待人工确认」建议展示。
const reviewAnchors = computed(() => {
  const ra = caption.value.manual?.s2?.parsed?.review_anchors;
  if (!Array.isArray(ra)) return [];
  const have = new Set((caption.value.verifiedTags || []).map(t => String(t)));
  return ra.filter(a => a && a.tag && !have.has(String(a.tag)));
});

// 渲染：errors（红）按 start 排序分段输出，错误段显示原文。
// 仅标记模式用；输入模式直接走可编辑 textarea。
const captionRenderSegments = computed(() => {
  const text = caption.value.text || '';
  const spans = caption.value.errors
    .map((e, i) => ({ ...e, idx: i }))
    .sort((a, b) => a.start - b.start);
  const segs = [];
  let cursor = 0;
  for (const s of spans) {
    if (s.start > cursor) segs.push({ type: 'text', text: text.slice(cursor, s.start) });
    segs.push({ type: 'error', text: text.slice(s.start, s.end), idx: s.idx });
    cursor = Math.max(cursor, s.end);
  }
  if (cursor < text.length) segs.push({ type: 'text', text: text.slice(cursor) });
  return segs;
});

// 是否有可保存内容：最终文本，或任一 pipeline 步骤已填/已往后走。
// 让「保存」按钮在只完成中间步骤时也可用，从而保存每一步的进度。
const captionHasContent = computed(() => {
  if ((caption.value.text || '').trim()) return true;
  const m = caption.value.manual;
  if (m.stage !== 1) return true; // 已进入 stage 2/3/done
  return !!(m.s1.pasted.trim() || m.s2.pasted.trim() || m.s3.pasted.trim());
});

async function saveCaption() {
  if (!caption.value.imagePath || caption.value.saving) return;
  if (!window.desktopAPI?.caption?.save) {
    caption.value.message = '保存失败：window.desktopAPI.caption.save 不存在。请完全关闭并重启 Electron（preload 改动需要重启进程，仅热重载渲染端不会更新）。';
    return;
  }
  // JSON 深拷贝去掉 Vue reactive proxy，避免 IPC structuredClone 报「could not be cloned」
  const m = caption.value.manual;
  const entry = JSON.parse(JSON.stringify({
    caption: caption.value.text,        // 顶层 caption = 中文散文（保画廊绿标语义/向后兼容）
    caption_zh: caption.value.text,
    caption_en: caption.value.captionEn || '',
    verified_tags: Array.isArray(caption.value.verifiedTags) ? caption.value.verifiedTags : [],
    with_artist: caption.value.withArtist,
    artist: caption.value.meta?.artist || null,
    characters: caption.value.meta?.characters || null,
    copyright: caption.value.meta?.copyright || null,
    errors: caption.value.errors,
    // Pipeline 每一步内容：重开同一张图时恢复（loadExistingCaption），避免重做 stage
    pipeline: { stage: m.stage, s1: m.s1, s2: m.s2, s3: m.s3 }
  }));
  const imagePath = String(caption.value.imagePath);
  caption.value.saving = true;
  caption.value.message = '保存中...';
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error('保存超时（IPC 无响应，可能需要重启 Electron）')), 8000));
  try {
    const result = await Promise.race([
      window.desktopAPI.caption.save(imagePath, entry),
      timeout
    ]);
    if (result?.ok) {
      caption.value.message = `已保存到 ${result.path}`;
      caption.value.dirty = false;
      // 立即更新画廊标记（仅当有最终文本时；只存了中间步骤不算「已生成」）
      const fname = caption.value.text.trim() ? caption.value.imagePath.split(/[\\/]/).pop() : '';
      if (fname) {
        const next = new Set(captionedSet.value);
        next.add(fname);
        captionedSet.value = next;
      }
    } else {
      caption.value.message = `保存失败：${result?.error || '未知错误'}`;
    }
  } catch (e) {
    caption.value.message = `保存失败：${e.message || e}`;
  } finally {
    caption.value.saving = false;
  }
}

// ---------------- Caption 手动模式（3 阶段 Pipeline） ----------------
// 用户在外部 chat LLM 同一对话里连续粘贴 stage 1/2/3 三段提示词，
// 把每轮返回粘回到对应文本框；stage 3 现在返回结构化 JSON
// {caption_en, caption_zh, verified_tags}，解析后中文段落即为 caption.text。

function resetManualPipeline() {
  const m = caption.value.manual;
  m.stage = 1;
  m.promptBusy = false;
  m.metaUsed = null;
  m.s1 = { combined: '', pasted: '', parsed: null, parseError: '' };
  m.s2 = { user: '',     pasted: '', parsed: null, parseError: '' };
  m.s3 = { user: '',     pasted: '', parsed: null, parseError: '' };
}

function stripCodeFences(text) {
  if (!text) return '';
  let s = String(text).trim();
  // ```json ... ``` 或 ``` ... ```
  const fence = /^```(?:json|JSON)?\s*([\s\S]*?)\s*```$/m;
  const m = s.match(fence);
  if (m) return m[1].trim();
  // 没有围栏：尝试找第一个 { 到最后一个 }（兼容模型在前后加解释文字的情况）
  const first = s.indexOf('{');
  const last = s.lastIndexOf('}');
  if (first >= 0 && last > first) {
    return s.slice(first, last + 1);
  }
  return s;
}

async function fetchStagePrompt(stage) {
  const m = caption.value.manual;
  const payload = {
    image_path: caption.value.imagePath,
    with_artist: !!caption.value.withArtist,
    stage,
  };
  if (stage === 3 && m.s2.parsed) {
    payload.verify_json = JSON.stringify(m.s2.parsed);
  }
  const res = await fetch('http://127.0.0.1:8000/api/caption_prompt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.msg || `获取 stage ${stage} 提示词失败`);
  m.metaUsed = !!data.meta_used;
  if (stage === 1) m.s1.combined = data.combined || '';
  else if (stage === 2) m.s2.user = data.user || '';
  else if (stage === 3) m.s3.user = data.user || '';
  return data;
}

async function copyStagePrompt(stage) {
  if (!caption.value.imagePath || caption.value.manual.promptBusy) return;
  const m = caption.value.manual;
  m.promptBusy = true;
  caption.value.message = '';
  try {
    // withArtist 变更后，stage 3 prompt 需要重新拉；为简单起见每次都重拉。
    const data = await fetchStagePrompt(stage);
    const text = stage === 1 ? data.combined : data.user;
    await navigator.clipboard.writeText(text || '');
    const len = (text || '').length;
    const metaNote = m.metaUsed ? '' : ' · 未找到 viewer_data 元数据，prompt 是空白版';
    const stageLabel = stage === 1 ? 'Stage 1 (观察)'
                     : stage === 2 ? 'Stage 2 (校验)'
                     : 'Stage 3 (成文)';
    showToast(`已复制 ${stageLabel} 提示词（${len} 字）${metaNote}`, 'success');
  } catch (e) {
    showToast(`复制失败：${e.message || e}`, 'error');
  } finally {
    m.promptBusy = false;
  }
}

function parseStageJson(stage) {
  const m = caption.value.manual;
  const slot = stage === 1 ? m.s1 : m.s2;
  const raw = (slot.pasted || '').trim();
  if (!raw) {
    slot.parseError = '粘贴框为空';
    return;
  }
  try {
    const stripped = stripCodeFences(raw);
    const obj = JSON.parse(stripped);
    if (!obj || typeof obj !== 'object') throw new Error('解析结果不是对象');
    slot.parsed = obj;
    slot.parseError = '';
    m.stage = stage + 1;
    caption.value.message = `Stage ${stage} 已解析，进入 Stage ${m.stage}。`;
  } catch (e) {
    slot.parsed = null;
    slot.parseError = `JSON 解析失败：${e.message || e}`;
  }
}

function applyFinalCaption() {
  const m = caption.value.manual;
  const raw = (m.s3.pasted || '').trim();
  if (!raw) {
    m.s3.parseError = '粘贴框为空';
    return;
  }
  let parsed = null;
  try {
    const obj = JSON.parse(stripCodeFences(raw));
    if (obj && typeof obj === 'object') parsed = obj;
  } catch (e) {
    parsed = null;
  }
  if (parsed) {
    m.s3.parsed = parsed;
    caption.value.text = (parsed.caption_zh || parsed.caption || '').trim();
    caption.value.captionEn = (parsed.caption_en || '').trim();
    caption.value.verifiedTags = Array.isArray(parsed.verified_tags) ? parsed.verified_tags : [];
    m.s3.parseError = '';
    caption.value.message = '已解析结构化 JSON（中英 caption + 标签），点「保存」落盘。';
  } else {
    // 容错：模型没按 JSON 输出时，把整段当中文散文
    m.s3.parsed = null;
    caption.value.text = raw;
    caption.value.captionEn = '';
    caption.value.verifiedTags = [];
    m.s3.parseError = '⚠️ 未能解析为 JSON，已按纯中文文本应用（caption_en/标签为空）。';
    caption.value.message = '已按纯文本应用（非 JSON），点「保存」落盘。';
  }
  caption.value.errors = [];
  caption.value.loaded = true;
  caption.value.dirty = true;
  m.stage = 'done';
}

async function copyCaptionImage() {
  if (!caption.value.imagePath) return;
  if (!window.desktopAPI?.caption?.copyImage) {
    showToast('当前 Electron preload 缺少 caption.copyImage，请重启程序', 'error');
    return;
  }
  try {
    const result = await window.desktopAPI.caption.copyImage(caption.value.imagePath);
    if (result?.ok) {
      showToast('已复制图片到剪贴板，去 LLM 对话框 Ctrl+V 粘贴', 'success');
    } else {
      showToast(result?.error || '复制图片失败', 'error');
    }
  } catch (e) {
    showToast(`复制图片失败：${e.message || e}`, 'error');
  }
}

watch(() => caption.value.withArtist, (v) => {
  habits.captionWithArtist = v;
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
});

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
  if (task.value.maximized && logBodyRef.value) {
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
      if (format === 'favorited' && !isCardFavorited(item)) return false;
      if (format === 'captioned' && !hasCaption(item)) return false;
      if (format === 'not_captioned' && hasCaption(item)) return false;
    }

    if (hotOnly && (item.score || 0) < threshold) return false;

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
  const all = gallery.value.images;
  const filtered = filteredLocalImages.value;
  // 一趟遍历收集 >0 的分数并累加均值，再对（更小的）正分子集排序求中位数，
  // 避免原先 map+filter+spread+sort 的多次分配与多趟遍历（上千图时明显更省）
  let sum = 0;
  const scores = [];
  for (let i = 0; i < filtered.length; i++) {
    const s = filtered[i].score || 0;
    if (s > 0) { scores.push(s); sum += s; }
  }
  const avg = scores.length ? Math.round(sum / scores.length) : 0;
  scores.sort((a, b) => a - b);
  const median = scores.length ? scores[Math.floor(scores.length / 2)] : 0;
  return { total: all.length, filtered: filtered.length, avg, median };
});

// 搜索防抖：输入框绑 searchInput 即时回显，250ms 后才写回 gallery.search（真正参与过滤）
const searchInput = ref(gallery.value.search);
let searchDebounceTimer = null;
function onSearchInput() {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => { gallery.value.search = searchInput.value; }, 250);
}
// 程序化写入（清空按钮 / 查看器点 token 跳转）：立即同步两端并绕过防抖，避免回显与过滤关键字错位
function setSearch(keyword) {
  if (searchDebounceTimer) { clearTimeout(searchDebounceTimer); searchDebounceTimer = null; }
  const v = keyword || '';
  searchInput.value = v;
  gallery.value.search = v;
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
// 块导航：跳到目标块的「块首页」
function gotoBlock(blockIndex) {
  const b = Math.min(Math.max(0, blockIndex), totalBlocks.value - 1);
  gotoPage(b * PAGE_BLOCK_SIZE + 1);
}
function prevBlock() { gotoBlock(currentBlock.value - 1); }
function nextBlock() { gotoBlock(currentBlock.value + 1); }
function onBlockSelect(e) { gotoBlock(Number(e.target.value)); } // select value 是字符串

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
}

function toggleLogHeader() {
  // 缩小态：日志体收起，点击整条头部即可放大查看；
  // 放大态：只用右上角「缩小」图标收起，避免误触把日志收走
  if (task.value.maximized) return;
  task.value.maximized = true;
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
  if (!silent) loadingGallery.value = true;
  try {
    const data = await window.desktopAPI.gallery.getByDate(date || gallery.value.selectedDate);
    const normalizedImages = data.images.map(item => ({
      ...item,
      thumbUrl: '',
      postId: extractPostId(item),
      artistTokens: splitTags(item.artist),
      characterTokens: Array.isArray(item.characters) ? item.characters : splitTags(item.characters)
    }));
    gallery.value.selectedDate = data.selectedDate;
    gallery.value.availableDates = data.availableDates;
    // tag 文件夹由 IPC/后端透传，IPC fallback 没有此字段时给空数组兜底
    gallery.value.availableTags = Array.isArray(data.availableTags) ? data.availableTags : [];
    gallery.value.today = data.today;
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
    refreshCaptionedSet();
  } finally {
    if (!silent) loadingGallery.value = false;
  }
}

// 已生成 caption 的文件名集合（按当前日期目录）
const captionedSet = ref(new Set());
async function refreshCaptionedSet() {
  const date = gallery.value.selectedDate;
  if (!date || !window.desktopAPI?.caption?.listForDate) {
    captionedSet.value = new Set();
    return;
  }
  try {
    const names = await window.desktopAPI.caption.listForDate(date);
    captionedSet.value = new Set(Array.isArray(names) ? names : []);
  } catch {
    captionedSet.value = new Set();
  }
}
function hasCaption(item) {
  return !!item?.filename && captionedSet.value.has(item.filename);
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
      // 后端返回的 new_images 是模块全局 daily_viewer_data 的增量切片 —— tag 下载期间
      // 这份全局会被切换到 tag 文件夹，new_images 里也都是 tag 文件夹下的图。
      // 用 status.target_folder（后端当前实际写入的子目录）匹配当前画廊日期，
      // 不匹配就不合并，避免 tag 新图被 unshift 进日期画廊（一旦用户点开就会通过
      // refresh_visible 在日期 viewer_data.json 里追加错位的孤立条目）。
      if (status.target_folder && gallery.value.selectedDate === status.target_folder) {
        const appended = status.new_images.map(item => ({
          ...item,
          thumbUrl: '',
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

        await hydrateThumbs(pagedLocalImages.value);
      }
    }

    if (wasRunning && !task.value.isRunning) {
      if (status.backendError || task.value.backendError) {
        showToast("抓取任务异常停止！", "error");
      } else {
        showToast("抓取任务已完成！", "success");
      }
      // 只有「下载的日期 == 正在看的日期」才需要整盘 reload。下载 A 但在看 B 时，
      // B 的盘上数据没变，reload 只会白白跳回第 1 页 + 重排，还会打断 B 正在进行的刷新。
      // 复用上面 new_images 同款 target_folder 门控；preserveView=true 让同日期 reload
      // 也保持当前页与排序快照（新图落末尾，等用户主动「重新排序」）。
      const finishedFolder = status.target_folder || '';
      if (finishedFolder && finishedFolder === gallery.value.selectedDate) {
        await loadGallery(gallery.value.selectedDate, false, true);
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
    const payload = {
      start_page: Number(form.value.startPage) || 1,
      end_page: Number(form.value.endPage) || 1,
      tags: form.value.tags || '',
      mode: form.value.mode || 'rank',
      target_date: form.value.targetDate || '',
      start_date: form.value.startDate || '',
      end_date: form.value.endDate || '',
      tag_query: form.value.tagQuery || ''
    };
    if (payload.mode === 'download_ids') {
      const ids = parsePastedIds(form.value.idsText);
      if (ids.length) payload.ids = ids;
    } else if (payload.mode === 'tags') {
      if (!payload.tag_query.trim()) {
        showToast('请填写 tag 查询串（例如：hatsune_miku rating:safe）', 'error');
        return;
      }
      pushRecentPage(payload.mode, 'start', payload.start_page);
      pushRecentPage(payload.mode, 'end', payload.end_page);
    } else {
      // 记录这次实际使用的起始页/结束页，方便下次一键填回
      pushRecentPage(payload.mode, 'start', payload.start_page);
      pushRecentPage(payload.mode, 'end', payload.end_page);
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

// ================= 多任务队列 + 常用组合（习惯） =================
// 后端 MAX_CONCURRENT=1：队列在前端顺序驱动，逐个 start → 等完成 → 下一个。
// 完成检测只读响应式 task.value.isRunning（由 1.2s 的 pollTimer/syncStatus 维护），
// 绝不在这里自己调用 crawler.status()，否则会抢走 /api/status 的破坏性 drain、吞掉日志。
let _queueIdSeq = 0;
const taskQueue = ref([]);          // [{id, mode, startPage, endPage, tags, targetDate, startDate, endDate, tagQuery, idsText, label, status, error}]
const queueRunning = ref(false);    // 整个队列是否在跑
const queueAbort = ref(false);      // 停止队列的中断旗标
const queueIndex = ref(-1);         // 当前正在跑的项下标（-1 = 没在跑）
const taskCombos = ref(Array.isArray(habits.taskCombos) ? habits.taskCombos : []);
// 存为常用组合时的命名草稿（Electron 渲染进程不支持 window.prompt，改用应用内输入框）
const comboDraft = ref({ open: false, name: '' });

const MODE_NAMES = {
  rank: '排行榜', popular: '日期热门', popular_range: '日期范围',
  tags: '标签下载', collect_ids: '仅收集ID', download_ids: '按ID下载',
};

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function queueItemLabel(it) {
  const name = MODE_NAMES[it.mode] || it.mode;
  if (it.mode === 'tags') return `${name}「${it.tagQuery || ''}」 ${it.startPage}-${it.endPage}页`;
  if (it.mode === 'download_ids') return `${name} ${dateTokenLabel(it.targetDate)} (${parsePastedIds(it.idsText || '').length}个ID)`;
  if (it.mode === 'popular') return `${name} ${dateTokenLabel(it.targetDate)} ${it.startPage}-${it.endPage}页`;
  if (it.mode === 'popular_range') return `${name} ${dateTokenLabel(it.startDate)}~${dateTokenLabel(it.endDate)}`;
  return `${name} ${it.startPage}-${it.endPage}页`;
}

// 快照当前表单为一个队列项（日期转相对令牌，便于常用组合每天复用）
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
    idsText: f.idsText || '',
    status: 'pending',
    error: '',
  };
}

function addCurrentToQueue() {
  if (queueRunning.value) return;
  const f = form.value;
  // 与 startTask 一致的最小校验
  if (f.mode === 'tags' && !(f.tagQuery || '').trim()) {
    showToast('标签下载需要先填 tag 查询串', 'error');
    return;
  }
  if (f.mode === 'download_ids' && !parsePastedIds(f.idsText || '').length) {
    showToast('按ID下载需要先粘贴有效的 ID', 'error');
    return;
  }
  const item = snapshotFormToItem();
  item.label = queueItemLabel(item);
  taskQueue.value.push(item);
  showToast(`已加入队列：${item.label}`, 'success');
}

function removeQueueItem(i) {
  if (queueRunning.value) return;
  taskQueue.value.splice(i, 1);
}
function moveQueueItem(i, dir) {
  if (queueRunning.value) return;
  const j = i + dir;
  if (j < 0 || j >= taskQueue.value.length) return;
  const arr = taskQueue.value;
  [arr[i], arr[j]] = [arr[j], arr[i]];
}
function clearQueue() {
  if (queueRunning.value) return;
  taskQueue.value = [];
}

function buildQueuePayload(it) {
  const payload = {
    start_page: Number(it.startPage) || 1,
    end_page: Number(it.endPage) || 1,
    tags: it.tags || '',
    mode: it.mode || 'rank',
    target_date: resolveDateToken(it.targetDate || ''),
    start_date: resolveDateToken(it.startDate || ''),
    end_date: resolveDateToken(it.endDate || ''),
    tag_query: it.tagQuery || '',
  };
  if (payload.mode === 'download_ids') {
    const ids = parsePastedIds(it.idsText || '');
    if (ids.length) payload.ids = ids;
  }
  // popular / popular_range 缺日期时兜底昨天（snapshot 时通常已填，这里双保险）
  if (payload.mode === 'popular' && !payload.target_date) payload.target_date = yesterdayString();
  if (payload.mode === 'popular_range') {
    if (!payload.start_date) payload.start_date = yesterdayString();
    if (!payload.end_date) payload.end_date = yesterdayString();
  }
  return payload;
}

// 等当前任务跑完：只读 task.value.isRunning（1.2s pollTimer 维护），不自己调 status。
async function waitForTaskIdle() {
  // 1) 确认任务起来了（后端 start 已同步置 is_running；兜极快完成的任务，最多等 ~4s）
  const t0 = Date.now();
  while (!task.value.isRunning && (Date.now() - t0) < 4000) {
    if (queueAbort.value) return;
    await sleep(300);
  }
  // 2) 等它结束
  while (task.value.isRunning) {
    if (queueAbort.value) return;
    await sleep(500);
  }
}

async function runQueue() {
  if (queueRunning.value) return;
  if (!taskQueue.value.length) { showToast('队列为空', 'info'); return; }
  if (task.value.isRunning) { showToast('已有任务在跑，先停止再运行队列', 'error'); return; }
  queueRunning.value = true;
  queueAbort.value = false;
  taskQueue.value.forEach(it => { it.status = 'pending'; it.error = ''; });
  let okCount = 0, failCount = 0;
  try {
    for (let i = 0; i < taskQueue.value.length; i++) {
      if (queueAbort.value) break;
      const item = taskQueue.value[i];
      const n = taskQueue.value.length;
      queueIndex.value = i;
      item.status = 'running';
      try {
        const payload = buildQueuePayload(item);
        const result = await window.desktopAPI.crawler.start(payload);
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
        if (task.value.backendError) {
          item.status = 'error';
          item.error = task.value.backendError;
          failCount += 1;
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
  if (queueAbort.value) showToast(`队列已停止（完成 ${okCount}，失败 ${failCount}）`, 'info');
  else showToast(`队列完成：成功 ${okCount}，失败 ${failCount}`, failCount ? 'warning' : 'success');
}

async function stopQueue() {
  if (!queueRunning.value) return;
  queueAbort.value = true;
  if (task.value.isRunning) await stopTask();
  showToast('正在停止队列...', 'info');
}

// ---- 常用组合（习惯）：持久化在 localStorage.crawlerHabits.taskCombos ----
function persistCombos() {
  habits.taskCombos = taskCombos.value.map(c => ({ name: c.name, tasks: c.tasks }));
  localStorage.setItem('crawlerHabits', JSON.stringify(habits));
}
function stripItemForSave(it) {
  // 只存可复现字段（保留日期相对令牌），不存 id/label/status
  return {
    mode: it.mode,
    startPage: it.startPage,
    endPage: it.endPage,
    tags: it.tags,
    targetDate: it.targetDate,
    startDate: it.startDate,
    endDate: it.endDate,
    tagQuery: it.tagQuery,
    idsText: it.idsText,
  };
}
function saveQueueAsCombo() {
  if (queueRunning.value) return;
  if (!taskQueue.value.length) { showToast('队列为空，无法保存', 'info'); return; }
  // Electron 不支持 window.prompt，打开应用内输入框取名
  comboDraft.value.name = '晨间例行';
  comboDraft.value.open = true;
  nextTick(() => {
    const el = document.getElementById('combo-name-input');
    if (el) { el.focus(); el.select(); }
  });
}
function confirmSaveCombo() {
  const name = (comboDraft.value.name || '').trim();
  if (!name) { showToast('请输入组合名字', 'error'); return; }
  if (!taskQueue.value.length) { comboDraft.value.open = false; return; }
  const combo = { name, tasks: taskQueue.value.map(stripItemForSave) };
  const idx = taskCombos.value.findIndex(c => c.name === name);
  if (idx >= 0) taskCombos.value.splice(idx, 1, combo);
  else taskCombos.value.push(combo);
  persistCombos();
  comboDraft.value.open = false;
  showToast(`已保存常用组合「${name}」`, 'success');
}
function cancelSaveCombo() { comboDraft.value.open = false; }
function loadCombo(combo) {
  if (queueRunning.value) return;
  taskQueue.value = (combo.tasks || []).map(t => {
    const it = { ...t, id: ++_queueIdSeq, status: 'pending', error: '' };
    it.label = queueItemLabel(it);
    return it;
  });
}
async function runCombo(combo) {
  if (queueRunning.value || task.value.isRunning) {
    showToast('已有任务/队列在跑，先停止再运行组合', 'error');
    return;
  }
  loadCombo(combo);
  await nextTick();
  await runQueue();
}
function deleteCombo(name) {
  if (queueRunning.value) return;
  const idx = taskCombos.value.findIndex(c => c.name === name);
  if (idx >= 0) {
    taskCombos.value.splice(idx, 1);
    persistCombos();
  }
}
function comboTooltip(combo) {
  return (combo.tasks || []).map((t, i) => `${i + 1}. ${queueItemLabel({ ...t })}`).join('\n');
}
function queueStatusIcon(it) {
  return { pending: '○', running: '⏳', done: '✓', error: '⚠' }[it.status] || '○';
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
  const items = filteredLocalImages.value;
  const index = items.findIndex(candidate => (candidate.localPath || candidate.filename) === (item.localPath || item.filename));
  viewer.value.open = true;
  viewer.value.index = Math.max(0, index);
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
  if (gallery.value.refreshOnView) refreshSinglePost(viewerItem.value);
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

// 切换缩略图分辨率：清掉已生成的 thumbUrl，按新尺寸重建当前页（其余页翻到时惰性重建）
watch(() => gallery.value.thumbSize, () => {
  gallery.value.images.forEach(it => { it.thumbUrl = ''; });
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
  document.addEventListener('click', onDocClickForPagePicker);
});

onBeforeUnmount(() => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
  if (pollTimer) window.clearInterval(pollTimer);
  window.removeEventListener('keydown', onKeyDown);
  document.removeEventListener('click', onDocClickForRefreshMenu);
  document.removeEventListener('click', onDocClickForTranslateMenu);
  document.removeEventListener('click', onDocClickForPagePicker);
  window.removeEventListener('resize', positionPagePicker);
  window.removeEventListener('scroll', positionPagePicker, true);
});

const modeDescription = computed(() => {
  switch (form.value.mode) {
    case 'rank': return '获取当日 Danbooru 排行榜最受欢迎的图片并自动下载。';
    case 'popular': return '根据指定日期，获取 Explore 页面当天的热门图片。';
    case 'popular_range': return '设定起始与结束日期，批量抓取这段时间内所有的热门图片。';
    case 'collect_ids': return '网络状况不佳时的极速模式：仅拉取列表和元数据，不下载图片本体。';
    case 'download_ids': return '从已收集的 ID 列表中进行批量下载。';
    case 'tags': return '按 tag 查询下载到tag文件夹。';
    default: return '选择模式后配置参数，点击启动开始执行。';
  }
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
        <button
          class="ghost proxy-mode-btn"
          :class="{ 'is-proxy': useProxy, 'is-direct': !useProxy }"
          @click="toggleProxy"
          :title="useProxy ? '当前走代理下载。关掉代理软件后请点这里切到「直连」，否则下载会连不上死代理端口' : '当前直连下载（不走代理）。开了代理软件可点这里切回「走代理」'"
        >{{ useProxy ? '🌐 走代理' : '🔌 直连' }}</button>
      </div>

      <div class="mode-selector">
        <button class="mode-chip" :class="{ active: form.mode === 'rank' }" @click="form.mode = 'rank'"
          title="按 Danbooru 排行榜抓取并下载图片">排行榜</button>
        <button class="mode-chip" :class="{ active: form.mode === 'popular' }" @click="form.mode = 'popular'"
          title="按指定日期获取热门帖子并下载">日期热门</button>
        <button class="mode-chip" :class="{ active: form.mode === 'popular_range' }" @click="form.mode = 'popular_range'"
          title="按指定日期范围获取热门帖子并下载">日期范围</button>
        <button class="mode-chip" :class="{ active: form.mode === 'tags' }" @click="form.mode = 'tags'"
          title="按 tag 查询下载到独立的 tag_xxx 文件夹，与日期文件夹并行">标签下载</button>
        <button class="mode-chip" :class="{ active: form.mode === 'collect_ids' }" @click="form.mode = 'collect_ids'"
          title="网不好时只收集 ID，不下载图片">仅收集ID</button>
        <button class="mode-chip" :class="{ active: form.mode === 'download_ids' }" @click="form.mode = 'download_ids'"
          title="从已收集的 ID 列表批量下载">按ID下载</button>
      </div>

      <div class="field-grid pages-grid" v-if="form.mode !== 'download_ids'">
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
      <label class="field-full" v-if="['popular', 'download_ids'].includes(form.mode)">
        <span>目标日期 <span class="muted compact-text">{{ form.mode === 'popular' ? '(默认昨天，可改)' : '(留空则用今天)' }}</span></span>
        <input v-model="form.targetDate" type="date" />
      </label>
      <label class="field-full" v-if="form.mode === 'download_ids'">
        <span>
          粘贴 ID 列表
        </span>
        <textarea
          v-model="form.idsText"
          rows="2"
          placeholder="1) 压缩：dbids:6tewdt.dw&#10;2) 明文： 行分隔 / 逗号分隔 或 URL"
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
          下载到独立文件夹 <strong style="color: var(--accent-deep); font-family: Consolas, monospace;">{{ tagFolderPreview || 'tag_...' }}</strong>
        </div>
      </label>
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

      <!-- 多任务队列：把多个下载任务排队顺序执行；可存为常用组合一键复用 -->
      <div class="task-queue-panel">
        <div class="tq-header">
          <span class="tq-title">🗂 多任务队列<span v-if="queueRunning" class="tq-running-badge">运行中 {{ queueIndex + 1 }}/{{ taskQueue.length }}</span></span>
          <button class="ghost tq-add" @click="addCurrentToQueue" :disabled="queueRunning" title="把上方当前配置作为一个任务加入队列">＋ 加入队列</button>
        </div>

        <div v-if="taskQueue.length" class="tq-list">
          <div
            v-for="(it, i) in taskQueue"
            :key="it.id"
            class="tq-item"
            :class="[it.status, { current: queueRunning && i === queueIndex }]"
            :title="it.error || queueItemLabel(it)"
          >
            <span class="tq-item-status">{{ queueStatusIcon(it) }}</span>
            <span class="tq-item-idx">{{ i + 1 }}</span>
            <span class="tq-item-label">{{ it.label }}</span>
            <span class="tq-item-ops">
              <button class="tq-mini" @click="moveQueueItem(i, -1)" :disabled="queueRunning || i === 0" title="上移">↑</button>
              <button class="tq-mini" @click="moveQueueItem(i, 1)" :disabled="queueRunning || i === taskQueue.length - 1" title="下移">↓</button>
              <button class="tq-mini tq-del" @click="removeQueueItem(i)" :disabled="queueRunning" title="移除">×</button>
            </span>
          </div>
        </div>
        <div v-else class="tq-empty">
        </div>

        <div v-if="taskQueue.length" class="tq-actions">
          <button class="tq-run" @click="runQueue" :disabled="queueRunning || task.isRunning" title="按顺序依次执行队列里的全部任务">运行（{{ taskQueue.length }}）</button>
          <button class="secondary" @click="stopQueue" :disabled="!queueRunning">停止</button>
          <button class="ghost" @click="clearQueue" :disabled="queueRunning">清空</button>
          <button class="ghost" @click="saveQueueAsCombo" :disabled="queueRunning" title="把当前队列存成常用组合，下次一键执行">保存</button>
        </div>

        <div v-if="comboDraft.open" class="tq-combo-save">
          <input
            id="combo-name-input"
            v-model="comboDraft.name"
            type="text"
            maxlength="40"
            placeholder="组合名字，如：晨间例行"
            class="tq-combo-save-input"
            @keyup.enter="confirmSaveCombo"
            @keyup.esc="cancelSaveCombo"
          />
          <button class="secondary" @click="confirmSaveCombo">保存</button>
          <button class="ghost" @click="cancelSaveCombo">取消</button>
        </div>

        <div v-if="taskCombos.length" class="tq-combos">
          <span v-for="c in taskCombos" :key="c.name" class="tq-combo-chip">
            <button class="tq-combo-run" @click="runCombo(c)" :disabled="queueRunning || task.isRunning" :title="comboTooltip(c)">▶ {{ c.name }}</button>
            <button class="tq-combo-load" @click="loadCombo(c)" :disabled="queueRunning" title="只载入到队列，不立即运行">载入</button>
            <button class="tq-mini tq-del" @click="deleteCombo(c.name)" :disabled="queueRunning" title="删除该组合">×</button>
          </span>
        </div>
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

      <div class="modern-log-wrapper" :class="{ 'is-maximized': task.maximized }">
        <div class="modern-log-header" @click="toggleLogHeader">
          <div class="log-header-left">
            <span class="status-dot" :class="{ 'is-active': task.isRunning }"></span>
            <span class="log-title">运行动态</span>
          </div>
          <div class="log-header-right" @click.stop>
            <!-- 缩小态只保留「放大」图标；显示全部/清空属于查看日志时才用得到的操作，放大后再出现 -->
            <template v-if="task.maximized">
              <button
                class="log-toolbar-btn"
                :class="{ active: task.hideSuccess }"
                @click="task.hideSuccess = !task.hideSuccess"
                :title="task.hideSuccess ? '当前隐藏单张下载/已存在日志，点击切换为显示全部' : '当前显示全部日志，点击只看异常与概要'"
              >{{ task.hideSuccess ? '只看异常+概要' : '显示全部' }}</button>
              <button class="log-toolbar-btn" @click="clearLogs" title="清空当前日志">清空</button>
            </template>
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
          <div class="modern-log-body" v-show="task.maximized" ref="logBodyRef">
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
          >🔃 排序</button>
          <select v-model="gallery.filterFormat" class="search-input" style="width: auto;">
            <option value="all">全部格式</option>
            <option value="image">图片</option>
            <option value="video">视频</option>
            <option value="zip">动图ZIP</option>
            <option value="favorited">仅收藏画师/角色</option>
            <option value="captioned">仅已生成 Caption</option>
            <option value="not_captioned">仅未生成 Caption</option>
          </select>
          <select v-model.number="gallery.cardSize" class="search-input" style="width: auto;" title="卡片大小">
            <option :value="120">紧凑</option>
            <option :value="150">小</option>
            <option :value="180">默认</option>
            <option :value="220">大</option>
          </select>
          <select v-model.number="gallery.thumbSize" class="search-input" style="width: auto;" title="缩略图分辨率：越低越省内存、翻页越流畅；点开看大图仍是原图">
            <option :value="240">省流</option>
            <option :value="360">标准</option>
            <option :value="540">高清</option>
            <option :value="0">原图</option>
          </select>
          <button
            :class="['hot-toggle', { active: gallery.hotOnly }]"
            @click="gallery.hotOnly = !gallery.hotOnly"
            :title="`只看 score ≥ ${gallery.hotThreshold}`"
          >🔥 高分</button>
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
            <input v-model="searchInput" @input="onSearchInput" class="search-input search-input-with-clear" type="text" placeholder="搜索作者 / 角色" />
            <button
              v-if="searchInput"
              class="search-clear-btn"
              @click="setSearch('')"
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
          <button
            :class="['hot-toggle', { active: gallery.refreshOnView }]"
            @click="gallery.refreshOnView = !gallery.refreshOnView"
            title="开启后，点开 / 切换大图会联网刷新该图 score / 收藏数；离线时建议保持关闭"
          >{{ gallery.refreshOnView ? '刷新：开' : '刷新：关' }}</button>
          <input type="file" ref="translationFileInput" style="display: none" accept=".json" @change="onTranslationFileSelected" />
        </div>
      </div>

      <!-- 统一选择器：日期日历 + tag 文件夹列表（搜索 + 最近使用置顶），
           内部根据 selectedDate 是否以 'tag_' 开头决定默认 tab -->
      <GalleryCalendar
        :available-dates="gallery.availableDates"
        :available-tags="gallery.availableTags"
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
        <article v-for="item in activeItems" :key="item.localPath || item.filename" class="image-card" :class="{ 'is-favorited': isCardFavorited(item), 'is-img-favorited': isImageFavorited(item), 'is-selected': isItemSelected(item), 'has-caption': hasCaption(item) }">
          <div class="thumb-wrap">
            <img class="thumb clickable-thumb" :src="item.thumbUrl" :alt="item.filename" loading="lazy" decoding="async" @click="onThumbClick($event, item)" />
            <button
              v-if="selection.enabled"
              class="img-select-toggle"
              :class="{ active: isItemSelected(item) }"
              @click.stop="toggleItemSelection(item)"
              :title="isItemSelected(item) ? '取消选择' : '加入选择'"
            >{{ isItemSelected(item) ? '✓' : '' }}</button>
            <span v-if="hasCaption(item)" class="caption-badge" title="已生成 Caption，点击查看/编辑">📝</span>
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
          >👆</button>
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
        </div>
        <div class="button-row compact viewer-actions">
          <button class="secondary" @click="stepViewer(-1)" :disabled="viewer.index <= 0">上一张</button>
          <button class="secondary" @click="stepViewer(1)" :disabled="viewer.index >= viewerItems.length - 1">下一张</button>
          <button
            class="viewer-fav-btn"
            :class="{ active: viewerItem && isImageFavorited(viewerItem) }"
            @click="viewerItem && toggleImageFavorite(viewerItem)"
            :disabled="!viewerItem"
            :title="viewerItem && isImageFavorited(viewerItem) ? '取消图片收藏' : '加入图片收藏'"
          >{{ viewerItem && isImageFavorited(viewerItem) ? '♥ 已收藏' : '♡ 收藏' }}</button>
          <button v-if="viewerItem?.filename?.toLowerCase().endsWith('.zip')" class="secondary" @click="convertGif(viewerItem)" style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;">转GIF</button>
          <button
            @click="openCaptionWindow"
            :style="hasCaption(viewerItem)
              ? 'background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;'
              : 'background: linear-gradient(135deg, #8b5cf6, #6d28d9); border: none; color: white;'"
            :title="hasCaption(viewerItem) ? '已生成 Caption，点击查看/编辑' : '为这张图生成 AI 描述'"
          >{{ hasCaption(viewerItem) ? '📝 Caption ✓' : '📝 Caption' }}</button>
          <button @click="editItem(viewerItem)" style="background: linear-gradient(135deg, var(--accent), var(--accent-deep)); border: none; color: white;">编辑图片</button>
          <button class="ghost" @click="closeViewer" style="color: #fff; border: 1px solid rgba(255,255,255,0.2);">关闭</button>
        </div>
      </div>

      <!-- 始终显示的右上角信息小栏：计数 / 适应窗口 / 固定信息栏。
           独立于顶部置顶 toolbar，不随 viewerToolbarVisible 收起 -->
      <div class="viewer-corner-info">
        <div class="viewer-corner-row viewer-corner-counter">
          <span class="viewer-corner-counter-label">第</span>
          <input
            class="viewer-jump-input viewer-corner-jump"
            type="number"
            min="1"
            :max="viewerItems.length"
            :value="viewer.index + 1"
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
        >{{ viewer.fitMode === 'fit' ? '⛶ 原始大小' : '▣ 适应窗口' }}</button>
        <button
          class="viewer-corner-row viewer-corner-btn"
          :class="{ 'is-active': viewer.toolbarPinned }"
          @click="toggleViewerToolbarPin"
          :title="viewer.toolbarPinned ? '已固定信息栏，点击取消固定（恢复鼠标悬浮显示）' : '固定信息栏（默认悬浮显示）'"
        >{{ viewer.toolbarPinned ? '📌 已固定' : '📌 固定' }}</button>
      </div>

      <!-- 左右切换箭头：默认半透明，悬浮变明显；在边界自动隐藏 -->
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
        v-show="viewer.index < viewerItems.length - 1"
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

    <!-- Caption 悬浮窗口（可拖动，不全屏遮罩，可同时看图） -->
    <div
      v-if="caption.open"
      class="caption-panel floating"
      :style="{ left: caption.pos.x + 'px', top: caption.pos.y + 'px' }"
    >
        <div class="caption-panel-header" @mousedown="onCaptionDragStart">
          <h3>📝 图片描述 (Caption) <span class="caption-drag-hint">拖动标题栏移动窗口</span></h3>
          <button class="ghost" @click="closeCaptionWindow" title="关闭">✕</button>
        </div>
        <!-- 可滚动主体（改动1）：标题栏留在外面当固定拖动把手，其余内容放进这里。 -->
        <!-- 内容超高时本容器内部滚动，子项 flex-shrink:0 不再被压扁（修复 Stage 2/3 挤压）。 -->
        <div class="caption-panel-scroll">
        <div class="caption-panel-meta" v-if="caption.meta">
          <span v-if="caption.meta.characters"><b>角色：</b>{{ caption.meta.characters }}</span>
          <span v-if="caption.meta.copyright"><b>作品：</b>{{ caption.meta.copyright }}</span>
          <span v-if="caption.withArtist && caption.meta.artist"><b>画师：</b>{{ caption.meta.artist }}</span>
        </div>
        <div class="caption-panel-controls">
          <label class="caption-toggle">
            <input type="checkbox" v-model="caption.withArtist" />
            包含画师信息
          </label>
          <div class="caption-mode-switch" role="tablist">
            <button :class="{ active: caption.mode === 'mark' }" @click="caption.mode = 'mark'" title="拖选标红错误段">🖍 标记模式</button>
            <button :class="{ active: caption.mode === 'edit' }" @click="caption.mode = 'edit'" title="在下方文本框里直接编辑描述文本">✏️ 输入模式</button>
          </div>
          <div class="caption-panel-actions">
            <button :disabled="!captionHasContent || caption.saving" @click="saveCaption" style="background: linear-gradient(135deg, #10b981, #059669); border: none; color: white;">
              {{ caption.saving ? '保存中...' : '💾 保存' }}
            </button>
          </div>
        </div>
        <!-- Pipeline 手动模式：主要入口。在外部 chat LLM 同一对话里连续粘贴 stage 1/2/3 -->
        <div class="caption-manual pipeline-manual">
          <div class="pipeline-header">
            <span class="pipeline-title">🧭 Pipeline 手动模式 <span class="pipeline-badge">主入口</span></span>
            <button class="ghost pipeline-reset" @click="resetManualPipeline" title="清空所有 stage 重新开始">↻ 重置</button>
          </div>
          <div class="pipeline-progress">
            <span :class="['pipeline-dot', caption.manual.stage === 1 ? 'active' : (caption.manual.stage > 1 || caption.manual.stage === 'done' ? 'done' : '')]">①</span>
            <span class="pipeline-dot-label">观察</span>
            <span class="pipeline-dash">─</span>
            <span :class="['pipeline-dot', caption.manual.stage === 2 ? 'active' : (caption.manual.stage > 2 || caption.manual.stage === 'done' ? 'done' : '')]">②</span>
            <span class="pipeline-dot-label">校验</span>
            <span class="pipeline-dash">─</span>
            <span :class="['pipeline-dot', caption.manual.stage === 3 ? 'active' : (caption.manual.stage === 'done' ? 'done' : '')]">③</span>
            <span class="pipeline-dot-label">成文</span>
          </div>
          <p class="pipeline-tip">
            打开 Claude / ChatGPT / Gemini Web，<b>在同一个对话</b>里依次粘贴 3 段提示词；每轮把返回粘回对应文本框。
          </p>

          <!-- Stage 1: observe -->
          <div :class="['pipeline-stage', caption.manual.stage === 1 ? 'active' : (caption.manual.stage > 1 || caption.manual.stage === 'done' ? 'done' : 'locked')]">
            <div class="pipeline-stage-head">
              <span class="pipeline-stage-title">① Stage 1 · 观察 (得到 JSON)</span>
              <span v-if="caption.manual.stage > 1 || caption.manual.stage === 'done'" class="pipeline-stage-status done">✓ 已完成</span>
              <span v-else-if="caption.manual.stage === 1" class="pipeline-stage-status active">当前</span>
            </div>
            <div v-if="caption.manual.stage === 1" class="pipeline-stage-body">
              <div class="pipeline-stage-buttons">
                <button class="secondary" :disabled="caption.manual.promptBusy" @click="copyStagePrompt(1)">
                  {{ caption.manual.promptBusy ? '复制中...' : '📋 复制 Stage 1 提示词' }}
                </button>
                <button class="secondary" @click="copyCaptionImage">🖼️ 复制图片</button>
              </div>
              <p class="pipeline-stage-hint">在 LLM 新对话粘贴提示词 + 图片，把返回的 JSON 整段复制到下方：</p>
              <textarea
                v-model="caption.manual.s1.pasted"
                rows="4"
                placeholder='粘贴 Stage 1 返回的 JSON，例如：{"subjects_count": 1, ...}'
                class="caption-manual-paste"
              ></textarea>
              <div v-if="caption.manual.s1.parseError" class="pipeline-error">⚠️ {{ caption.manual.s1.parseError }}</div>
              <button
                :disabled="!caption.manual.s1.pasted.trim()"
                @click="parseStageJson(1)"
                class="caption-manual-apply"
              >解析并进入 Stage 2 →</button>
            </div>
            <div v-else-if="caption.manual.stage > 1 || caption.manual.stage === 'done'" class="pipeline-stage-summary">
              已解析 {{ Object.keys(caption.manual.s1.parsed || {}).length }} 个字段
            </div>
          </div>

          <!-- Stage 2: verify -->
          <div :class="['pipeline-stage', caption.manual.stage === 2 ? 'active' : (caption.manual.stage > 2 || caption.manual.stage === 'done' ? 'done' : 'locked')]">
            <div class="pipeline-stage-head">
              <span class="pipeline-stage-title">② Stage 2 · 校验 (得到 JSON)</span>
              <span v-if="caption.manual.stage > 2 || caption.manual.stage === 'done'" class="pipeline-stage-status done">✓ 已完成</span>
              <span v-else-if="caption.manual.stage === 2" class="pipeline-stage-status active">当前</span>
              <span v-else class="pipeline-stage-status locked">🔒 等待 Stage 1</span>
            </div>
            <div v-if="caption.manual.stage === 2" class="pipeline-stage-body">
              <div class="pipeline-stage-buttons">
                <button class="secondary" :disabled="caption.manual.promptBusy" @click="copyStagePrompt(2)">
                  {{ caption.manual.promptBusy ? '复制中...' : '📋 复制 Stage 2 提示词' }}
                </button>
              </div>
              <p class="pipeline-stage-hint">在<b>同一对话</b>粘贴上面这段，得到校验 JSON 后粘回下方（含 tag visible/absent 标注）：</p>
              <textarea
                v-model="caption.manual.s2.pasted"
                rows="4"
                placeholder='粘贴 Stage 2 返回的 JSON，例如：{"character_identification": {...}, "tag_evaluation": [...]}'
                class="caption-manual-paste"
              ></textarea>
              <div v-if="caption.manual.s2.parseError" class="pipeline-error">⚠️ {{ caption.manual.s2.parseError }}</div>
              <button
                :disabled="!caption.manual.s2.pasted.trim()"
                @click="parseStageJson(2)"
                class="caption-manual-apply"
              >解析并进入 Stage 3 →</button>
            </div>
            <div v-else-if="caption.manual.stage > 2 || caption.manual.stage === 'done'" class="pipeline-stage-summary">
              已解析 · tag 评估 {{ (caption.manual.s2.parsed?.tag_evaluation || []).length }} 条 · 角色一致性 {{ caption.manual.s2.parsed?.character_identification?.consistent === false ? '❌ 不一致' : (caption.manual.s2.parsed?.character_identification?.consistent ? '✓ 一致' : '—') }}
            </div>
          </div>

          <!-- Stage 3: compose（结构化 JSON） -->
          <div :class="['pipeline-stage', caption.manual.stage === 3 ? 'active' : (caption.manual.stage === 'done' ? 'done' : 'locked')]">
            <div class="pipeline-stage-head">
              <span class="pipeline-stage-title">③ Stage 3 · 成文 (结构化 JSON：中英 caption + 标签)</span>
              <span v-if="caption.manual.stage === 'done'" class="pipeline-stage-status done">✓ 已完成</span>
              <span v-else-if="caption.manual.stage === 3" class="pipeline-stage-status active">当前</span>
              <span v-else class="pipeline-stage-status locked">🔒 等待 Stage 2</span>
            </div>
            <div v-if="caption.manual.stage === 3" class="pipeline-stage-body">
              <div class="pipeline-stage-buttons">
                <button class="secondary" :disabled="caption.manual.promptBusy" @click="copyStagePrompt(3)">
                  {{ caption.manual.promptBusy ? '复制中...' : '📋 复制 Stage 3 提示词' }}
                </button>
              </div>
              <p class="pipeline-stage-hint">在同一对话粘贴这段，把返回的<b>结构化 JSON</b>（含 caption_en / caption_zh / verified_tags）粘到下方：</p>
              <textarea
                v-model="caption.manual.s3.pasted"
                rows="6"
                placeholder='粘贴 Stage 3 返回的 JSON，例如：{"caption_en": "[SUBJECT]\n1girl...", "caption_zh": "...", "verified_tags": [...]}'
                class="caption-manual-paste"
              ></textarea>
              <div v-if="caption.manual.s3.parseError" class="pipeline-error">⚠️ {{ caption.manual.s3.parseError }}</div>
              <button
                :disabled="!caption.manual.s3.pasted.trim()"
                @click="applyFinalCaption"
                class="caption-manual-apply pipeline-finish"
              >✓ 解析并应用</button>
            </div>
            <div v-else-if="caption.manual.stage === 'done'" class="pipeline-stage-summary">
              已应用 · 中文 {{ (caption.text || '').length }} 字 · 英文 {{ caption.captionEn ? '✓' : '—' }} · 标签 {{ (caption.verifiedTags || []).length }} 个
            </div>
          </div>
        </div>

        <!-- 结构化产物：英文分区 caption（只读）+ 可增删的已校验标签 + 待确认锚点 -->
        <div v-if="caption.loaded || caption.captionEn || (caption.verifiedTags && caption.verifiedTags.length) || reviewAnchors.length" class="caption-structured">
          <div v-if="caption.captionEn" class="caption-structured-block">
            <div class="caption-structured-label">caption_en（训练用，只读）</div>
            <pre class="caption-structured-en">{{ caption.captionEn }}</pre>
          </div>
          <div class="caption-structured-block">
            <div class="caption-structured-label">verified_tags（{{ (caption.verifiedTags || []).length }}）· 可增删</div>
            <div class="caption-structured-tags">
              <span v-for="(t, i) in caption.verifiedTags" :key="`vt-${i}`" class="caption-chip caption-chip-editable">
                {{ t }}
                <button class="caption-chip-x" @click="removeVerifiedTag(i)" title="删除此标签">×</button>
              </span>
              <span v-if="!caption.verifiedTags || !caption.verifiedTags.length" class="caption-tags-empty">（暂无标签）</span>
            </div>
            <div class="caption-tag-add">
              <input
                v-model="caption.tagInput"
                @keyup.enter="addVerifiedTag()"
                placeholder="添加 booru 标签，回车确认（如 blue_hair）"
                class="caption-tag-input"
              />
              <button class="secondary" :disabled="!caption.tagInput.trim()" @click="addVerifiedTag()">＋ 添加</button>
            </div>
          </div>
          <!-- 模型存疑、danbooru 未标的高显著锚点：人工确认后再加入 verified_tags -->
          <div v-if="reviewAnchors.length" class="caption-structured-block">
            <div class="caption-structured-label">⚠️ 待确认锚点（danbooru 未标，模型存疑——发型≈兽耳等易误判，确认真有再加入）</div>
            <div class="caption-structured-tags">
              <span v-for="(a, i) in reviewAnchors" :key="`ra-${i}`" class="caption-chip caption-chip-review" :title="a.reason || ''">
                {{ a.tag }}
                <button class="caption-chip-add" @click="addVerifiedTag(a.tag)" title="确认并加入 verified_tags">＋</button>
              </span>
            </div>
          </div>
        </div>
        <div class="caption-hint" v-if="caption.text || caption.mode === 'edit'">
          <template v-if="caption.mode === 'mark'">
            <b>标记模式</b>：拖选错误片段 → 标红（尾部紧贴标点自动吸附）；点击红段撤销。
          </template>
          <template v-else>
            <b>输入模式</b>：在下方文本框里直接编辑描述文本，改完点上方「💾 保存」。
          </template>
        </div>

        <!-- 底部内容区（改动3）：输入模式=可直接编辑文本框；标记模式=红标渲染。 -->
        <textarea
          v-if="caption.mode === 'edit'"
          v-model="caption.text"
          @input="caption.dirty = true"
          class="caption-text-edit"
          placeholder="在此直接编辑描述文本，改完点上方「💾 保存」。"
        ></textarea>
        <div v-else-if="!caption.text" class="caption-empty">尚未生成。用上方 Pipeline 手动模式生成，或切到输入模式直接编写。</div>
        <div v-else class="caption-text-area" ref="captionTextRef" @mouseup="markCurrentSelection">
          <template v-for="(seg, i) in captionRenderSegments" :key="i">
            <span v-if="seg.type === 'text'">{{ seg.text }}</span>
            <span v-else class="caption-error" @click.stop="removeErrorSpan(seg.idx)" :title="`点击撤销标红：${seg.text}`">{{ seg.text }}</span>
          </template>
        </div>
        <div v-if="caption.errors.length" class="caption-marks-summary">
          <div class="caption-error-list">
            <div class="caption-error-list-title">
              <span>🔴 错误标记（{{ caption.errors.length }}）</span>
              <button class="ghost" @click="clearAllErrors">全部清除</button>
            </div>
            <ol>
              <li v-for="(e, i) in caption.errors" :key="`err-${i}`">
                <span>{{ e.text }}</span>
                <button class="ghost" @click="removeErrorSpan(i)" title="移除">✕</button>
              </li>
            </ol>
          </div>
        </div>
        <div v-if="caption.message" class="caption-message">{{ caption.message }}</div>
        </div><!-- /caption-panel-scroll -->
    </div>

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
  gap: 10px;
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
.caption-tag-input:focus { border-color: rgba(99, 102, 241, 0.55); }
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
  /* Teleport 到 body + position:fixed：彻底脱离分页栏(.cr-pg-bar 的 overflow 裁剪)
     与图片卡片(content-visibility:auto 形成的层叠上下文)，确保浮层永远在最上层、
     不被图片盖住。right/bottom 由 JS 按 .pg-picker-host 的视口位置实时计算
     （见 positionPagePicker）。 */
  position: fixed;
  z-index: 9000;
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
  background: linear-gradient(135deg, #fbf4eb, #f2e8db);
  color: var(--ink);
  border: 1px solid rgba(74, 53, 25, 0.12);
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
  scrollbar-width: thin;
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

/* ---------------- 多任务队列 + 常用组合 ---------------- */
.task-queue-panel {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.025);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tq-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.tq-title {
  font-weight: 700;
  font-size: 13px;
  color: var(--accent-deep);
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.tq-running-badge {
  font-weight: 600;
  font-size: 11px;
  color: #2a6f8e;
  background: rgba(59, 130, 160, 0.15);
  border: 1px solid rgba(59, 130, 160, 0.4);
  border-radius: 999px;
  padding: 1px 8px;
}
.tq-add { font-size: 12px; padding: 3px 10px; }
.tq-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  /* 最多显示约 2 行任务，再多用滚轮纵向收纳，避免把下方日志挤出去 */
  max-height: 72px;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 2px;
}
.tq-list::-webkit-scrollbar { width: 6px; }
.tq-list::-webkit-scrollbar-thumb { background: rgba(0, 0, 0, 0.18); border-radius: 3px; }
.tq-list::-webkit-scrollbar-track { background: transparent; }
.tq-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 7px;
  background: #fff;
  border: 1px solid var(--line);
  font-size: 12.5px;
  flex-shrink: 0;            /* 在滚动容器里保持每行高度，不被压扁 */
}
.tq-item.current { box-shadow: 0 0 0 2px rgba(59, 130, 160, 0.35); }
.tq-item.running { border-color: rgba(59, 130, 160, 0.5); background: rgba(59, 130, 160, 0.06); }
.tq-item.done { border-color: rgba(16, 185, 129, 0.5); background: rgba(16, 185, 129, 0.06); }
.tq-item.error { border-color: rgba(239, 68, 68, 0.5); background: rgba(239, 68, 68, 0.06); }
.tq-item-status { width: 16px; flex: 0 0 auto; text-align: center; }
.tq-item.done .tq-item-status { color: #059669; }
.tq-item.error .tq-item-status { color: #dc2626; }
.tq-item-idx {
  min-width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.06);
  font-size: 11px;
  color: var(--muted);
  flex: 0 0 auto;
}
.tq-item-label {
  flex: 1;
  min-width: 0;             /* 允许收缩到比内容更窄，才能触发横向滚动 */
  color: var(--ink);
  white-space: nowrap;      /* 每个任务只占一行，不再换行撑高 */
  overflow-x: auto;         /* 文字过长时左右滚动查看 */
  overflow-y: hidden;
}
.tq-item-label::-webkit-scrollbar { height: 5px; }
.tq-item-label::-webkit-scrollbar-thumb { background: rgba(0, 0, 0, 0.18); border-radius: 3px; }
.tq-item-label::-webkit-scrollbar-track { background: transparent; }
.tq-item-ops { display: inline-flex; gap: 3px; flex: 0 0 auto; }
.tq-mini {
  font-size: 12px;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 5px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.8);
  color: var(--muted);
  cursor: pointer;
}
.tq-mini:hover:not(:disabled) { background: rgba(0, 0, 0, 0.06); color: var(--ink); }
.tq-mini:disabled { opacity: 0.4; cursor: not-allowed; }
.tq-del:hover:not(:disabled) { color: #dc2626; border-color: rgba(239, 68, 68, 0.5); background: rgba(239, 68, 68, 0.08); }
.tq-empty { font-size: 12px; color: var(--muted); padding: 6px 4px; line-height: 1.6; }
.tq-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.tq-actions button { font-size: 12px; padding: 4px 12px; }
.tq-actions .tq-run {
  background: linear-gradient(135deg, #3b82a0, #2a6f8e);
  border: none;
  color: #fff;
  font-weight: 600;
}
.tq-combo-save { display: flex; gap: 6px; align-items: center; }
.tq-combo-save-input {
  flex: 1;
  min-width: 0;
  padding: 5px 10px;
  border: 1px solid var(--accent);
  border-radius: 7px;
  font-size: 13px;
  outline: none;
  background: #fff;
  color: var(--ink);
}
.tq-combo-save button { font-size: 12px; padding: 4px 12px; }
.tq-combos {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
}
.tq-combos-label { font-size: 12px; color: var(--muted); }
.tq-combo-chip {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid rgba(77, 145, 90, 0.4);
  border-radius: 999px;
  overflow: hidden;
  background: rgba(77, 145, 90, 0.08);
}
.tq-combo-run {
  border: none;
  background: transparent;
  color: #2d7a3e;
  font-weight: 600;
  font-size: 12px;
  padding: 3px 10px;
  cursor: pointer;
}
.tq-combo-run:hover:not(:disabled) { background: rgba(77, 145, 90, 0.18); }
.tq-combo-load {
  border: none;
  border-left: 1px solid rgba(77, 145, 90, 0.3);
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  padding: 3px 8px;
  cursor: pointer;
}
.tq-combo-load:hover:not(:disabled) { background: rgba(0, 0, 0, 0.05); color: var(--ink); }
.tq-combo-chip .tq-del {
  border: none;
  border-left: 1px solid rgba(77, 145, 90, 0.3);
  border-radius: 0;
}
.tq-combo-chip button:disabled { opacity: 0.5; cursor: not-allowed; }

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
  border: 1px solid rgba(74, 53, 25, 0.12);
  border-radius: 999px;
  cursor: pointer;
  user-select: none;
  line-height: 1.3;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.recent-page-chip:hover {
  background: rgba(243, 223, 212, 0.85);
  border-color: rgba(182, 84, 52, 0.35);
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
  background: rgba(74, 53, 25, 0.18);
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
  box-shadow: 0 4px 16px rgba(182, 84, 52, 0.45);
  /* 固定状态是用户明确选择的「常驻可见」语义，保持不透明 */
  opacity: 1;
}
</style>
