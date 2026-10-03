<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue';

const props = defineProps({
  sourceItem: { type: Object, default: null }
});

const emit = defineEmits(['back']);

const canvasRef = ref(null);
const wrapRef = ref(null);
const presets = ref([]);
const isDragOver = ref(false);
const copyStatus = ref('');
// 有序多图复制（split 模式）的进度：null = 未开始；否则 { total, done, index }
// 「复制全部」会逐张写入剪贴板，每张之间等用户确认（跳转下一张按钮），
// 因为系统剪贴板一次只装得下一张图。
const copyProgress = ref(null);
// 「按份数等分」输入框的临时值（不落盘，避免污染 editorHabits）
const splitPartInput = ref(3);

const STORAGE_KEY_EDITOR_HABITS = 'editorHabits';
const editorHabits = (() => {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY_EDITOR_HABITS) || '{}'); }
  catch { return {}; }
})();

// 右侧控件浮层：固定（常驻不透明）/ 收起（隐藏让画布全幅），状态记忆到 editorHabits
const panelPinned = ref(editorHabits.panelPinned === true);
const panelCollapsed = ref(editorHabits.panelCollapsed === true);
watch([panelPinned, panelCollapsed], ([pinned, collapsed]) => {
  editorHabits.panelPinned = pinned === true;
  editorHabits.panelCollapsed = collapsed === true;
  try { localStorage.setItem(STORAGE_KEY_EDITOR_HABITS, JSON.stringify(editorHabits)); }
  catch { /* localStorage 异常时静默 */ }
});

const editor = reactive({
  image: null,
  imageSrc: '',
  imageName: '',
  zoom: 1,
  layers: [],
  selectedId: null,
  nextId: 1,
  mode: null,
  handle: null,
  start: null,
  current: null,
  draft: null,
  fillMode: ['mosaic', 'stripe', 'reveal', 'solid', 'image', 'split'].includes(editorHabits.fillMode) ? editorHabits.fillMode : 'mosaic',
  opacity: Number.isFinite(editorHabits.opacity) ? Math.max(0, Math.min(1, editorHabits.opacity)) : 1,
  mosaicBlockSize: Number.isFinite(editorHabits.mosaicBlockSize) ? editorHabits.mosaicBlockSize : 18,
  stripeText: '该信息已被管理员撤回',
  stripeFontFamily: typeof editorHabits.stripeFontFamily === 'string' && editorHabits.stripeFontFamily
    ? editorHabits.stripeFontFamily
    : 'Microsoft YaHei',
  stripeFontSize: Number.isFinite(editorHabits.stripeFontSize) && editorHabits.stripeFontSize > 0
    ? editorHabits.stripeFontSize
    : 26,
  stripeAutoFit: editorHabits.stripeAutoFit !== false,
  stripeOrientation: editorHabits.stripeOrientation === 'vertical' ? 'vertical' : 'horizontal',
  imageDataUrl: null,
  imageOverlayName: '',
  revealColor: typeof editorHabits.revealColor === 'string' ? editorHabits.revealColor : '#000000',
  revealOpacity: Number.isFinite(editorHabits.revealOpacity) ? Math.max(0, Math.min(1, editorHabits.revealOpacity)) : 0.8,
  revealBlur: Number.isFinite(editorHabits.revealBlur) && editorHabits.revealBlur >= 0 ? editorHabits.revealBlur : 0,
  solidColor: typeof editorHabits.solidColor === 'string' ? editorHabits.solidColor : '#000000',
  // 用户习惯：复制尺寸上限。0 表示原图无上限。watch 里会落盘。
  outputMaxEdge: Number.isFinite(editorHabits.outputMaxEdge) ? editorHabits.outputMaxEdge : 1600,
  sourceMeta: {
    artist: '',
    characters: '',
    postUrl: ''
  },
  // ── 切分图片（split 模式）────────────────────────────────────────────
  // 打码方式选 split 时，画横线不再生成码块，而是往 splitLines 里塞一条水平切线。
  // 线段以「图片坐标系的 y 值」记录（与 zoom 无关），渲染时再乘 zoom。
  splitLines: [],        // 归一化的切线 y 值数组（升序维护），单位=图片像素
  draggingLineIndex: -1, // 正在拖动的切线下标（-1 = 无）
  hoverLineIndex: -1     // 鼠标悬停的切线（用于加粗高亮，便于抓取）
});

watch(() => editor.outputMaxEdge, (v) => {
  // 0 / 负数都按"原图"处理；写盘的值统一规范化一下，避免下次进来加载到 NaN
  const normalized = Number.isFinite(v) && v > 0 ? Math.round(v) : 0;
  editorHabits.outputMaxEdge = normalized;
  try { localStorage.setItem(STORAGE_KEY_EDITOR_HABITS, JSON.stringify(editorHabits)); }
  catch { /* localStorage 异常时静默 */ }
});

watch(() => [editor.fillMode, editor.opacity, editor.stripeFontSize, editor.stripeFontFamily, editor.stripeOrientation, editor.stripeAutoFit, editor.mosaicBlockSize, editor.solidColor, editor.revealColor, editor.revealOpacity, editor.revealBlur], ([fillMode, opacity, size, family, orientation, autoFit, mosaicBlockSize, solidColor, revealColor, revealOpacity, revealBlur]) => {
  if (['mosaic', 'stripe', 'reveal', 'solid', 'image', 'split'].includes(fillMode)) editorHabits.fillMode = fillMode;
  if (Number.isFinite(opacity)) editorHabits.opacity = Math.max(0, Math.min(1, opacity));
  if (Number.isFinite(size) && size > 0) editorHabits.stripeFontSize = Math.round(size);
  if (typeof family === 'string' && family) editorHabits.stripeFontFamily = family;
  editorHabits.stripeOrientation = orientation === 'vertical' ? 'vertical' : 'horizontal';
  editorHabits.stripeAutoFit = autoFit !== false;
  if (Number.isFinite(mosaicBlockSize) && mosaicBlockSize > 0) editorHabits.mosaicBlockSize = Math.round(mosaicBlockSize);
  if (typeof solidColor === 'string' && solidColor) editorHabits.solidColor = solidColor;
  if (typeof revealColor === 'string' && revealColor) editorHabits.revealColor = revealColor;
  if (Number.isFinite(revealOpacity)) editorHabits.revealOpacity = Math.max(0, Math.min(1, revealOpacity));
  if (Number.isFinite(revealBlur) && revealBlur >= 0) editorHabits.revealBlur = revealBlur;
  try { localStorage.setItem(STORAGE_KEY_EDITOR_HABITS, JSON.stringify(editorHabits)); }
  catch { /* localStorage 异常时静默 */ }
});

function selectedLayer() {
  return editor.layers.find(item => item.id === editor.selectedId) || null;
}

// ── 切分图片：水平切线（split 模式）──────────────────────────────────────
// 不变量：editor.splitLines 始终是「已排序 + 去重 + 夹在 (0, height) 内」的 y 值数组。
// 排序保证导出的分段天然按从上到下的顺序（用户要求「顺序的多张图片」）。
const SPLIT_MIN_GAP = 4; // 两条切线至少间隔 4px，避免产生 0 高度的碎段

function sortSplitLines() {
  const h = editor.image?.height || 0;
  editor.splitLines = editor.splitLines
    .map(y => Math.round(y))
    .filter(y => y > 0 && y < h)
    .sort((a, b) => a - b)
    // 去重/推开过近的线：保留先出现的，后一条至少比前一条大 SPLIT_MIN_GAP
    .reduce((acc, y) => {
      const prev = acc[acc.length - 1];
      if (prev == null || y - prev >= SPLIT_MIN_GAP) acc.push(y);
      return acc;
    }, []);
}

// 在当前 split 布局上再加一条线：切在「最宽的那一段」的中点，避免新线挤在一起
function addSplitLine() {
  if (!editor.image) return;
  const h = editor.image.height;
  const bounds = [0, ...editor.splitLines, h];
  let bestStart = 0;
  let bestGap = -1;
  for (let i = 0; i < bounds.length - 1; i += 1) {
    const gap = bounds[i + 1] - bounds[i];
    if (gap > bestGap) { bestGap = gap; bestStart = bounds[i]; }
  }
  if (bestGap < SPLIT_MIN_GAP * 2) return; // 已经切得太碎，不再加
  const y = Math.round((bestStart + (bestStart + bestGap)) / 2);
  editor.splitLines.push(y);
  sortSplitLines();
  render();
}

function removeSplitLine(index) {
  if (index < 0 || index >= editor.splitLines.length) return;
  editor.splitLines.splice(index, 1);
  render();
}

function clearSplitLines() {
  editor.splitLines = [];
  render();
}

// 等比切 N 份（含 0 与 height 的等分点，去掉两端）
function splitIntoEqualParts(count) {
  if (!editor.image) return;
  const n = Math.max(2, Math.min(30, Math.round(count) || 2));
  const h = editor.image.height;
  const lines = [];
  for (let i = 1; i < n; i += 1) lines.push(Math.round((h * i) / n));
  editor.splitLines = lines;
  sortSplitLines();
  render();
}

