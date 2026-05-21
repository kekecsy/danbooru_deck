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

const STORAGE_KEY_EDITOR_HABITS = 'editorHabits';
const editorHabits = (() => {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY_EDITOR_HABITS) || '{}'); }
  catch { return {}; }
})();

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
  fillMode: 'mosaic',
  opacity: 1,
  stripeText: '该信息已被管理员撤回',
  stripeFontFamily: 'Microsoft YaHei',
  stripeFontSize: 26,
  stripeOrientation: 'horizontal',
  imageDataUrl: null,
  imageOverlayName: '',
  revealColor: '#000000',
  revealOpacity: 0.8,
  // 用户习惯：复制尺寸上限。0 表示原图无上限。watch 里会落盘。
  outputMaxEdge: Number.isFinite(editorHabits.outputMaxEdge) ? editorHabits.outputMaxEdge : 1600,
  sourceMeta: {
    artist: '',
    characters: '',
    postUrl: ''
  }
});

watch(() => editor.outputMaxEdge, (v) => {
  // 0 / 负数都按"原图"处理；写盘的值统一规范化一下，避免下次进来加载到 NaN
  const normalized = Number.isFinite(v) && v > 0 ? Math.round(v) : 0;
  editorHabits.outputMaxEdge = normalized;
  try { localStorage.setItem(STORAGE_KEY_EDITOR_HABITS, JSON.stringify(editorHabits)); }
  catch { /* localStorage 异常时静默 */ }
});

function selectedLayer() {
  return editor.layers.find(item => item.id === editor.selectedId) || null;
}

function normalizeCharacters(value) {
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
  layer.fillMode = editor.fillMode;
  layer.opacity = editor.opacity;
  layer.stripeText = editor.stripeText;
  layer.stripeFontFamily = editor.stripeFontFamily;
  layer.stripeFontSize = editor.stripeFontSize;
  layer.stripeOrientation = editor.stripeOrientation;
  layer.imageDataUrl = editor.imageDataUrl;
  layer.imageOverlayName = editor.imageOverlayName;
  layer.revealColor = editor.revealColor;
  layer.revealOpacity = editor.revealOpacity;
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
  const x = layer.x * editor.zoom;
  const y = layer.y * editor.zoom;
  const w = layer.width * editor.zoom;
  const h = layer.height * editor.zoom;
  const s = customSize;
  return {
    nw: { x: x - s / 2, y: y - s / 2, s },
    ne: { x: x + w - s / 2, y: y - s / 2, s },
    sw: { x: x - s / 2, y: y + h - s / 2, s },
    se: { x: x + w - s / 2, y: y + h - s / 2, s }
  };
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

function topLayerAt(point) {
  for (let i = editor.layers.length - 1; i >= 0; i -= 1) {
    const layer = editor.layers[i];
    const rect = {
      x: layer.x * editor.zoom,
      y: layer.y * editor.zoom,
      width: layer.width * editor.zoom,
      height: layer.height * editor.zoom
    };
    if (pointInRect(point.x, point.y, rect)) return layer;
  }
  return null;
}

function syncFromLayer(layer) {
  if (!layer) return;
  editor.fillMode = layer.fillMode;
  editor.opacity = layer.opacity;
  editor.stripeText = layer.stripeText;
  editor.stripeFontFamily = layer.stripeFontFamily;
  editor.stripeFontSize = layer.stripeFontSize;
  editor.stripeOrientation = layer.stripeOrientation;
  editor.imageDataUrl = layer.imageDataUrl || null;
  editor.imageOverlayName = layer.imageOverlayName || '';
  editor.revealColor = layer.revealColor || '#000000';
  editor.revealOpacity = layer.revealOpacity ?? 0.8;
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
    fillMode: editor.fillMode,
    opacity: editor.opacity,
    stripeText: editor.stripeText,
    stripeFontFamily: editor.stripeFontFamily,
    stripeFontSize: editor.stripeFontSize,
    stripeOrientation: editor.stripeOrientation,
    imageDataUrl: editor.imageDataUrl,
    imageOverlayName: editor.imageOverlayName,
    revealColor: editor.revealColor,
    revealOpacity: editor.revealOpacity
  };
  applyControlsToLayer(layer);
  editor.layers.push(layer);
  selectLayer(layer.id);
}