// 按当前切线把图片切成 segments（y 区间列表），天然有序
const splitSegments = computed(() => {
  const h = editor.image?.height || 0;
  if (!h) return [];
  const bounds = [0, ...editor.splitLines, h];
  const out = [];
  for (let i = 0; i < bounds.length - 1; i += 1) {
    const top = bounds[i];
    const bottom = bounds[i + 1];
    if (bottom - top > 0) out.push({ index: out.length, y: top, height: bottom - top, bottom });
  }
  return out;
});

const splitSegmentCount = computed(() => splitSegments.value.length);

// 分段缩略图（data URL）。canvas 非响应式，所以单独缓存成 data URL 供面板渲染。
// 依赖 imageSrc / splitLines / layers / 各类打码参数，任一变化即重算。
const splitThumbs = ref([]);
let splitThumbToken = 0;

async function refreshSplitThumbs() {
  if (!editor.image || editor.fillMode !== 'split') {
    splitThumbs.value = [];
    return;
  }
  const token = ++splitThumbToken;
  const segs = splitSegments.value;
  if (!segs.length) { splitThumbs.value = []; return; }
  const full = await exportPng();
  if (token !== splitThumbToken) return; // 期间又变了，丢弃这次结果
  const scale = full.width / editor.image.width;
  const out = segs.map(seg => {
    const h = Math.max(1, Math.round(seg.height * scale));
    const c = document.createElement('canvas');
    c.width = full.width;
    c.height = h;
    c.getContext('2d').drawImage(full, 0, Math.round(seg.y * scale), full.width, h, 0, 0, full.width, h);
    return {
      index: seg.index,
      y: seg.y,
      height: seg.height,
      dataUrl: c.toDataURL('image/png')
    };
  });
  splitThumbs.value = out;
}

// 点到某条切线的命中判定（屏幕坐标），返回下标；容差随 zoom 放大便于抓取
function findSplitLineAt(point) {
  if (!editor.image || !editor.splitLines.length) return -1;
  const tolerance = Math.max(4, 7 / editor.zoom + 3);
  const y = point.y / editor.zoom;
  for (let i = 0; i < editor.splitLines.length; i += 1) {
    if (Math.abs(editor.splitLines[i] - y) <= tolerance) return i;
  }
  return -1;
}

function normalizeCharacters(value) {
  if (Array.isArray(value)) return value.filter(Boolean).join(', ');
  return String(value || '').split(' ').filter(Boolean).join(', ');
}

function normalizeLayer(layer) {
  if (layer.width < 0) { layer.x += layer.width; layer.width = Math.abs(layer.width); }
  if (layer.height < 0) { layer.y += layer.height; layer.height = Math.abs(layer.height); }
  layer.width = Math.max(1, layer.width);
  layer.height = Math.max(1, layer.height);
  if (!editor.image) return;
  layer.x = Math.max(0, Math.min(layer.x, editor.image.width - 1));
  layer.y = Math.max(0, Math.min(layer.y, editor.image.height - 1));
  layer.width = Math.min(layer.width, editor.image.width - layer.x);
  layer.height = Math.min(layer.height, editor.image.height - layer.y);
}

function applyControlsToLayer(layer) {
  // split 不是可写在图层上的填充方式；万一带到图层，回落到马赛克保证能画出东西
  layer.fillMode = editor.fillMode === 'split' ? 'mosaic' : editor.fillMode;
  layer.opacity = editor.opacity;
  layer.mosaicBlockSize = editor.mosaicBlockSize;
  layer.stripeText = editor.stripeText;
  layer.stripeFontFamily = editor.stripeFontFamily;
  layer.stripeFontSize = editor.stripeFontSize;
  layer.stripeAutoFit = editor.stripeAutoFit;
  layer.stripeOrientation = editor.stripeOrientation;
  layer.imageDataUrl = editor.imageDataUrl;
  layer.imageOverlayName = editor.imageOverlayName;
  layer.revealColor = editor.revealColor;
  layer.revealOpacity = editor.revealOpacity;
  layer.revealBlur = editor.revealBlur;
  layer.solidColor = editor.solidColor;
  normalizeLayer(layer);
}

function canvasPoint(event) {
  const rect = canvasRef.value.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function imagePoint(point) {
  return { x: point.x / editor.zoom, y: point.y / editor.zoom };
}

function pointInRect(px, py, rect) {
  return px >= rect.x && px <= rect.x + rect.width && py >= rect.y && py <= rect.y + rect.height;
}

function handles(layer, customSize = 12) {
  const z = editor.zoom;
  const cx = (layer.x + layer.width / 2) * z;
  const cy = (layer.y + layer.height / 2) * z;
  const w = layer.width * z;
  const h = layer.height * z;
  const rad = ((layer.rotation || 0) * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const s = customSize;
  const corners = {
    nw: [-w / 2, -h / 2],
    ne: [w / 2, -h / 2],
    sw: [-w / 2, h / 2],
    se: [w / 2, h / 2]
  };
  const out = {};
  for (const [key, [lx, ly]] of Object.entries(corners)) {
    const rx = lx * cos - ly * sin + cx;
    const ry = lx * sin + ly * cos + cy;
    out[key] = { x: rx - s / 2, y: ry - s / 2, s };
  }
  // rot 手柄：local (0, -h/2 - 20)，屏幕空间略大于四角方便点击
  const rH = 20;
  const rotLy = -h / 2 - rH;
  const rx = -rotLy * sin + cx;
  const ry = rotLy * cos + cy;
  out.rot = { x: rx - 8, y: ry - 8, s: 16 };
  return out;
}

function findHandle(point, layer, hitSize = 30) {
  const map = handles(layer, hitSize);
  for (const [name, handle] of Object.entries(map)) {
    if (point.x >= handle.x && point.x <= handle.x + handle.s && point.y >= handle.y && point.y <= handle.y + handle.s) {
      return name;
    }
  }
  return null;
}

function pointInRotatedRect(px, py, layer) {
  const z = editor.zoom;
  const cx = (layer.x + layer.width / 2) * z;
  const cy = (layer.y + layer.height / 2) * z;
  const w = layer.width * z;
  const h = layer.height * z;
  const rad = -((layer.rotation || 0) * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const dx = px - cx;
  const dy = py - cy;
  const lx = dx * cos - dy * sin;
  const ly = dx * sin + dy * cos;
  return Math.abs(lx) <= w / 2 && Math.abs(ly) <= h / 2;
}

function topLayerAt(point) {
  for (let i = editor.layers.length - 1; i >= 0; i -= 1) {
    const layer = editor.layers[i];
    if (pointInRotatedRect(point.x, point.y, layer)) return layer;
  }
  return null;
}

function syncFromLayer(layer) {
  if (!layer) return;
  // 'split' 是「切割方式」而非码块填充方式，绝不能从图层回写，
  // 否则选中一个码块会把面板悄悄切回 split 模式。
  if (layer.fillMode && layer.fillMode !== 'split') editor.fillMode = layer.fillMode;
  editor.opacity = layer.opacity;
  editor.mosaicBlockSize = layer.mosaicBlockSize || 18;
  editor.stripeText = layer.stripeText;
  editor.stripeFontFamily = layer.stripeFontFamily;
  editor.stripeFontSize = layer.stripeFontSize;
  editor.stripeAutoFit = layer.stripeAutoFit !== false;
  editor.stripeOrientation = layer.stripeOrientation;
  editor.imageDataUrl = layer.imageDataUrl || null;
  editor.imageOverlayName = layer.imageOverlayName || '';
  editor.revealColor = layer.revealColor || '#000000';
  editor.revealOpacity = layer.revealOpacity ?? 0.8;
  editor.revealBlur = layer.revealBlur ?? 0;
  editor.solidColor = layer.solidColor || '#000000';
  if (editor.imageDataUrl) preloadOverlay(editor.imageDataUrl).catch(() => {});
}

function selectLayer(id) {
  editor.selectedId = id;
  syncFromLayer(selectedLayer());
  render();
}

function addLayer(rect) {
  const layer = {
    id: editor.nextId++,
    x: rect.x,
    y: rect.y,
    width: rect.width,
    height: rect.height,
    rotation: 0,
    fillMode: editor.fillMode,
    opacity: editor.opacity,
    mosaicBlockSize: editor.mosaicBlockSize,
    stripeText: editor.stripeText,
    stripeFontFamily: editor.stripeFontFamily,
    stripeFontSize: editor.stripeFontSize,
    stripeAutoFit: editor.stripeAutoFit,
    stripeOrientation: editor.stripeOrientation,
    imageDataUrl: editor.imageDataUrl,
    imageOverlayName: editor.imageOverlayName,
    revealColor: editor.revealColor,
    revealOpacity: editor.revealOpacity,
    revealBlur: editor.revealBlur,
    solidColor: editor.solidColor
  };
  applyControlsToLayer(layer);
  editor.layers.push(layer);
  selectLayer(layer.id);
}

function drawMosaic(ctx, layer, scale) {
  if (!editor.image) return;
  const sourceBlock = Math.max(3, Number(layer.mosaicBlockSize) || 18);
  const sampleWidth = Math.max(1, Math.ceil(layer.width / sourceBlock));
  const sampleHeight = Math.max(1, Math.ceil(layer.height / sourceBlock));
  const pixelCanvas = document.createElement('canvas');
  pixelCanvas.width = sampleWidth;
  pixelCanvas.height = sampleHeight;
  const pixelCtx = pixelCanvas.getContext('2d');
  pixelCtx.imageSmoothingEnabled = true;
  pixelCtx.drawImage(
    editor.image,
    layer.x, layer.y, layer.width, layer.height,
    0, 0, sampleWidth, sampleHeight
  );
  ctx.save();
  ctx.globalAlpha = layer.opacity;
  const cx = (layer.x + layer.width / 2) * scale;
  const cy = (layer.y + layer.height / 2) * scale;
  const rad = ((layer.rotation || 0) * Math.PI) / 180;
  if (rad) {
    ctx.translate(cx, cy);
    ctx.rotate(rad);
    ctx.translate(-cx, -cy);
  }
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(
    pixelCanvas,
    0, 0, sampleWidth, sampleHeight,
    layer.x * scale, layer.y * scale, layer.width * scale, layer.height * scale
  );
  ctx.restore();
}

function fittedStripeFontSize(ctx, layer, scale) {
  const manualSize = Math.max(6, Number(layer.stripeFontSize) || 26) * scale;
  if (layer.stripeAutoFit === false) return manualSize;
  const text = String(layer.stripeText || '').trim() || ' ';
  const w = layer.width * scale;
  const h = layer.height * scale;
  if (layer.stripeOrientation === 'vertical') {
    const count = Math.max(1, Array.from(text).length);
    return Math.max(1 * scale, Math.min(w * 0.68, h / (count * 1.12)));
  }
  let low = 1 * scale;
  let high = Math.max(low, h * 0.72);
  for (let i = 0; i < 12; i += 1) {
    const mid = (low + high) / 2;
    ctx.font = `900 ${mid}px "${layer.stripeFontFamily}"`;
    if (ctx.measureText(text).width <= w * 0.88) low = mid;
    else high = mid;
  }
  return low;
}

function drawStripe(ctx, layer, scale) {
  const x = layer.x * scale;
  const y = layer.y * scale;
  const w = layer.width * scale;
  const h = layer.height * scale;
  const cx = (layer.x + layer.width / 2) * scale;
  const cy = (layer.y + layer.height / 2) * scale;
  const rad = ((layer.rotation || 0) * Math.PI) / 180;
  ctx.save();
  ctx.globalAlpha = layer.opacity;
  if (rad) {
    ctx.translate(cx, cy);
    ctx.rotate(rad);
    ctx.translate(-cx, -cy);
  }

  // Removed background box for watermark style

  ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
  const fontSize = fittedStripeFontSize(ctx, layer, scale);
  ctx.font = `900 ${fontSize}px "${layer.stripeFontFamily}"`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  ctx.shadowColor = 'rgba(0, 0, 0, 0.6)';
  ctx.shadowBlur = Math.max(2, fontSize * 0.12);
  ctx.shadowOffsetX = Math.max(1, fontSize * 0.05);
  ctx.shadowOffsetY = Math.max(1, fontSize * 0.05);

  ctx.strokeStyle = 'rgba(0, 0, 0, 0.6)';
  ctx.lineWidth = Math.max(1.5, fontSize * 0.1);
  ctx.lineJoin = 'round';

  if (layer.stripeOrientation === 'vertical') {
    const chars = Array.from(layer.stripeText || '');
    const line = fontSize * 1.1;
    const start = y + h / 2 - (chars.length * line) / 2 + line / 2;
    chars.forEach((char, index) => {
      ctx.strokeText(char, x + w / 2, start + index * line);
      ctx.fillText(char, x + w / 2, start + index * line);
    });
  } else {
    ctx.strokeText(layer.stripeText || '', x + w / 2, y + h / 2);
    ctx.fillText(layer.stripeText || '', x + w / 2, y + h / 2);
  }
  ctx.restore();
}

function drawSolid(ctx, layer, scale) {
  const x = layer.x * scale;
  const y = layer.y * scale;
  const w = layer.width * scale;
  const h = layer.height * scale;
  const cx = (layer.x + layer.width / 2) * scale;
  const cy = (layer.y + layer.height / 2) * scale;
  const rad = ((layer.rotation || 0) * Math.PI) / 180;
  ctx.save();
  ctx.globalAlpha = layer.opacity;
  if (rad) {
    ctx.translate(cx, cy);
    ctx.rotate(rad);
    ctx.translate(-cx, -cy);
  }
  ctx.fillStyle = layer.solidColor || '#000000';
  ctx.fillRect(x, y, w, h);
  ctx.restore();
}

function drawSelection(ctx, layer) {
  const z = editor.zoom;
  const x = layer.x * z;
  const y = layer.y * z;
  const w = layer.width * z;
  const h = layer.height * z;
  const cx = (layer.x + layer.width / 2) * z;
  const cy = (layer.y + layer.height / 2) * z;
  const rad = ((layer.rotation || 0) * Math.PI) / 180;

  // 1) 旋转后的描边矩形
  ctx.save();
  ctx.strokeStyle = '#1d7ef3';
  ctx.lineWidth = 2;
  if (rad) {
    ctx.translate(cx, cy);
    ctx.rotate(rad);
    ctx.translate(-cx, -cy);
  }
  ctx.strokeRect(x, y, w, h);
  ctx.restore();

  // 2) 4 个角句柄 + rot 圆形手柄（屏幕坐标，不受 ctx 旋转影响）
  ctx.save();
  ctx.fillStyle = '#1d7ef3';
  ctx.strokeStyle = '#1d7ef3';
  ctx.lineWidth = 2;
  const map = handles(layer);
  for (const [key, handle] of Object.entries(map)) {
    if (key === 'rot') {
      // 从顶部中点到 rot 手柄的引导线
      const topX = (layer.x + layer.width / 2) * z;
      const topY = layer.y * z;
      ctx.beginPath();
      ctx.moveTo(topX, topY);
      ctx.lineTo(handle.x + handle.s / 2, handle.y + handle.s / 2);
      ctx.stroke();
      // 圆形 rot 手柄
      ctx.beginPath();
      ctx.arc(handle.x + handle.s / 2, handle.y + handle.s / 2, handle.s / 2, 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.fillRect(handle.x, handle.y, handle.s, handle.s);
    }
  }
  ctx.restore();
}

async function drawImageLayer(ctx, layer, scale) {
  if (!layer.imageDataUrl) {
    drawMosaic(ctx, layer, scale);
    return;
  }
  let image = getOverlaySync(layer.imageDataUrl);
  if (!image) {
    drawMosaic(ctx, layer, scale);
    preloadOverlay(layer.imageDataUrl).then(() => render()).catch(() => {});
    return;
  }
  const x = layer.x * scale;
  const y = layer.y * scale;
  const w = layer.width * scale;
  const h = layer.height * scale;
  const cx = (layer.x + layer.width / 2) * scale;
  const cy = (layer.y + layer.height / 2) * scale;
  const rad = ((layer.rotation || 0) * Math.PI) / 180;
  const ratio = Math.min(w / image.width, h / image.height);
  const dw = image.width * ratio;
  const dh = image.height * ratio;
  ctx.save();
  ctx.globalAlpha = layer.opacity;
  if (rad) {
    ctx.translate(cx, cy);
    ctx.rotate(rad);
    ctx.translate(-cx, -cy);
  }
  ctx.drawImage(image, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
  ctx.restore();
}

function drawRevealMask(ctx, layers, scale, globalColor, globalOpacity, globalBlur = 0) {
  const revealLayers = layers.filter(layer => layer.fillMode === 'reveal');
  if (!revealLayers.length) return;

  const maskCanvas = document.createElement('canvas');
  maskCanvas.width = ctx.canvas.width;
  maskCanvas.height = ctx.canvas.height;
  const maskCtx = maskCanvas.getContext('2d');

  maskCtx.fillStyle = globalColor;
  maskCtx.globalAlpha = globalOpacity;
  maskCtx.fillRect(0, 0, maskCanvas.width, maskCanvas.height);

  maskCtx.globalCompositeOperation = 'destination-out';
  maskCtx.globalAlpha = 1;
  // 注意：blur 半径按 scale 缩放，使 exportPng (scale=1) 与画布预览 (scale=zoom) 视觉一致
  const blurPx = Math.max(0, Number(globalBlur) || 0) * scale;

  for (const layer of revealLayers) {
    const cx = (layer.x + layer.width / 2) * scale;
    const cy = (layer.y + layer.height / 2) * scale;
    const rad = ((layer.rotation || 0) * Math.PI) / 180;
    maskCtx.save();
    if (rad) {
      maskCtx.translate(cx, cy);
      maskCtx.rotate(rad);
      maskCtx.translate(-cx, -cy);
    }
    if (blurPx > 0) {
      // 在 destination-out 椭圆 fill 前应用 blur filter，让挖出的洞边缘有软过渡
      maskCtx.filter = `blur(${blurPx}px)`;
    }
    maskCtx.beginPath();
    maskCtx.ellipse(
      cx,
      cy,
      (layer.width / 2) * scale,
      (layer.height / 2) * scale,
      0,
      0,
      Math.PI * 2
    );
    maskCtx.fill();
    maskCtx.restore();
  }

  ctx.drawImage(maskCanvas, 0, 0);
}

// split 模式叠加层：交替明暗带区分分段 + 实线切线 + 左上角段序号
// 纯视觉辅助，不参与导出（导出走 exportSplitSegment，直接按 y 区间裁剪原图）
function drawSplitOverlay(ctx, scale) {
  if (!editor.image) return;
  const w = editor.image.width * scale;
  const segs = splitSegments.value;
  if (!segs.length) return;

  ctx.save();
  // 1) 交替明暗带：奇数段压一层淡蓝，让每一段的范围一目了然
  segs.forEach(seg => {
    if (seg.index % 2 === 1) {
      ctx.fillStyle = 'rgba(29, 126, 243, 0.10)';
      ctx.fillRect(0, seg.y * scale, w, seg.height * scale);
    }
  });
  // 2) 切线：悬停/拖动的线加粗高亮
  editor.splitLines.forEach((y, i) => {
    const active = i === editor.draggingLineIndex || i === editor.hoverLineIndex;
    ctx.strokeStyle = active ? '#ff8a3d' : '#1d7ef3';
    ctx.lineWidth = active ? 3 : 2;
    ctx.setLineDash(active ? [] : [10, 6]);
    ctx.beginPath();
    ctx.moveTo(0, y * scale + 0.5);
    ctx.lineTo(w, y * scale + 0.5);
    ctx.stroke();
  });
  ctx.setLineDash([]);
  // 3) 每段左上角标序号
  const fontSize = Math.max(11, Math.min(18, 12 / scale));
  ctx.font = `700 ${fontSize}px "Microsoft YaHei", sans-serif`;
  ctx.textBaseline = 'top';
  segs.forEach(seg => {
    const label = `${seg.index + 1}/${segs.length}`;
    const padX = 6;
    const boxH = fontSize * 1.5;
    const boxW = ctx.measureText(label).width + padX * 2;
    ctx.fillStyle = 'rgba(15, 20, 32, 0.72)';
    ctx.fillRect(6, seg.y * scale + 6, boxW, boxH);
    ctx.fillStyle = '#ffffff';
    ctx.fillText(label, 6 + padX, seg.y * scale + 6 + (boxH - fontSize) / 2);
  });
  ctx.restore();
}

async function render() {
  if (!canvasRef.value || !editor.image) return;
  const canvas = canvasRef.value;
  canvas.width = Math.max(1, Math.round(editor.image.width * editor.zoom));
  canvas.height = Math.max(1, Math.round(editor.image.height * editor.zoom));
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(editor.image, 0, 0, canvas.width, canvas.height);

  for (const layer of editor.layers) {
    if (layer.fillMode === 'stripe') drawStripe(ctx, layer, editor.zoom);
    else if (layer.fillMode === 'image') await drawImageLayer(ctx, layer, editor.zoom);
    else if (layer.fillMode === 'solid') drawSolid(ctx, layer, editor.zoom);
    else if (layer.fillMode === 'mosaic') {
      drawMosaic(ctx, layer, editor.zoom);
    }
  }

  drawRevealMask(ctx, editor.layers, editor.zoom, editor.revealColor, editor.revealOpacity, editor.revealBlur);

  // split 模式：在图片之上叠加「分段明暗带 + 切线 + 序号」
  if (editor.fillMode === 'split') drawSplitOverlay(ctx, editor.zoom);

  if (editor.fillMode === 'reveal' && editor.mode === 'draw' && editor.start && editor.current) {
    const dx = editor.current.x - editor.start.x;
    const dy = editor.current.y - editor.start.y;
    const x = dx >= 0 ? editor.start.x : editor.current.x;
    const y = dy >= 0 ? editor.start.y : editor.current.y;
    const w = Math.abs(dx);
    const h = Math.abs(dy);
    ctx.save();
    ctx.setLineDash([8, 6]);
    ctx.strokeStyle = '#1d7ef3';
    ctx.lineWidth = 2;
    ctx.strokeRect(x * editor.zoom, y * editor.zoom, w * editor.zoom, h * editor.zoom);
    ctx.restore();
  }

  if (editor.draft) {
    ctx.save();
    ctx.setLineDash([8, 6]);
    ctx.strokeStyle = '#1d7ef3';
    ctx.lineWidth = 2;
    ctx.strokeRect(editor.draft.x * editor.zoom, editor.draft.y * editor.zoom, editor.draft.width * editor.zoom, editor.draft.height * editor.zoom);
    ctx.restore();
  }

  const active = selectedLayer();
  if (active) drawSelection(ctx, active);
}

function fitToWindow() {
  if (!editor.image || !wrapRef.value) return;
  const bounds = wrapRef.value.getBoundingClientRect();
  editor.zoom = Math.min((bounds.width - 32) / editor.image.width, (bounds.height - 32) / editor.image.height, 1);
  render();
}

function actualSize() {
  if (!editor.image) return;
  editor.zoom = 1;
  render();
}

function zoomIn() {
  if (!editor.image) return;
  editor.zoom = Math.min(5, editor.zoom * 1.15);
  render();
}

function onWheel(event) {
  if (!event.ctrlKey || !editor.image) return;
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.1 : 0.9;
  editor.zoom = Math.min(8, Math.max(0.2, editor.zoom * factor));
  render();
}

function undoLast() {
  const removed = editor.layers.pop();
  if (removed?.id === editor.selectedId) editor.selectedId = null;
  render();
}

function deleteSelected() {
  editor.layers = editor.layers.filter(item => item.id !== editor.selectedId);
  editor.selectedId = null;
  render();
}

const selectionBox = computed(() => {
  const layer = editor.layers.find(item => item.id === editor.selectedId);
  if (!layer) return null;
  // 显式访问用到的字段，确保 Vue reactivity 跟踪到 layer.x/y/width/height/rotation 的变化
  // （find 只读了 item.id，会让 computed 漏掉其他字段，导致 × 按钮位置滞后）
  const lx = layer.x;
  const ly = layer.y;
  const lw = layer.width;
  const lh = layer.height;
  const lrot = layer.rotation || 0;
  const z = editor.zoom;
  const cx = (lx + lw / 2) * z;
  const cy = (ly + lh / 2) * z;
  const w = lw * z;
  const h = lh * z;
  const rad = (lrot * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [px, py] of [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]]) {
    const rx = px * cos - py * sin + cx;
    const ry = px * sin + py * cos + cy;
    if (rx < minX) minX = rx;
    if (ry < minY) minY = ry;
    if (rx > maxX) maxX = rx;
    if (ry > maxY) maxY = ry;
  }
  return { left: minX, top: minY, right: maxX, bottom: maxY };
});

function onEditorKeyDown(event) {
  const tag = event.target?.tagName?.toLowerCase();
  if (['input', 'textarea', 'select'].includes(tag)) return;
  if (event.target?.isContentEditable) return;

  if (event.ctrlKey || event.metaKey) {
    if (event.key === 'z' || event.key === 'Z') {
      event.preventDefault();
      undoLast();
    } else if (event.key === 'c' || event.key === 'C') {
      event.preventDefault();
      copyToClipboard();
    }
  } else if (event.key === 'Delete' || event.key === 'Backspace') {
    if (editor.selectedId != null) {
      event.preventDefault();
      deleteSelected();
    }
  } else if (editor.selectedId != null && (event.key === ']' || event.key === '[' || event.key === 'ArrowRight' || event.key === 'ArrowLeft')) {
    event.preventDefault();
    const layer = selectedLayer();
    if (!layer) return;
    const dir = (event.key === ']' || event.key === 'ArrowRight') ? 1 : -1;
    const step = event.shiftKey ? 1 : 15;
    layer.rotation = (((layer.rotation || 0) + dir * step) % 360 + 360) % 360;
    render();
  } else if (event.key === 'Escape' && editor.selectedId != null) {
    editor.selectedId = null;
    render();
  }
}

function clearAll() {
  editor.layers = [];
  editor.selectedId = null;
  render();
}

// 卸载整张图片，回到空拖拽区（区别于只清码块的 clearAll）
function clearImage() {
  editor.image = null;
  editor.imageSrc = '';
  editor.imageName = '';
  editor.layers = [];
  editor.selectedId = null;
  editor.nextId = 1;
  editor.splitLines = [];
  editor.draggingLineIndex = -1;
  editor.hoverLineIndex = -1;
  editor.sourceMeta.artist = '';
  editor.sourceMeta.characters = '';
  editor.sourceMeta.postUrl = '';
  copyStatus.value = '';
  copyProgress.value = null;
}

async function createImage(source) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = source;
  });
}

const overlayImageCache = new Map();

async function preloadOverlay(url) {
  if (!url) return null;
  const cached = overlayImageCache.get(url);
  if (cached instanceof HTMLImageElement) return cached;
  if (cached && typeof cached.then === 'function') return cached;
  const promise = createImage(url).then(img => {
    overlayImageCache.set(url, img);
    return img;
  }).catch(err => {
    overlayImageCache.delete(url);
    throw err;
  });
  overlayImageCache.set(url, promise);
  return promise;
}

function getOverlaySync(url) {
  const entry = overlayImageCache.get(url);
  return entry instanceof HTMLImageElement ? entry : null;
}

async function loadImageFromDataUrl(dataUrl, meta = {}) {
  if (!dataUrl) return;
  editor.image = await createImage(dataUrl);
  editor.imageSrc = dataUrl;
  editor.imageName = meta.filename || 'image.png';
  editor.sourceMeta.artist = meta.artist || '';
  editor.sourceMeta.characters = meta.characters || '';
  editor.sourceMeta.postUrl = meta.postUrl || meta.post_url || '';
  editor.layers = [];
  editor.selectedId = null;
  editor.nextId = 1;
  editor.splitLines = [];
  editor.draggingLineIndex = -1;
  editor.hoverLineIndex = -1;
  if (editor.sourceMeta.artist) editor.stripeText = editor.sourceMeta.artist;
  await nextTick();
  fitToWindow();
}

async function loadImageFromPath(filePath, meta = {}) {
  const dataUrl = await window.desktopAPI.file.toLocalUrl(filePath);
  await loadImageFromDataUrl(dataUrl, {
    ...meta,
    filename: meta.filename || filePath.split(/[\\/]/).pop() || 'image.png'
  });
}

async function chooseImage() {
  const filePath = await window.desktopAPI.dialog.selectImage();
  if (!filePath) return;
  await loadImageFromPath(filePath, { filename: filePath.split(/[\\/]/).pop() });
}

async function loadDroppedFile(file) {
  if (!file) return;
  const filePath = file.path;
  if (filePath) {
    await loadImageFromPath(filePath, { filename: file.name || filePath.split(/[\\/]/).pop() });
  }
}

async function chooseOverlay() {
  const filePath = await window.desktopAPI.dialog.selectImage();
  if (!filePath) return;
  editor.imageDataUrl = await window.desktopAPI.file.readDataUrl(filePath);
  editor.imageOverlayName = filePath.split(/[\\/]/).pop() || '';
  editor.fillMode = 'image';
  await preloadOverlay(editor.imageDataUrl);
  const layer = selectedLayer();
  if (layer) applyControlsToLayer(layer);
  render();
}

async function loadPresets() {
  const items = await window.desktopAPI.preset.list();
  presets.value = await Promise.all(items.map(async item => ({
    ...item,
    thumbUrl: await window.desktopAPI.file.toLocalUrl(item.path)
  })));
}

// 贴图展示只显示名称、去掉文件后缀（.png/.webp 等）；不改 item.name 本身，避免影响其它逻辑。
function presetLabel(name) {
  return String(name || '').replace(/\.[^./\\]+$/, '');
}

async function usePreset(item) {
  editor.imageDataUrl = await window.desktopAPI.file.toLocalUrl(item.path);
  editor.imageOverlayName = item.name;
  editor.fillMode = 'image';
  await preloadOverlay(editor.imageDataUrl);
  const layer = selectedLayer();
  if (layer) applyControlsToLayer(layer);
  render();
}

async function exportPng({ maxEdgeOverride } = {}) {
  const scale = 1;
  const canvas = document.createElement('canvas');
  canvas.width = editor.image.width;
  canvas.height = editor.image.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(editor.image, 0, 0);

  const overlayUrls = new Set();
  for (const layer of editor.layers) {
    if (layer.fillMode === 'image' && layer.imageDataUrl) overlayUrls.add(layer.imageDataUrl);
  }
  await Promise.all([...overlayUrls].map(u => preloadOverlay(u).catch(() => null)));

  for (const layer of editor.layers) {
    if (layer.fillMode === 'stripe') drawStripe(ctx, layer, scale);
    else if (layer.fillMode === 'image') await drawImageLayer(ctx, layer, scale);
    else if (layer.fillMode === 'solid') drawSolid(ctx, layer, scale);
    else if (layer.fillMode === 'mosaic') {
      drawMosaic(ctx, layer, scale);
    }
  }
  drawRevealMask(ctx, editor.layers, scale, editor.revealColor, editor.revealOpacity, editor.revealBlur);
  // maxEdgeOverride 优先：原图复制时传 0 即可跳过缩放
  const maxEdge = maxEdgeOverride !== undefined
    ? (Number(maxEdgeOverride) || 0)
    : (Number(editor.outputMaxEdge) || 0);
  if (maxEdge > 0) {
    const currentMax = Math.max(canvas.width, canvas.height);
    if (currentMax > maxEdge) {
      const ratio = maxEdge / currentMax;
      const resized = document.createElement('canvas');
      resized.width = Math.max(1, Math.round(canvas.width * ratio));
      resized.height = Math.max(1, Math.round(canvas.height * ratio));
      resized.getContext('2d').drawImage(canvas, 0, 0, resized.width, resized.height);
      return resized;
    }
  }
  return canvas;
}

async function copyToClipboard({ original = false } = {}) {
  if (!editor.image) return;
  // split 模式：走「有序多图」流程（系统剪贴板一次只能放一张）
  if (!original && editor.fillMode === 'split') {
    await startSplitCopySequence();
    return;
  }
  copyStatus.value = '';
  const canvas = await exportPng(original ? { maxEdgeOverride: 0 } : {});
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  if (!blob) {
    copyStatus.value = '复制失败';
    return;
  }
  const sizeLabel = `${canvas.width}×${canvas.height}`;
  try {
    if (navigator.clipboard && window.ClipboardItem) {
      if (!document.hasFocus()) window.focus();
      if (!document.hasFocus()) throw new Error('窗口未聚焦');
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      copyStatus.value = `已复制到剪贴板（${sizeLabel}${original ? ' · 原图' : ''}）`;
      return;
    }
  } catch {
    // Fallback to Electron clipboard below.
  }

  const bytes = new Uint8Array(await blob.arrayBuffer());
  const result = await window.desktopAPI.file.copyPng(bytes);
  copyStatus.value = result?.ok
    ? `已复制到剪贴板（${sizeLabel}${original ? ' · 原图' : ''}）`
    : `复制失败${result?.error ? `: ${result.error}` : ''}`;
}

function copyOriginalToClipboard() { return copyToClipboard({ original: true }); }

// ── 切分图片：导出与有序复制 ─────────────────────────────────────────────
// 把「打了码的整图」按 splitSegments 的 y 区间裁成多张。顺序 = 从上到下。
// 不走 exportPng（那条会按 outputMaxEdge 整体缩放），改为逐段裁剪后再统一缩放。
async function exportSplitCanvases({ maxEdgeOverride } = {}) {
  if (!editor.image) return [];
  const segs = splitSegments.value;
  if (!segs.length) return [];
  const full = await exportPng({ maxEdgeOverride }); // 复用整套绘制逻辑，拿到打完码的整图
  const scale = full.width / editor.image.width;    // 若触发了尺寸上限，这里是缩放比
  return segs.map(seg => {
    const canvas = document.createElement('canvas');
    const sx = 0;
    const sy = Math.round(seg.y * scale);
    const sh = Math.max(1, Math.round(seg.height * scale));
    canvas.width = full.width;
    canvas.height = sh;
    canvas.getContext('2d').drawImage(full, sx, sy, full.width, sh, 0, 0, full.width, sh);
    return canvas;
  });
}

async function canvasToBlob(canvas) {
  return new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
}

// 复用 copyToClipboard 的「写单张到系统剪贴板」能力
async function writeCanvasToClipboard(canvas) {
  const blob = await canvasToBlob(canvas);
  if (!blob) return { ok: false, error: '转码失败' };
  try {
    if (navigator.clipboard && window.ClipboardItem) {
      if (!document.hasFocus()) window.focus();
      if (!document.hasFocus()) throw new Error('窗口未聚焦');
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      return { ok: true };
    }
  } catch {
    // 落到 Electron 主进程兜底
  }
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const result = await window.desktopAPI.file.copyPng(bytes);
  return result?.ok ? { ok: true } : { ok: false, error: result?.error || '未知错误' };
}

// 进入「有序复制」流程：先复制第 1 张，其余由图库面板上的「复制下一张」推进。
// 系统剪贴板一次只装一张，所以必须一张一张来 —— 中间留出用户粘贴的时间。
async function startSplitCopySequence() {
  if (!editor.image) return;
  const canvases = await exportSplitCanvases();
  if (!canvases.length) {
    copyStatus.value = '没有可复制的分段，请先添加切分线';
    return;
  }
  copyStatus.value = '';
  const first = canvases[0];
  const result = await writeCanvasToClipboard(first);
  if (!result.ok) {
    copyStatus.value = `复制失败：${result.error}`;
    copyProgress.value = null;
    return;
  }
  copyProgress.value = { total: canvases.length, done: 1, index: 0 };
  copyStatus.value = `已复制第 1/${canvases.length} 张（${first.width}×${first.height}），粘贴后点「复制下一张」`;
}

async function copyNextSplitSegment() {
  const progress = copyProgress.value;
  if (!progress || !editor.image) return;
  const nextIndex = progress.index + 1;
  if (nextIndex >= progress.total) {
    copyProgress.value = null;
    copyStatus.value = `全部 ${progress.total} 张已依次复制完成`;
    return;
  }
  const canvases = await exportSplitCanvases();
  const canvas = canvases[nextIndex];
  if (!canvas) {
    copyProgress.value = null;
    copyStatus.value = '复制中断：分段已变化，请重新开始';
    return;
  }
  const result = await writeCanvasToClipboard(canvas);
  if (!result.ok) {
    copyStatus.value = `复制失败：${result.error}`;
    return;
  }
  copyProgress.value = { total: progress.total, done: nextIndex + 1, index: nextIndex };
  copyStatus.value = `已复制第 ${nextIndex + 1}/${progress.total} 张（${canvas.width}×${canvas.height}）`;
}

function cancelSplitCopySequence() {
  copyProgress.value = null;
  copyStatus.value = '已取消有序复制';
}

// 从当前进度重来
async function restartSplitCopySequence() {
  copyProgress.value = null;
  await startSplitCopySequence();
}

// 一次性导出为多个文件（用「选择图片」同一套 dialog 不合适，走目录选择）
async function exportSplitToFiles() {
  if (!editor.image) return;
  const canvases = await exportSplitCanvases({ maxEdgeOverride: 0 });
  if (!canvases.length) {
    copyStatus.value = '没有可导出的分段，请先添加切分线';
    return;
  }
  const dir = await window.desktopAPI.dialog.selectFolder();
  if (!dir) return;
  const baseName = String(editor.imageName || 'image').replace(/\.[^./\\]+$/, '') || 'image';
  let okCount = 0;
  const failures = [];
  for (let i = 0; i < canvases.length; i += 1) {
    const blob = await canvasToBlob(canvases[i]);
    if (!blob) { failures.push(i + 1); continue; }
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const filename = `${baseName}_${String(i + 1).padStart(2, '0')}.png`;
    const result = await window.desktopAPI.file.saveBytesToDir({ dir, filename, bytes });
    if (result?.ok) okCount += 1;
    else failures.push(i + 1);
  }
  copyStatus.value = failures.length
    ? `导出 ${okCount}/${canvases.length} 张，失败：第 ${failures.join('、')} 张`
    : `已导出 ${okCount} 张到 ${dir}`;
}

async function openSourceLink(event) {
  event.preventDefault();
  if (!editor.sourceMeta.postUrl) return;
  await window.desktopAPI.external.open(editor.sourceMeta.postUrl);
}

function useArtistText() {
  if (!editor.sourceMeta.artist) return;
  editor.stripeText = editor.sourceMeta.artist;
}

function useCharactersText() {
  const text = normalizeCharacters(editor.sourceMeta.characters);
  if (!text) return;
  editor.stripeText = text;
}

function usePostUrlText() {
  if (!editor.sourceMeta.postUrl) return;
  editor.stripeText = editor.sourceMeta.postUrl;
}

function onMouseDown(event) {
  if (!editor.image) return;
  const point = canvasPoint(event);

  // 0. split 模式：命中已有切线 → 拖动该线；点空白 → 新建一条线
  //    必须排在码块判定之前，因为 split 模式不产生码块，画布上只有切线。
  if (editor.fillMode === 'split') {
    const lineIndex = findSplitLineAt(point);
    if (lineIndex >= 0) {
      editor.draggingLineIndex = lineIndex;
      editor.mode = 'split-drag';
      if (canvasRef.value) canvasRef.value.style.cursor = 'grabbing';
      render();
      return;
    }
    const y = Math.round(Math.max(1, Math.min(editor.image.height - 1, point.y / editor.zoom)));
    editor.splitLines.push(y);
    sortSplitLines();
    editor.draggingLineIndex = editor.splitLines.indexOf(y);
    editor.mode = 'split-drag';
    render();
    return;
  }

  // 1. Check if user clicked a handle of the currently selected layer
  const active = selectedLayer();
  if (active) {
    const handle = findHandle(point, active);
    if (handle === 'rot') {
      editor.handle = 'rot';
      editor.mode = 'rotate';
      editor.start = imagePoint(point);
      const cx = active.x + active.width / 2;
      const cy = active.y + active.height / 2;
      editor.startAngle = Math.atan2(editor.start.y - cy, editor.start.x - cx) * 180 / Math.PI;
      editor.startRotation = active.rotation || 0;
      if (canvasRef.value) canvasRef.value.style.cursor = 'grabbing';
      return;
    }
    if (handle) {
      editor.handle = handle;
      editor.mode = 'resize';
      editor.start = imagePoint(point);
      return;
    }
  }

  // 2. Check if user clicked inside ANY layer
  const hit = topLayerAt(point);
  if (hit) {
    if (editor.selectedId !== hit.id) selectLayer(hit.id);
    editor.handle = null;
    // 关键修复：mousedown 命中码块时不要立即进入 move 模式。
    // 只记录 pendingMove + 起始位置，等 mousemove 超过 3px 阈值才正式激活。
    // 这样 click + 微小移动不会让 layer.x/y 变化，避免 selectionBox 跟随后 × 按钮"跳动"。
    editor.pendingMove = true;
    editor.moveStart = imagePoint(point);
    editor.start = imagePoint(point);
    return;
  }
  editor.selectedId = null;
  editor.mode = 'draw';
  editor.start = imagePoint(point);
  if (editor.fillMode === 'reveal') {
    editor.current = { ...editor.start };
    editor.draft = null;
  } else {
    editor.draft = { x: editor.start.x, y: editor.start.y, width: 0, height: 0 };
  }
  render();
}

function onMouseMove(event) {
  if (!editor.image) return;
  const point = imagePoint(canvasPoint(event));
  // split 模式：拖动切线 / 悬停高亮（都不涉及码块逻辑，直接返回）
  if (editor.fillMode === 'split') {
    if (editor.mode === 'split-drag' && editor.draggingLineIndex >= 0) {
      const idx = editor.draggingLineIndex;
      const y = Math.max(1, Math.min(editor.image.height - 1, point.y));
      editor.splitLines[idx] = Math.round(y);
      sortSplitLines();
      // 排序后线序可能变化，重新定位被拖动的这条（按 y 值匹配）
      editor.draggingLineIndex = editor.splitLines.indexOf(Math.round(y));
      render();
      return;
    }
    const hover = findSplitLineAt(canvasPoint(event));
    if (hover !== editor.hoverLineIndex) {
      editor.hoverLineIndex = hover;
      if (canvasRef.value) canvasRef.value.style.cursor = hover >= 0 ? 'row-resize' : 'crosshair';
      render();
    }
    return;
  }
  // pendingMove 激活：mousedown 命中码块后，鼠标移动超过 3px 才正式进入 move 模式
  if (editor.pendingMove && !editor.mode) {
    const dx = point.x - editor.moveStart.x;
    const dy = point.y - editor.moveStart.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      editor.mode = 'move';
      editor.start = { ...editor.moveStart };
    } else {
      return;  // 还没跨过阈值，不做任何事
    }
  }
  if (!editor.mode) return;
  if (editor.mode === 'draw' && editor.fillMode === 'reveal') {
    editor.current = point;
    render();
    return;
  }
  if (editor.mode === 'draw' && editor.draft) {
    editor.draft.width = point.x - editor.start.x;
    editor.draft.height = point.y - editor.start.y;
    const draft = { ...editor.draft };
    normalizeLayer(draft);
    editor.draft = draft;
    render();
    return;
  }
  const layer = selectedLayer();
  if (!layer) return;
  if (editor.mode === 'move') {
    layer.x += point.x - editor.start.x;
    layer.y += point.y - editor.start.y;
    editor.start = point;
    normalizeLayer(layer);
    render();
    return;
  }
  if (editor.mode === 'resize') {
    const dx = point.x - editor.start.x;
    const dy = point.y - editor.start.y;
    if (editor.handle.includes('n')) { layer.y += dy; layer.height -= dy; }
    if (editor.handle.includes('s')) layer.height += dy;
    if (editor.handle.includes('w')) { layer.x += dx; layer.width -= dx; }
    if (editor.handle.includes('e')) layer.width += dx;
    editor.start = point;
    normalizeLayer(layer);
    render();
    return;
  }
  if (editor.mode === 'rotate') {
    if (!layer) return;
    const cx = layer.x + layer.width / 2;
    const cy = layer.y + layer.height / 2;
    const curAngle = Math.atan2(point.y - cy, point.x - cx) * 180 / Math.PI;
    const delta = curAngle - (editor.startAngle || 0);
    let rot = (editor.startRotation || 0) + delta;
    if (!event.shiftKey) {
      // 优先吸附到 0/45/90/.../315（±5° 内），否则按 15° 步进
      const snap = Math.round(rot / 45) * 45;
      if (Math.abs(rot - snap) < 5) rot = snap;
      else rot = Math.round(rot / 15) * 15;
    }
    layer.rotation = ((rot % 360) + 360) % 360;
    render();
  }
}