function drawMosaic(ctx, layer, scale) {
  const block = Math.max(3, 15 * scale);
  ctx.save();
  ctx.globalAlpha = layer.opacity;
  for (let y = 0; y < layer.height * scale; y += block) {
    for (let x = 0; x < layer.width * scale; x += block) {
      ctx.fillStyle = (((Math.floor(x / block) + Math.floor(y / block)) % 2) === 0) ? 'rgb(180,180,180)' : 'rgb(120,120,120)';
      ctx.fillRect(layer.x * scale + x, layer.y * scale + y, block, block);
    }
  }
  ctx.restore();
}

function drawStripe(ctx, layer, scale) {
  const x = layer.x * scale;
  const y = layer.y * scale;
  const w = layer.width * scale;
  const h = layer.height * scale;
  ctx.save();
  ctx.globalAlpha = layer.opacity;
  
  // Removed background box for watermark style

  ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
  ctx.font = `900 ${Math.max(12, layer.stripeFontSize * scale)}px "${layer.stripeFontFamily}"`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  
  ctx.shadowColor = 'rgba(0, 0, 0, 0.6)';
  ctx.shadowBlur = 4 * scale;
  ctx.shadowOffsetX = 2 * scale;
  ctx.shadowOffsetY = 2 * scale;

  ctx.strokeStyle = 'rgba(0, 0, 0, 0.6)';
  ctx.lineWidth = Math.max(1, 3 * scale);
  ctx.lineJoin = 'round';

  if (layer.stripeOrientation === 'vertical') {
    const chars = Array.from(layer.stripeText || '');
    const line = layer.stripeFontSize * scale * 1.1;
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

function drawSelection(ctx, layer) {
  const x = layer.x * editor.zoom;
  const y = layer.y * editor.zoom;
  const w = layer.width * editor.zoom;
  const h = layer.height * editor.zoom;
  ctx.save();
  ctx.strokeStyle = '#1d7ef3';
  ctx.lineWidth = 2;
  ctx.strokeRect(x, y, w, h);
  ctx.fillStyle = '#1d7ef3';
  Object.values(handles(layer)).forEach(handle => ctx.fillRect(handle.x, handle.y, handle.s, handle.s));
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
  const ratio = Math.min(w / image.width, h / image.height);
  const dw = image.width * ratio;
  const dh = image.height * ratio;
  ctx.save();
  ctx.globalAlpha = layer.opacity;
  ctx.drawImage(image, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
  ctx.restore();
}

function drawRevealMask(ctx, layers, scale, globalColor, globalOpacity) {
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

  for (const layer of revealLayers) {
    maskCtx.beginPath();
    maskCtx.ellipse(
      (layer.x + layer.width / 2) * scale,
      (layer.y + layer.height / 2) * scale,
      (layer.width / 2) * scale,
      (layer.height / 2) * scale,
      0,
      0,
      Math.PI * 2
    );
    maskCtx.fill();
  }

  ctx.drawImage(maskCanvas, 0, 0);
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
    else if (layer.fillMode === 'mosaic') {
      drawMosaic(ctx, layer, editor.zoom);
    }
  }

  drawRevealMask(ctx, editor.layers, editor.zoom, editor.revealColor, editor.revealOpacity);

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
  const z = editor.zoom;
  return {
    left: layer.x * z,
    top: layer.y * z,
    right: (layer.x + layer.width) * z,
    bottom: (layer.y + layer.height) * z
  };
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
  }
}

function clearAll() {
  editor.layers = [];
  editor.selectedId = null;
  render();
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
  editor.sourceMeta.postUrl = meta.postUrl || '';
  editor.layers = [];
  editor.selectedId = null;
  editor.nextId = 1;
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
    else if (layer.fillMode === 'mosaic') {
      drawMosaic(ctx, layer, scale);
    }
  }
  drawRevealMask(ctx, editor.layers, scale, editor.revealColor, editor.revealOpacity);
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

function onMouseDown(event) {
  if (!editor.image) return;
  const point = canvasPoint(event);
  
  // 1. Check if user clicked a handle of the currently selected layer
  const active = selectedLayer();
  if (active) {
    const handle = findHandle(point, active);
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
    editor.mode = 'move';
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
  if (!editor.mode || !editor.image) return;
  const point = imagePoint(canvasPoint(event));
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
  }
}

function onMouseUp() {
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
  render();
}

watch(() => props.sourceItem, async (item) => {
  if (!item?.localPath) return;
  await loadImageFromPath(item.localPath, item);
}, { immediate: true });

watch(() => [
  editor.fillMode,
  editor.opacity,
  editor.stripeText,
  editor.stripeFontFamily,
  editor.stripeFontSize,
  editor.stripeOrientation,
  editor.revealColor,
  editor.revealOpacity
], () => {
  const layer = selectedLayer();
  if (layer) applyControlsToLayer(layer);
  render();
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
    <aside class="panel card editor-side">
      <div class="panel-head">
        <h2>打码编辑</h2>
        <button class="secondary" @click="emit('back')">返回图库</button>
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
        </div>
      </section>

      <label class="field-full">
        <span>打码方式</span>
        <select v-model="editor.fillMode">
          <option value="mosaic">默认马赛克</option>
          <option value="stripe">文本水印 (无背景)</option>
          <option value="image">贴图填充</option>
          <option value="reveal">显示遮罩</option>
        </select>
      </label>

      <label class="field-full">
        <span>透明度 {{ Math.round(editor.opacity * 100) }}%</span>
        <input v-model.number="editor.opacity" type="range" min="0.1" max="1" step="0.05" />
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
        <label class="field-full">
          <span>字号</span>
          <input v-model.number="editor.stripeFontSize" type="number" min="8" max="96" />
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
            <span>{{ item.name }}</span>
          </button>
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
      </template>

      <div class="button-row compact">
        <button class="secondary" @click="fitToWindow">适应窗口</button>
        <button class="secondary" @click="actualSize">实际大小</button>
        <button class="secondary" @click="zoomIn">放大</button>
      </div>

      <div class="button-row compact">
        <button class="secondary" @click="undoLast" :disabled="!editor.layers.length">撤销</button>
        <button class="ghost" @click="deleteSelected" :disabled="!editor.selectedId">删除选中</button>
        <button class="ghost" @click="clearAll" :disabled="!editor.layers.length">清空</button>
      </div>

      <label class="field-full">
        <span>复制尺寸上限（像素，0 = 不限 / 原图大小，自动记忆）</span>
        <input v-model.number="editor.outputMaxEdge" type="number" min="0" step="100" />
      </label>

      <div class="button-row compact">
        <button @click="copyToClipboard()" :disabled="!editor.image" style="flex: 1;">复制到剪贴板</button>
        <button
          class="secondary"
          @click="copyOriginalToClipboard"
          :disabled="!editor.image"
          title="忽略尺寸上限，按原图分辨率复制"
        >复制原图</button>
      </div>
      <p v-if="copyStatus" class="inline-note">{{ copyStatus }}</p>
    </aside>

    <section class="panel card gallery-panel">
      <div class="canvas-head">
        <div>
          <h2>{{ editor.imageName || '未载入图片' }}</h2>
          <p class="inline-note">拖拽矩形创建区域。透明背景文字不会铺白底，作者和角色可以一键填入。</p>
        </div>
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
  </div>
</template>