function onMouseUp(event) {
  // 撤销"click + 微小拖动"：如果 mousedown 到 mouseup 总位移 < 5px，
  // 视为纯 click，撤销整段 move，保证 × 按钮位置不会因为手抖而跳动。
  // 真正的拖动（总位移 ≥ 5px）不受影响。
  if (editor.mode === 'move' && editor.moveStart && event) {
    const movedLayer = selectedLayer();
    if (movedLayer) {
      const upPoint = imagePoint(canvasPoint(event));
      const totalDx = upPoint.x - editor.moveStart.x;
      const totalDy = upPoint.y - editor.moveStart.y;
      if (Math.abs(totalDx) < 5 && Math.abs(totalDy) < 5) {
        movedLayer.x -= totalDx;
        movedLayer.y -= totalDy;
      }
    }
  }

  if (editor.mode === 'draw') {
    if (editor.fillMode === 'reveal' && editor.start && editor.current) {
      const dx = editor.current.x - editor.start.x;
      const dy = editor.current.y - editor.start.y;
      const width = Math.abs(dx);
      const height = Math.abs(dy);
      if (width > 10 && height > 10) {
        const rect = {
          x: dx >= 0 ? editor.start.x : editor.current.x,
          y: dy >= 0 ? editor.start.y : editor.current.y,
          width,
          height
        };
        normalizeLayer(rect);
        addLayer(rect);
      }
    } else if (editor.draft && editor.draft.width > 10 && editor.draft.height > 10) {
      addLayer({ ...editor.draft });
    }
  }
  editor.mode = null;
  editor.handle = null;
  editor.start = null;
  editor.current = null;
  editor.draft = null;
  editor.pendingMove = false;
  editor.moveStart = null;
  editor.startAngle = 0;
  editor.startRotation = 0;
  editor.draggingLineIndex = -1;
  if (canvasRef.value) {
    canvasRef.value.style.cursor = editor.fillMode === 'split' ? 'crosshair' : 'default';
  }
  render();
}

watch(() => props.sourceItem, async (item) => {
  if (!item?.localPath) return;
  await loadImageFromPath(item.localPath, item);
}, { immediate: true });

// 切换打码方式时，强制清空选中码块，避免误操作
// （之前会让右侧控件值直接覆盖到选中码块上，用户切换 fillMode 通常是想画新码块）
watch(() => editor.fillMode, (newVal, oldVal) => {
  if (newVal === oldVal) return;
  if (editor.selectedId != null) {
    editor.selectedId = null;
  }
  editor.draggingLineIndex = -1;
  editor.hoverLineIndex = -1;
  if (canvasRef.value) {
    canvasRef.value.style.cursor = newVal === 'split' ? 'crosshair' : 'default';
  }
  render();
  if (newVal === 'split') scheduleSplitThumbRefresh();
  else { splitThumbs.value = []; copyProgress.value = null; }
});

watch(() => [
  editor.opacity,
  editor.mosaicBlockSize,
  editor.stripeText,
  editor.stripeFontFamily,
  editor.stripeFontSize,
  editor.stripeAutoFit,
  editor.stripeOrientation,
  editor.revealColor,
  editor.revealOpacity,
  editor.revealBlur,
  editor.solidColor
], () => {
  const layer = selectedLayer();
  if (layer) applyControlsToLayer(layer);
  render();
  scheduleSplitThumbRefresh();
});

// split 面板的分段缩略图需要重算的场景：切线变化 / 图片变化 / 打码参数变化 / 图层变化
// 用 120ms 防抖，避免拖动切线时每帧都重算整图。
let splitThumbTimer = null;
function scheduleSplitThumbRefresh() {
  if (splitThumbTimer) clearTimeout(splitThumbTimer);
  splitThumbTimer = setTimeout(() => {
    splitThumbTimer = null;
    refreshSplitThumbs();
  }, 120);
}

watch(() => editor.splitLines.slice(), () => {
  if (editor.fillMode === 'split') scheduleSplitThumbRefresh();
});

watch(() => [editor.imageSrc, editor.layers.length], () => {
  if (editor.fillMode === 'split') scheduleSplitThumbRefresh();
});

async function onResize() {
  if (editor.image) fitToWindow();
}

function onDragOver(event) {
  event.preventDefault();
  isDragOver.value = true;
}

function onDragLeave() {
  isDragOver.value = false;
}

async function onDrop(event) {
  event.preventDefault();
  isDragOver.value = false;
  const [file] = Array.from(event.dataTransfer?.files || []);
  await loadDroppedFile(file);
}

async function onPaste(event) {
  const items = Array.from(event.clipboardData?.items || []);
  const imageItem = items.find(item => item.type.startsWith('image/'));
  if (!imageItem) return;
  event.preventDefault();
  const file = imageItem.getAsFile();
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    await loadImageFromDataUrl(reader.result, { filename: file.name || 'clipboard-image.png' });
  };
  reader.readAsDataURL(file);
}

onMounted(async () => {
  await loadPresets();
  window.addEventListener('mousemove', onMouseMove);
  window.addEventListener('mouseup', onMouseUp);
  window.addEventListener('resize', onResize);
  window.addEventListener('paste', onPaste);
  window.addEventListener('keydown', onEditorKeyDown);
});

onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onMouseMove);
  window.removeEventListener('mouseup', onMouseUp);
  window.removeEventListener('resize', onResize);
  window.removeEventListener('paste', onPaste);
  window.removeEventListener('keydown', onEditorKeyDown);
});
</script>

<template>
  <div class="editor-layout">
    <aside v-show="!panelCollapsed" class="panel card editor-side" :class="{ 'is-pinned': panelPinned }">
      <div class="panel-head">
        <h2>打码编辑</h2>
        <button class="secondary" @click="emit('back')">返回图库</button>
      </div>

      <div class="button-row compact editor-panel-chrome">
        <button
          class="ghost"
          :class="{ 'is-active': panelPinned }"
          @click="panelPinned = !panelPinned"
          :title="panelPinned ? '已固定（常驻不透明），点击取消固定' : '固定面板（默认半透明，悬浮变清晰）'"
        >{{ panelPinned ? '已固定' : '固定' }}</button>
        <button class="ghost" @click="panelCollapsed = true" title="收起面板，让画布全幅显示">收起 ›</button>
      </div>

      <div class="button-row">
        <button @click="chooseImage">选择图片</button>
        <button class="secondary" @click="chooseOverlay">选择贴图</button>
      </div>

      <section class="meta-card">
        <h3>来源信息</h3>
        <div class="meta-item">
          <span>作者</span>
          <strong class="truncate-text" :title="editor.sourceMeta.artist || '未提供'">{{ editor.sourceMeta.artist || '未提供' }}</strong>
          <button class="ghost" :disabled="!editor.sourceMeta.artist" @click="useArtistText">填入文字</button>
        </div>
        <div class="meta-item">
          <span>角色</span>
          <strong class="truncate-text" :title="normalizeCharacters(editor.sourceMeta.characters) || '未提供'">{{ normalizeCharacters(editor.sourceMeta.characters) || '未提供' }}</strong>
          <button class="ghost" :disabled="!editor.sourceMeta.characters" @click="useCharactersText">填入文字</button>
        </div>
        <div class="meta-item">
          <span>原帖</span>
          <a
            class="link-button secondary"
            :class="{ disabled: !editor.sourceMeta.postUrl }"
            :href="editor.sourceMeta.postUrl || '#'"
            @click="openSourceLink"
          >
            链接
          </a>
          <button class="ghost" :disabled="!editor.sourceMeta.postUrl" @click="usePostUrlText">填入文字</button>
        </div>
      </section>

      <div class="editor-workflow-hint" :class="{ active: editor.selectedId != null }">
        <strong>{{ editor.selectedId != null ? `正在编辑第 ${editor.selectedId} 个码块` : '拖拽图片空白处创建码块' }}</strong>
        <span>拖动码块可移动，拖动四角可缩放，拖动上方圆点可旋转；按 ] 或 → 顺时针 15°（Shift=1°），按 [ 或 ← 逆时针 15°（Shift=1°），自动吸附 0/45/90°；Delete 删除，Esc 取消选中，Ctrl+Z 撤销。</span>
      </div>

      <label class="field-full">
        <span>打码方式</span>
        <select v-model="editor.fillMode">
          <option value="mosaic">真实像素马赛克</option>
          <option value="stripe">自适应文本水印</option>
          <option value="image">贴图填充</option>
          <option value="solid">纯色条（黑/白条）</option>
          <option value="reveal">显示遮罩</option>
          <option value="split">切分图片（画横线分割）</option>
        </select>
      </label>

      <label v-if="editor.fillMode !== 'split'" class="field-full">
        <span>透明度 {{ Math.round(editor.opacity * 100) }}%</span>
        <input v-model.number="editor.opacity" type="range" min="0" max="1" step="0.05" />
      </label>

      <label v-if="editor.fillMode === 'mosaic'" class="field-full">
        <span>马赛克强度 {{ editor.mosaicBlockSize }}px</span>
        <input v-model.number="editor.mosaicBlockSize" type="range" min="6" max="64" step="2" />
      </label>

      <template v-if="editor.fillMode === 'stripe'">
        <label class="field-full">
          <span>文字</span>
          <input v-model="editor.stripeText" type="text" />
        </label>
        <div class="field-grid">
          <label>
            <span>字体</span>
            <select v-model="editor.stripeFontFamily">
              <option>Arial</option>
              <option>Times New Roman</option>
              <option>Courier New</option>
              <option>Verdana</option>
              <option>Microsoft YaHei</option>
              <option>SimHei</option>
              <option>SimSun</option>
            </select>
          </label>
          <label>
            <span>方向</span>
            <select v-model="editor.stripeOrientation">
              <option value="horizontal">水平</option>
              <option value="vertical">垂直</option>
            </select>
          </label>
        </div>
        <label class="editor-check-row">
          <input v-model="editor.stripeAutoFit" type="checkbox" />
          <span>字号随码块大小和文字长度自动适配</span>
        </label>
        <label class="field-full">
          <span>{{ editor.stripeAutoFit ? '手动字号（关闭自动适配后生效）' : '字号' }}</span>
          <input v-model.number="editor.stripeFontSize" type="number" min="8" max="240" :disabled="editor.stripeAutoFit" />
        </label>
      </template>

      <template v-if="editor.fillMode === 'image'">
        <label class="field-full">
          <span>当前贴图</span>
          <input :value="editor.imageOverlayName || '未选择贴图'" type="text" readonly />
        </label>
        <div class="preset-grid">
          <button v-for="item in presets" :key="item.path" class="preset-btn" @click="usePreset(item)">
            <img :src="item.thumbUrl" :alt="item.name" />
            <span>{{ presetLabel(item.name) }}</span>
          </button>
        </div>
      </template>

      <template v-if="editor.fillMode === 'solid'">
        <label class="field-full">
          <span>填充颜色</span>
          <input v-model="editor.solidColor" type="color" />
        </label>
        <div class="button-row compact">
          <button class="ghost" @click="editor.solidColor = '#000000'">黑条</button>
          <button class="ghost" @click="editor.solidColor = '#ffffff'">白条</button>
        </div>
      </template>

      <template v-if="editor.fillMode === 'split'">
        <div class="editor-workflow-hint split-hint">
          <strong>画横线切分图片</strong>
          <span>在图片上点一下即新增一条水平切线；拖动切线可调整位置；点住已有切线拖动。当前共 {{ splitSegmentCount }} 段，将按从上到下的顺序输出。</span>
        </div>

        <div class="button-row compact">
          <button class="secondary" @click="addSplitLine" :disabled="!editor.image">添加切线</button>
          <button class="ghost" @click="clearSplitLines" :disabled="!editor.splitLines.length">清空切线</button>
        </div>

        <div class="field-grid">
          <label>
            <span>等分份数</span>
            <input v-model.number="splitPartInput" type="number" min="2" max="30" step="1" />
          </label>
          <label class="split-apply-label">
            <span>&nbsp;</span>
            <button class="secondary" @click="splitIntoEqualParts(splitPartInput)" :disabled="!editor.image">按份数等分</button>
          </label>
        </div>

        <div class="split-line-list" v-if="editor.splitLines.length">
          <div v-for="(y, i) in editor.splitLines" :key="`${i}-${y}`" class="split-line-row">
            <span class="split-line-idx">#{{ i + 1 }}</span>
            <span class="split-line-pos">y = {{ y }} px</span>
            <button class="ghost split-line-del" @click="removeSplitLine(i)" :title="`删除第 ${i + 1} 条切线`">删除</button>
          </div>
        </div>
        <p v-else class="inline-note">还没有切线，图片目前是完整一张。</p>

        <div class="split-preview-head">
          <span>切分预览（{{ splitThumbs.length }} 张）</span>
        </div>
        <div class="split-thumb-grid">
          <figure v-for="seg in splitThumbs" :key="seg.index" class="split-thumb">
            <div class="split-thumb-imgwrap">
              <img :src="seg.dataUrl" :alt="`第 ${seg.index + 1} 段`" />
              <span class="split-thumb-badge">{{ seg.index + 1 }}</span>
            </div>
            <figcaption>{{ editor.image?.width || 0 }}×{{ seg.height }}px</figcaption>
          </figure>
        </div>
      </template>

      <template v-if="editor.fillMode === 'reveal'">
        <label class="field-full">
          <span>遮罩颜色</span>
          <input v-model="editor.revealColor" type="color" />
        </label>
        <label class="field-full">
          <span>遮罩透明度 {{ Math.round(editor.revealOpacity * 100) }}%</span>
          <input v-model.number="editor.revealOpacity" type="range" min="0.1" max="1" step="0.05" />
        </label>
        <label class="field-full">
          <span>边缘模糊 {{ editor.revealBlur }}px{{ editor.revealBlur === 0 ? '（锐利）' : '' }}</span>
          <input v-model.number="editor.revealBlur" type="range" min="0" max="100" step="1" />
        </label>
      </template>

      <div class="button-row compact">
        <button class="secondary" @click="fitToWindow">适应窗口</button>
        <button class="secondary" @click="actualSize">实际大小</button>
        <button class="secondary" @click="zoomIn">放大</button>
      </div>

      <div class="button-row compact">
        <button class="secondary" @click="undoLast" :disabled="!editor.layers.length">撤销</button>
        <button class="ghost" @click="deleteSelected" :disabled="!editor.selectedId">删除选中</button>
        <button class="ghost" @click="clearAll" :disabled="!editor.layers.length">清空码块</button>
      </div>
      <div class="button-row compact">
        <button class="ghost editor-clear-image" @click="clearImage" :disabled="!editor.image">清空图片</button>
      </div>

      <label class="field-full">
        <span>复制尺寸上限（像素，0 = 不限 / 原图大小，自动记忆）</span>
        <input v-model.number="editor.outputMaxEdge" type="number" min="0" step="100" />
      </label>

      <div class="button-row compact">
        <button
          @click="copyToClipboard()"
          :disabled="!editor.image || (editor.fillMode === 'split' && !splitSegmentCount)"
          style="flex: 1;"
        >{{ editor.fillMode === 'split' ? '有序复制全部分段' : '复制到剪贴板' }}</button>
        <button
          class="secondary"
          @click="copyOriginalToClipboard"
          :disabled="!editor.image"
          title="忽略尺寸上限，按原图分辨率复制"
        >复制原图</button>
      </div>

      <template v-if="editor.fillMode === 'split'">
        <div class="button-row compact">
          <button class="secondary" @click="exportSplitToFiles" :disabled="!editor.image || !splitSegmentCount">导出为多张文件</button>
        </div>
        <div v-if="copyProgress" class="split-copy-progress">
          <div class="split-copy-bar">
            <span class="split-copy-text">有序复制进度 {{ copyProgress.done }} / {{ copyProgress.total }}</span>
            <button
              v-if="copyProgress.done < copyProgress.total"
              class="split-copy-next"
              @click="copyNextSplitSegment"
            >复制下一张 ›</button>
            <button v-else class="split-copy-done" @click="cancelSplitCopySequence">完成</button>
          </div>
          <div class="split-copy-actions">
            <button class="ghost" @click="restartSplitCopySequence">重头再来</button>
            <button class="ghost" @click="cancelSplitCopySequence">结束</button>
          </div>
        </div>
      </template>

      <p v-if="copyStatus" class="inline-note">{{ copyStatus }}</p>
    </aside>

    <section class="panel card gallery-panel">
      <div class="canvas-head canvas-head-compact">
        <div class="stats">
          <span>{{ editor.image ? `${editor.image.width} x ${editor.image.height}` : '无图片' }}</span>
          <span>{{ Math.round(editor.zoom * 100) }}%</span>
          <span>{{ editor.layers.length }} 层</span>
        </div>
      </div>
      <div
        ref="wrapRef"
        class="editor-wrap"
        :class="{ 'is-dragover': isDragOver }"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
        @wheel="onWheel"
      >
        <div v-if="!editor.image" class="empty-editor">
          <p>从图库进入，手动选择图片，或把图片拖到这里开始编辑。</p>
        </div>
        <div v-else class="canvas-stage">
          <canvas ref="canvasRef" class="editor-canvas" @mousedown="onMouseDown" />
          <button
            v-if="selectionBox"
            class="layer-delete-btn"
            :style="{ left: selectionBox.right + 'px', top: selectionBox.top + 'px' }"
            @click="deleteSelected"
            title="删除当前码块 (Delete)"
          >×</button>
        </div>
      </div>
    </section>

    <button
      v-show="panelCollapsed"
      class="editor-reopen-btn"
      @click="panelCollapsed = false"
      title="展开工具面板"
    >🎚 工具</button>
  </div>
</template>
