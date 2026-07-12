<script setup>
import { ref, computed, watch, onMounted } from 'vue';

const props = defineProps({
  sourceItem: { type: Object, default: null }
});
const emit = defineEmits(['back']);

// ---- Constants ----
const IMAGE_EXTS = new Set(['.jpg','.jpeg','.png','.gif','.webp','.bmp','.avif']);
const PAGE_SIZE = 15;
const SORT_OPTIONS = new Set(['default', 'score', 'fav']);
const STORAGE_KEY_EDITOR_HABITS = 'editorHabits';

function readEditorHabits() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY_EDITOR_HABITS) || '{}');
  } catch {
    return {};
  }
}

const editorHabits = readEditorHabits();
const habitMaxEdge = Number(editorHabits.outputMaxEdge);

// ---- Core state ----
const imagePath = ref('');
const meta = ref(null);
const captionText = ref('');
const captionEn = ref('');
const tagCaption = ref('');
const verifiedTags = ref([]);
const rejectedTags = ref([]);
const uncertainTags = ref([]);
const tagInput = ref('');
const message = ref('');
const saving = ref(false);
const loaded = ref(false);
const dirty = ref(false);
const copyMaxEdge = ref(Number.isFinite(habitMaxEdge) ? habitMaxEdge : 1600);

watch(copyMaxEdge, (value) => {
  const normalized = Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
  editorHabits.outputMaxEdge = normalized;
  try { localStorage.setItem(STORAGE_KEY_EDITOR_HABITS, JSON.stringify(editorHabits)); }
  catch { /* localStorage 异常时静默 */ }
});

// Image
const imageList = ref([]);
const currentDate = ref('');
const currentIndex = ref(0);
const imageZoom = ref(1);
const showZoomLabel = ref(false);
let zoomTimer = null;

// Gallery pagination
const galleryPage = ref(1);
const totalPages = computed(() => Math.max(1, Math.ceil(imageList.value.length / PAGE_SIZE)));
const pagedImages = computed(() => {
  const start = (galleryPage.value - 1) * PAGE_SIZE;
  return imageList.value.slice(start, start + PAGE_SIZE);
});
const hasCaptionPayload = computed(() =>
  !!(
    captionText.value ||
    captionEn.value ||
    tagCaption.value ||
    (verifiedTags.value && verifiedTags.value.length)
  )
);
const hasStructuredOutput = computed(() =>
  loaded.value ||
  !!(
    captionEn.value ||
    tagCaption.value ||
    (verifiedTags.value && verifiedTags.value.length) ||
    (rejectedTags.value && rejectedTags.value.length) ||
    (uncertainTags.value && uncertainTags.value.length)
  )
);

// Error marking
const errors = ref([]);
const markMode = ref(true);

// Pipeline
const manual = ref({
  open: true,
  stage: 1,           // 1 | 2 | 3 | 'done'
  promptBusy: false,
  s1: { pasted: '', parsed: null, parseError: '' },
  s2: { pasted: '', parsed: null, parseError: '' },
  s3: { pasted: '', parsed: null, parseError: '' }
});

// ---- Init ----
function isImageFile(name) {
  const ext = (name || '').toLowerCase();
  const dot = ext.lastIndexOf('.');
  return dot >= 0 && IMAGE_EXTS.has(ext.slice(dot));
}

function getCrawlerSortBy() {
  try {
    const habits = JSON.parse(localStorage.getItem('crawlerHabits') || '{}');
    return SORT_OPTIONS.has(habits.sortBy) ? habits.sortBy : 'default';
  } catch {
    return 'default';
  }
}

function sortLikeCrawler(items) {
  const sortBy = getCrawlerSortBy();
  if (sortBy === 'score') {
    return [...items].sort((a, b) => (b.score || 0) - (a.score || 0));
  }
  if (sortBy === 'fav') {
    return [...items].sort((a, b) => (b.favCount || 0) - (a.favCount || 0));
  }
  return items;
}

function normalizeTagAudit(list) {
  if (!Array.isArray(list)) return [];
  return list
    .filter(item => item && typeof item === 'object' && item.tag)
    .map(item => ({
      tag: String(item.tag || ''),
      reason: String(item.reason || '')
    }));
}

function auditTagsFromStage2(status) {
  const list = manual.value?.s2?.parsed?.tag_evaluation;
  if (!Array.isArray(list)) return [];
  return list
    .filter(item => item && item.status === status && item.tag)
    .map(item => ({
      tag: String(item.tag || ''),
      reason: String(item.reason || '')
    }));
}

async function initFromSource(item) {
  if (!item) return;
  imagePath.value = item.localPath || '';
  meta.value = null;
  captionText.value = ''; captionEn.value = ''; tagCaption.value = ''; verifiedTags.value = [];
  rejectedTags.value = []; uncertainTags.value = [];
  errors.value = []; tagInput.value = ''; message.value = '';
  loaded.value = false; dirty.value = false;
  imageZoom.value = 1; galleryPage.value = 1;
  manual.value = { open: true, stage: 1, promptBusy: false,
    s1: { pasted: '', parsed: null, parseError: '' },
    s2: { pasted: '', parsed: null, parseError: '' },
    s3: { pasted: '', parsed: null, parseError: '' } };

  const parts = imagePath.value.replace(/\\/g, '/').split('/');
  const hotIdx = parts.lastIndexOf('hot_pic');
  if (hotIdx >= 0 && parts.length > hotIdx + 2) currentDate.value = parts[hotIdx + 1];

  if (imagePath.value && window.desktopAPI) {
    try {
      const ctx = await window.desktopAPI.app.getContext();
      const dateDir = `${ctx.hotPicDir.replace(/\\/g, '/')}/${currentDate.value}`;
      const url = `http://127.0.0.1:8000/api/gallery_data/${currentDate.value}`;
      try {
        const resp = await fetch(url);
        if (resp.ok) {
          const data = await resp.json();
          imageList.value = sortLikeCrawler((data.local_images || [])
            .map(img => ({
              artist: img.artist || '未知',
              filename: img.filename,
              localPath: img.local_path || `${dateDir}/${img.filename}`,
              postUrl: img.post_url || '',
              characters: img.characters || '',
              tags: img.tags || {},
              score: img.score || 0,
              favCount: img.fav_count || 0
            }))
            .filter(img => isImageFile(img.filename)));
          const fname = imagePath.value.replace(/\\/g, '/').split('/').pop();
          const idx = imageList.value.findIndex(img => img.filename === fname);
          currentIndex.value = idx >= 0 ? idx : 0;
          if (idx >= 0) galleryPage.value = Math.floor(idx / PAGE_SIZE) + 1;
          if (idx >= 0 && imageList.value[idx]) {
            const img = imageList.value[idx];
            meta.value = {
              artist: img.artist,
              characters: typeof img.characters === 'string' ? img.characters : (Array.isArray(img.characters) ? img.characters.join(' ') : ''),
              copyright: img.tags?.tag_string_copyright || '',
              tags: img.tags || {}
            };
          }
        }
      } catch { imageList.value = isImageFile(item.filename) ? [item] : []; meta.value = { artist: item.artist, characters: item.characters || '', tags: item.tags || {} }; }
    } catch { imageList.value = isImageFile(item.filename) ? [item] : []; }
  }
  if (!imageList.value.length) imageList.value = [item];

  await loadExistingCaption();
  preloadThumbnails();
}

async function loadExistingCaption() {
  if (!imagePath.value || !window.desktopAPI?.caption?.read) return;
  try {
    const entry = await window.desktopAPI.caption.read(imagePath.value);
    if (entry) {
      captionText.value = entry.caption_zh || entry.caption || '';
      captionEn.value = entry.natural_caption_en || entry.caption_en || '';
      tagCaption.value = entry.tag_caption || '';
      verifiedTags.value = Array.isArray(entry.verified_tags) ? entry.verified_tags : [];
      rejectedTags.value = normalizeTagAudit(entry.rejected_tags);
      uncertainTags.value = normalizeTagAudit(entry.uncertain_tags);
      errors.value = Array.isArray(entry.errors) ? entry.errors : [];
      const m = manual.value;
      if (entry.s1_pasted) { m.s1.pasted = entry.s1_pasted; m.s1.parsed = entry.s1_parsed || null; }
      if (entry.s2_pasted) { m.s2.pasted = entry.s2_pasted; m.s2.parsed = entry.s2_parsed || null; }
      if (entry.s3_pasted) { m.s3.pasted = entry.s3_pasted; m.s3.parsed = entry.s3_parsed || null; }
      if (entry.pipeline_stage === 'done') m.stage = 'done';
      else if (entry.pipeline_stage) m.stage = entry.pipeline_stage;
      loaded.value = true;
    }
  } catch {}
}

// ---- Image navigation ----
function goToPrev() { if (currentIndex.value > 0) { currentIndex.value--; switchToCurrent(); } }
function goToNext() { if (currentIndex.value < imageList.value.length - 1) { currentIndex.value++; switchToCurrent(); } }
async function goToImage(idx) {
  currentIndex.value = idx;
  galleryPage.value = Math.floor(idx / PAGE_SIZE) + 1;
  await switchToCurrent();
}
async function switchToCurrent() {
  const img = imageList.value[currentIndex.value];
  if (!img) return;
  if (dirty.value) await saveCaptionSilent();
  imagePath.value = img.localPath || '';
  meta.value = { artist: img.artist, characters: img.characters || '', tags: img.tags || {} };
  captionText.value = ''; captionEn.value = ''; tagCaption.value = ''; verifiedTags.value = [];
  rejectedTags.value = []; uncertainTags.value = []; errors.value = [];
  loaded.value = false; dirty.value = false; imageZoom.value = 1;
  manual.value = { open: true, stage: 1, promptBusy: false,
    s1: { pasted: '', parsed: null, parseError: '' },
    s2: { pasted: '', parsed: null, parseError: '' },
    s3: { pasted: '', parsed: null, parseError: '' } };
  await loadExistingCaption();
}

// ---- Save ----
async function saveCaption() {
  if (!imagePath.value || saving.value) return;
  if (!window.desktopAPI?.caption?.save) { message.value = '保存接口不可用'; return; }
  const m = manual.value;
  const entry = {
    caption: captionText.value, caption_zh: captionText.value,
    caption_en: captionEn.value || '',
    natural_caption_en: captionEn.value || '',
    tag_caption: tagCaption.value || '',
    verified_tags: Array.isArray(verifiedTags.value) ? verifiedTags.value : [],
    rejected_tags: normalizeTagAudit(rejectedTags.value),
    uncertain_tags: normalizeTagAudit(uncertainTags.value),
    artist: meta.value?.artist || null,
    characters: meta.value?.characters || null,
    copyright: meta.value?.copyright || null,
    errors: errors.value,
    s1_pasted: m.s1.pasted, s1_parsed: m.s1.parsed,
    s2_pasted: m.s2.pasted, s2_parsed: m.s2.parsed,
    s3_pasted: m.s3.pasted, s3_parsed: m.s3.parsed,
    pipeline_stage: m.stage
  };
  saving.value = true; message.value = '保存中...';
  try {
    const result = await window.desktopAPI.caption.save(imagePath.value, entry);
    if (result?.ok) { message.value = '已保存'; dirty.value = false; }
    else message.value = `保存失败：${result?.error || '未知错误'}`;
  } catch (e) { message.value = `保存失败：${e.message || e}`; }
  saving.value = false;
}
async function saveCaptionSilent() {
  if (!imagePath.value || !window.desktopAPI?.caption?.save) return;
  const m = manual.value;
  const entry = {
    caption: captionText.value, caption_zh: captionText.value,
    caption_en: captionEn.value || '',
    natural_caption_en: captionEn.value || '',
    tag_caption: tagCaption.value || '',
    verified_tags: Array.isArray(verifiedTags.value) ? verifiedTags.value : [],
    rejected_tags: normalizeTagAudit(rejectedTags.value),
    uncertain_tags: normalizeTagAudit(uncertainTags.value),
    artist: meta.value?.artist || null, characters: meta.value?.characters || null,
    errors: errors.value,
    s1_pasted: m.s1.pasted, s1_parsed: m.s1.parsed,
    s2_pasted: m.s2.pasted, s2_parsed: m.s2.parsed,
    s3_pasted: m.s3.pasted, s3_parsed: m.s3.parsed,
    pipeline_stage: m.stage
  };
  try { await window.desktopAPI.caption.save(imagePath.value, entry); dirty.value = false; } catch {}
}

// ---- Pipeline ----
function normalizeTag(t) { return (t || '').toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, ''); }

async function copyStagePrompt(stage) {
  if (!imagePath.value || manual.value.promptBusy) return;
  manual.value.promptBusy = true; message.value = '';
  try {
    let verifyJson = null;
    if (stage === 3 && manual.value.s2.parsed) verifyJson = JSON.stringify(manual.value.s2.parsed);
    const body = { image_path: imagePath.value, with_artist: false, stage, verify_json: verifyJson };
    const resp = await fetch('http://127.0.0.1:8000/api/caption_prompt', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    if (!resp.ok) { message.value = `服务器错误: ${resp.status}`; return; }
    const data = await resp.json();
    if (!data.ok) { message.value = data.msg || '获取提示词失败'; return; }
    await navigator.clipboard.writeText(data.combined || data.user || '');
    message.value = `Stage ${stage} 提示词已复制到剪贴板`;
  } catch (e) { message.value = `复制失败: ${e.message || e}`; }
  manual.value.promptBusy = false;
}

function parseStageJson(stage) {
  const m = manual.value;
  const key = `s${stage}`;
  const raw = m[key].pasted.trim();
  if (!raw) return;
  let text = raw;
  if (text.startsWith('```json')) text = text.slice(7);
  else if (text.startsWith('```')) text = text.slice(3);
  if (text.endsWith('```')) text = text.slice(0, -3);
  text = text.trim();
  try {
    const parsed = JSON.parse(text);
    m[key].parsed = parsed;
    m[key].parseError = '';
    dirty.value = true;
    message.value = `Stage ${stage} 已解析 — 请审查并修正结果，确认后再进入下一阶段`;
  } catch (e) {
    m[key].parseError = `JSON 解析失败: ${e.message}`;
    m[key].parsed = null;
    message.value = m[key].parseError;
  }
}

function confirmStage1() { manual.value.stage = 2; message.value = '已确认观察结果，进入 Stage 2'; }
function confirmStage2() { manual.value.stage = 3; message.value = '已确认校验结果，进入 Stage 3'; }

function applyFinalCaption() {
  const m = manual.value;
  const raw = m.s3.pasted.trim();
  if (!raw) return;
  let text = raw;
  if (text.startsWith('```json')) text = text.slice(7);
  else if (text.startsWith('```')) text = text.slice(3);
  if (text.endsWith('```')) text = text.slice(0, -3);
  text = text.trim();
  try {
    const parsed = JSON.parse(text);
    m.s3.parsed = parsed; m.s3.parseError = '';
    captionText.value = (parsed.caption_zh || parsed.caption || '').trim();
    captionEn.value = (parsed.natural_caption_en || parsed.caption_en || '').trim();
    verifiedTags.value = Array.isArray(parsed.verified_tags) ? parsed.verified_tags : [];
    tagCaption.value = (
      parsed.tag_caption ||
      (Array.isArray(parsed.verified_tags) ? parsed.verified_tags.join(', ') : '') ||
      parsed.caption_en ||
      ''
    ).trim();
    rejectedTags.value = normalizeTagAudit(parsed.rejected_tags);
    if (!rejectedTags.value.length) rejectedTags.value = auditTagsFromStage2('absent');
    uncertainTags.value = normalizeTagAudit(parsed.uncertain_tags);
    if (!uncertainTags.value.length) uncertainTags.value = auditTagsFromStage2('uncertain');
    m.stage = 'done';
    message.value = '已解析结构化 JSON，点「保存」落盘';
  } catch {
    captionText.value = raw; captionEn.value = ''; tagCaption.value = ''; verifiedTags.value = [];
    rejectedTags.value = []; uncertainTags.value = [];
    m.s3.parseError = '未能解析为 JSON，已按纯文本应用';
    message.value = '已按纯文本应用（非 JSON），点「保存」落盘';
  }
  errors.value = []; loaded.value = true; dirty.value = true;
}

async function copyCaptionImage(options = {}) {
  if (!imagePath.value) return;
  if (!window.desktopAPI?.caption?.copyImage) { message.value = '当前不支持复制图片'; return; }
  const original = !!options?.original;
  const rawMaxEdge = original ? 0 : Number(copyMaxEdge.value);
  const maxEdge = Number.isFinite(rawMaxEdge) && rawMaxEdge > 0 ? Math.round(rawMaxEdge) : 0;
  try {
    const result = await window.desktopAPI.caption.copyImage(imagePath.value, maxEdge);
    const sizeLabel = result?.width && result?.height ? `（${result.width}×${result.height}${original ? ' · 原图' : ''}）` : '';
    message.value = result?.ok ? `图片已复制到剪贴板${sizeLabel}` : (result?.error || '复制失败');
  } catch (e) { message.value = `复制失败: ${e.message || e}`; }
}

function copyOriginalCaptionImage() { return copyCaptionImage({ original: true }); }

// ---- Stage 1 review helpers ----
function s1AddClothingTag() {
  if (!tagInput.value.trim()) return;
  const s1 = manual.value.s1.parsed;
  if (!s1) return;
  if (!Array.isArray(s1.clothing_tags)) s1.clothing_tags = [];
  const t = normalizeTag(tagInput.value);
  if (t && !s1.clothing_tags.includes(t)) { s1.clothing_tags.push(t); dirty.value = true; }
  tagInput.value = '';
}
function s1RemoveClothingTag(idx) {
  manual.value.s1.parsed?.clothing_tags?.splice(idx, 1);
  dirty.value = true;
}
function s1AddCharacter() {
  const s1 = manual.value.s1.parsed;
  if (!s1) return;
  if (!Array.isArray(s1.characters)) s1.characters = [];
  s1.characters.push({ hair_accessories: [], eye_features: '', expression: '', distinguishing_features: [] });
  dirty.value = true;
}
function s1RemoveCharacter(idx) {
  manual.value.s1.parsed?.characters?.splice(idx, 1);
  if (manual.value.s1.parsed) manual.value.s1.parsed.subjects_count = Math.max(1, (manual.value.s1.parsed.characters || []).length);
  dirty.value = true;
}
function s1AddDistFeat(charIdx) {
  const char = manual.value.s1.parsed?.characters?.[charIdx];
  if (!char) return;
  if (!Array.isArray(char.distinguishing_features)) char.distinguishing_features = [];
  char.distinguishing_features.push('');
  dirty.value = true;
}
function s1RemoveDistFeat(charIdx, featIdx) {
  manual.value.s1.parsed?.characters?.[charIdx]?.distinguishing_features?.splice(featIdx, 1);
  dirty.value = true;
}
function s1AddBgProp() {
  const s1 = manual.value.s1.parsed;
  if (!s1?.background) return;
  if (!Array.isArray(s1.background.props)) s1.background.props = [];
  s1.background.props.push('');
  dirty.value = true;
}
function s1RemoveBgProp(idx) {
  manual.value.s1.parsed?.background?.props?.splice(idx, 1);
  dirty.value = true;
}

// ---- Stage 2 interactive tag evaluation ----
function s2ToggleTagStatus(tagEval, newStatus) {
  tagEval.status = newStatus;
  dirty.value = true;
}
function s2AllVisibleToVerified() {
  const s2 = manual.value.s2.parsed;
  if (!s2?.tag_evaluation) return;
  for (const te of s2.tag_evaluation) {
    if (te.status === 'visible') {
      const t = normalizeTag(te.tag);
      if (t && !verifiedTags.value.includes(t)) verifiedTags.value.push(t);
    }
  }
  // Also add character/copyright if consistent
  const ci = s2.character_identification;
  if (ci?.consistent) {
    const metaTags = (meta.value?.tags?.tag_string_character || '').split(/\s+/).filter(Boolean);
    const copyrightTags = (meta.value?.tags?.tag_string_copyright || '').split(/\s+/).filter(Boolean);
    for (const t of [...metaTags, ...copyrightTags]) {
      const nt = normalizeTag(t);
      if (nt && !verifiedTags.value.includes(nt)) verifiedTags.value.push(nt);
    }
  }
  dirty.value = true;
  message.value = '已从 visible tags 汇总 verified_tags';
}

// ---- Error marking ----
const errorTypes = [
  { value: 'character', label: '角色识别错误' },
  { value: 'omission', label: '遗漏元素' },
  { value: 'clothing_color', label: '服饰/颜色错误' },
  { value: 'hallucination', label: '凭空臆造' },
  { value: 'other', label: '其他' }
];
function markErrorFromSelection() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  const text = range.toString().trim();
  if (!text) return;
  const preRange = document.createRange();
  const container = document.getElementById('caption-text-editable');
  if (!container) return;
  preRange.selectNodeContents(container);
  preRange.setEnd(range.startContainer, range.startOffset);
  const startOffset = preRange.toString().length;
  errors.value.push({ start: startOffset, end: startOffset + text.length, text, type: '', note: '' });
  dirty.value = true;
  window.getSelection().removeAllRanges();
}
function setErrorType(idx, type) { errors.value[idx].type = type; dirty.value = true; }
function updateErrorNote(idx, note) { errors.value[idx].note = note; dirty.value = true; }
function removeError(idx) { errors.value.splice(idx, 1); dirty.value = true; }
function clearErrors() { errors.value = []; dirty.value = true; }
const correctionPrompt = computed(() => {
  if (!errors.value.length || !captionText.value) return '';
  const lines = ['以下是之前生成的图片描述，请根据我标注的错误进行修正：', '', '【原始描述】', captionText.value, '', '【需要修正的错误】'];
  errors.value.forEach((e, i) => {
    const typeLabel = (errorTypes.find(t => t.value === e.type) || {}).label || '未分类';
    lines.push(`${i + 1}. [${typeLabel}] 原文片段：「${e.text}」`);
    if (e.note) lines.push(`   修正说明：${e.note}`);
  });
  lines.push('', '请重新生成一段修正后的中文描述，直接输出修正后的散文段落，不要额外解释。');
  return lines.join('\n');
});
async function copyCorrectionPrompt() {
  if (!correctionPrompt.value) return;
  try { await navigator.clipboard.writeText(correctionPrompt.value); message.value = '修正提示词已复制到剪贴板'; }
  catch { message.value = '复制失败'; }
}

// ---- Tag management ----
const reviewAnchors = computed(() => {
  const ra = manual.value?.s2?.parsed?.review_anchors;
  if (!Array.isArray(ra) || !ra.length) return [];
  const have = new Set((verifiedTags.value || []).map(t => String(t).toLowerCase()));
  return ra.filter(a => a && a.tag && !have.has(String(a.tag).toLowerCase()));
});
function addVerifiedTag(fromInput) {
  const t = normalizeTag(fromInput ? tagInput.value : '');
  if (!t) return;
  if (!Array.isArray(verifiedTags.value)) verifiedTags.value = [];
  if (!verifiedTags.value.includes(t)) { verifiedTags.value.push(t); dirty.value = true; }
  if (fromInput) tagInput.value = '';
}
function removeVerifiedTag(idx) { verifiedTags.value.splice(idx, 1); dirty.value = true; }

// Caption render segments
const captionRenderSegments = computed(() => {
  const text = captionText.value || '';
  const spans = errors.value.filter(e => e.start >= 0 && e.end > e.start).sort((a, b) => a.start - b.start);
  const segments = []; let pos = 0;
  for (const s of spans) {
    if (s.start > pos) segments.push({ text: text.slice(pos, s.start), error: false });
    segments.push({ text: text.slice(s.start, s.end), error: true, idx: errors.value.indexOf(s) });
    pos = s.end;
  }
  if (pos < text.length) segments.push({ text: text.slice(pos), error: false });
  return segments;
});

// Character identification
const characterId = computed(() => {
  const s2 = manual.value?.s2?.parsed;
  if (!s2?.character_identification) return null;
  const ci = s2.character_identification;
  return { name: ci.provided_name || '', series: ci.provided_series || '', consistent: ci.consistent, confidence: ci.confidence ?? 0, reason: ci.reason || '', fallback: ci.fallback_description || '' };
});
const showCharacterWarning = computed(() => {
  const ci = characterId.value;
  if (!ci) return false;
  return !ci.consistent || ci.confidence < 0.7;
});

// ---- Image zoom ----
const currentImgUrl = ref('');
watch(imagePath, async (p) => {
  if (!p || !window.desktopAPI?.file?.toLocalUrl) { currentImgUrl.value = ''; return; }
  currentImgUrl.value = await window.desktopAPI.file.toLocalUrl(p);
}, { immediate: true });
function onImageWheel(e) {
  if (e.ctrlKey) {
    e.preventDefault();
    const delta = -e.deltaY * 0.005;
    imageZoom.value = Math.max(0.2, Math.min(5.0, imageZoom.value + delta));
    showZoomLabel.value = true;
    clearTimeout(zoomTimer);
    zoomTimer = setTimeout(() => showZoomLabel.value = false, 1500);
  }
}
function resetZoom() { imageZoom.value = 1; }

// ---- Thumbnails ----
const thumbUrls = ref({});
async function preloadThumbnails() {
  if (!window.desktopAPI?.file?.toThumbUrl) return;
  const pageImgs = pagedImages.value;
  for (const img of pageImgs) {
    if (!img.localPath || thumbUrls.value[img.localPath]) continue;
    thumbUrls.value[img.localPath] = await window.desktopAPI.file.toThumbUrl(img.localPath, 180);
  }
}
watch(galleryPage, () => preloadThumbnails());

// ---- Page nav ----
function prevGalleryPage() { if (galleryPage.value > 1) { galleryPage.value--; preloadThumbnails(); } }
function nextGalleryPage() { if (galleryPage.value < totalPages.value) { galleryPage.value++; preloadThumbnails(); } }

// ---- Lifecycle ----
onMounted(() => { if (props.sourceItem) initFromSource(props.sourceItem); });
watch(() => props.sourceItem, (item) => { if (item) initFromSource(item); });
</script>

<template>
  <div class="caption-page">
    <!-- Top bar -->
    <header class="caption-header">
      <button class="secondary" @click="emit('back')">← 返回画廊</button>
      <div class="caption-nav">
        <button class="secondary" :disabled="currentIndex <= 0" @click="goToPrev">◀ 上一张</button>
        <span class="nav-info">{{ imageList.length ? currentIndex + 1 : 0 }} / {{ imageList.length }}</span>
        <button class="secondary" :disabled="currentIndex >= imageList.length - 1" @click="goToNext">下一张</button>
      </div>
      <div class="caption-header-right">
        <span v-if="message" class="caption-msg">{{ message }}</span>
        <button :disabled="!hasCaptionPayload || saving" @click="saveCaption" class="primary">{{ saving ? '保存中...' : '💾 保存' }}</button>
        <span v-if="dirty" class="dirty-dot" title="有未保存的修改">●</span>
      </div>
    </header>

    <!-- Body: left image + right workspace -->
    <div class="caption-body">
      <!-- Left: Image viewer -->
      <section class="caption-image-panel" @wheel="onImageWheel">
        <div class="image-container" @dblclick="resetZoom">
          <img v-if="currentImgUrl" :src="currentImgUrl" class="caption-image" :style="{ transform: `scale(${imageZoom})` }" />
          <div v-else class="image-placeholder">无图片</div>
          <div v-if="showZoomLabel" class="zoom-label">{{ Math.round(imageZoom * 100) }}%</div>
        </div>
        <div class="image-controls">
          <span class="zoom-hint">🖱 Ctrl+滚轮缩放 · 双击重置 · {{ Math.round(imageZoom * 100) }}%</span>
          <div class="copy-controls">
            <label class="caption-copy-size" title="0 = 不限 / 原图大小，自动记忆">
              <span>复制尺寸上限</span>
              <input v-model.number="copyMaxEdge" type="number" min="0" step="100" />
            </label>
            <button class="secondary" :disabled="!imagePath" @click="copyCaptionImage()">复制图片</button>
            <button
              class="secondary"
              :disabled="!imagePath"
              title="忽略尺寸上限，按原图分辨率复制"
              @click="copyOriginalCaptionImage"
            >原图</button>
          </div>
        </div>
      </section>

      <!-- Right: Caption workspace -->
      <section class="caption-workspace">
        <!-- Metadata -->
        <div class="caption-meta" v-if="meta">
          <span v-if="meta.characters"><b>角色：</b>{{ meta.characters }}</span>
          <span v-if="meta.copyright"><b>作品：</b>{{ meta.copyright }}</span>
        </div>

        <!-- Character warning -->
        <div v-if="showCharacterWarning" class="char-warning">
          ⚠️ 角色身份置信度{{ characterId.confidence < 0.5 ? '较低' : '一般' }}
          <template v-if="!characterId.consistent">（识别不一致）</template>
          <template v-if="characterId.reason">：{{ characterId.reason }}</template>
          <br />建议使用外观描述替代角色名。如需包含角色名请在 Stage 2 审查面板中手动开启。
        </div>

        <!-- 3-Stage Pipeline -->
        <details class="pipeline-block" :open="manual.open">
          <summary @click.prevent="manual.open = !manual.open">🔧 3 阶段 Pipeline（手动复制粘贴 + 人工审查）</summary>
          <div class="pipeline-stages">

            <!-- ===== Stage 1 ===== -->
            <div :class="['pipeline-stage', manual.stage === 1 ? 'active' : (manual.stage > 1 || manual.stage === 'done' ? 'done' : 'locked')]">
              <div class="stage-header">
                <span class="stage-title">① Stage 1 · 观察</span>
                <span v-if="manual.stage > 1 || manual.stage === 'done'" class="stage-status done">✓</span>
                <span v-else-if="manual.stage === 1" class="stage-status active">当前</span>
              </div>
              <!-- Step 1a: copy prompt & paste -->
              <div v-if="manual.stage === 1 && !manual.s1.parsed" class="stage-body">
                <button class="secondary" :disabled="manual.promptBusy" @click="copyStagePrompt(1)">复制 Stage 1 提示词</button>
                <button class="secondary" :disabled="!imagePath" @click="copyCaptionImage()">🖼️ 复制图片</button>
                <textarea v-model="manual.s1.pasted" placeholder="粘贴 LLM 返回的 JSON..." rows="3"></textarea>
                <div v-if="manual.s1.parseError" class="pipeline-error">⚠️ {{ manual.s1.parseError }}</div>
                <button :disabled="!manual.s1.pasted.trim()" @click="parseStageJson(1)" class="primary">✓ 解析 JSON</button>
              </div>
              <!-- Step 1b: review & edit parsed result -->
              <div v-else-if="manual.stage === 1 && manual.s1.parsed" class="stage-body s1-review">
                <div class="review-section">
                  <h4>审查观察结果</h4>
                  <div class="review-grid">
                    <div class="review-field">
                      <label>人数</label>
                      <input type="number" v-model.number="manual.s1.parsed.subjects_count" min="1" @change="dirty = true" />
                    </div>
                    <div class="review-field">
                      <label>取景 (framing)</label>
                      <input v-model="manual.s1.parsed.composition.framing" @change="dirty = true" />
                    </div>
                    <div class="review-field">
                      <label>镜头角度</label>
                      <input v-model="manual.s1.parsed.composition.camera_angle" @change="dirty = true" />
                    </div>
                    <div class="review-field">
                      <label>视线方向</label>
                      <input v-model="manual.s1.parsed.composition.gaze_direction" @change="dirty = true" />
                    </div>
                  </div>
                  <!-- Characters -->
                  <div class="review-subsection">
                    <div class="review-subhead">
                      <span>人物 ({{ (manual.s1.parsed.characters || []).length }})</span>
                      <button class="secondary" @click="s1AddCharacter">＋ 添加人物</button>
                    </div>
                    <div v-for="(ch, ci) in manual.s1.parsed.characters" :key="`ch-${ci}`" class="review-char">
                      <div class="review-char-head">
                        <span>人物 #{{ ci + 1 }}</span>
                        <button class="ghost" @click="s1RemoveCharacter(ci)">✕</button>
                      </div>
                      <input v-model="ch.expression" placeholder="表情" @change="dirty = true" />
                      <div class="review-tags">
                        <span v-for="(df, di) in (ch.distinguishing_features || [])" :key="`df-${di}`" class="review-tag">
                          <input v-model="ch.distinguishing_features[di]" size="14" @change="dirty = true" />
                          <button class="ghost" @click="s1RemoveDistFeat(ci, di)">×</button>
                        </span>
                        <button class="secondary small" @click="s1AddDistFeat(ci)">＋ 特征</button>
                      </div>
                    </div>
                  </div>
                  <!-- Clothing tags -->
                  <div class="review-subsection">
                    <div class="review-subhead"><span>服饰标签</span></div>
                    <div class="review-tags">
                      <span v-for="(ct, cti) in (manual.s1.parsed.clothing_tags || [])" :key="`ct-${cti}`" class="review-tag">
                        {{ ct }}<button class="ghost" @click="s1RemoveClothingTag(cti)">×</button>
                      </span>
                    </div>
                    <div class="tag-add-row">
                      <input v-model="tagInput" @keyup.enter="s1AddClothingTag()" placeholder="添加服饰标签..." class="tag-input" />
                      <button class="secondary" @click="s1AddClothingTag">＋</button>
                    </div>
                  </div>
                  <!-- Background summary -->
                  <div class="review-subsection" v-if="manual.s1.parsed.background">
                    <div class="review-subhead"><span>背景 / 风格</span></div>
                    <div class="review-grid small">
                      <div class="review-field"><label>场景</label><input v-model="manual.s1.parsed.background.environment" @change="dirty = true" /></div>
                      <div class="review-field"><label>光照</label><input v-model="manual.s1.parsed.background.lighting" @change="dirty = true" /></div>
                      <div class="review-field"><label>色调</label><input v-model="manual.s1.parsed.background.palette" @change="dirty = true" /></div>
                      <div class="review-field"><label>氛围</label><input v-model="manual.s1.parsed.background.mood" @change="dirty = true" /></div>
                    </div>
                    <div class="review-tags" style="margin-top:4px">
                      <span v-for="(p, pi) in (manual.s1.parsed.background.props || [])" :key="`bp-${pi}`" class="review-tag">
                        <input v-model="manual.s1.parsed.background.props[pi]" size="10" @change="dirty = true" />
                        <button class="ghost" @click="s1RemoveBgProp(pi)">×</button>
                      </span>
                      <button class="secondary small" @click="s1AddBgProp">＋ 道具</button>
                    </div>
                  </div>
                </div>
                <button @click="confirmStage1" class="primary" style="align-self: flex-end;">✓ 确认观察结果，进入 Stage 2</button>
              </div>
              <div v-else-if="manual.stage > 1 || manual.stage === 'done'" class="stage-summary">已审查 · {{ Object.keys(manual.s1.parsed || {}).length }} 个字段</div>
            </div>

            <!-- ===== Stage 2 ===== -->
            <div :class="['pipeline-stage', manual.stage === 2 ? 'active' : (manual.stage > 2 || manual.stage === 'done' ? 'done' : 'locked')]">
              <div class="stage-header">
                <span class="stage-title">② Stage 2 · 校验 (交互式 tag 审查)</span>
                <span v-if="manual.stage > 2 || manual.stage === 'done'" class="stage-status done">✓</span>
                <span v-else-if="manual.stage === 2" class="stage-status active">当前</span>
              </div>
              <!-- Step 2a: copy prompt & paste -->
              <div v-if="manual.stage === 2 && !manual.s2.parsed" class="stage-body">
                <button class="secondary" :disabled="manual.promptBusy" @click="copyStagePrompt(2)">复制 Stage 2 提示词</button>
                <textarea v-model="manual.s2.pasted" placeholder="粘贴 LLM 返回的 JSON..." rows="4"></textarea>
                <div v-if="manual.s2.parseError" class="pipeline-error">⚠️ {{ manual.s2.parseError }}</div>
                <button :disabled="!manual.s2.pasted.trim()" @click="parseStageJson(2)" class="primary">✓ 解析 JSON，进入审查</button>
              </div>
              <!-- Step 2b: interactive tag review -->
              <div v-else-if="manual.stage === 2 && manual.s2.parsed" class="stage-body s2-review">
                <div class="review-section">
                  <h4>审查校验结果</h4>
                  <!-- Character identification -->
                  <div class="s2-char-id" v-if="manual.s2.parsed.character_identification">
                    <div class="s2-char-head">
                      <span><b>角色识别</b></span>
                      <label class="s2-toggle">
                        <input type="checkbox" :checked="manual.s2.parsed.character_identification.consistent" @change="manual.s2.parsed.character_identification.consistent = !manual.s2.parsed.character_identification.consistent; dirty = true" />
                        一致
                      </label>
                    </div>
                    <div class="s2-char-detail">
                      <span v-if="manual.s2.parsed.character_identification.provided_name">名称：{{ manual.s2.parsed.character_identification.provided_name }}</span>
                      <span v-if="manual.s2.parsed.character_identification.provided_series">作品：{{ manual.s2.parsed.character_identification.provided_series }}</span>
                      <span>置信度：{{ (manual.s2.parsed.character_identification.confidence ?? 0) * 100 }}%</span>
                      <span class="s2-reason">{{ manual.s2.parsed.character_identification.reason }}</span>
                    </div>
                    <div v-if="!manual.s2.parsed.character_identification.consistent">
                      <label>fallback 描述：</label>
                      <input v-model="manual.s2.parsed.character_identification.fallback_description" @change="dirty = true" style="width:100%;margin-top:4px" />
                    </div>
                  </div>
                  <!-- Tag evaluation list -->
                  <div class="s2-tag-list" v-if="manual.s2.parsed.tag_evaluation?.length">
                    <div class="s2-tag-head">
                      <span>Tag 评估 ({{ manual.s2.parsed.tag_evaluation.length }} 条)</span>
                      <button class="secondary small" @click="s2AllVisibleToVerified">全部 visible → verified_tags</button>
                    </div>
                    <div class="s2-tag-items">
                      <div v-for="(te, tei) in manual.s2.parsed.tag_evaluation" :key="`te-${tei}`" :class="['s2-tag-row', `s2-tag-${te.status}`]">
                        <span class="s2-tag-name">{{ te.tag }}</span>
                        <div class="s2-tag-status">
                          <button :class="{ on: te.status === 'visible' }" @click="s2ToggleTagStatus(te, 'visible')" title="图中可见">👁 visible</button>
                          <button :class="{ on: te.status === 'absent' }" @click="s2ToggleTagStatus(te, 'absent')" title="图中没有">✕ absent</button>
                          <button :class="{ on: te.status === 'uncertain' }" @click="s2ToggleTagStatus(te, 'uncertain')" title="不确定">? uncertain</button>
                        </div>
                        <span class="s2-tag-reason">{{ te.reason }}</span>
                      </div>
                    </div>
                  </div>
                  <!-- verified_tags preview -->
                  <div class="review-subsection">
                    <div class="review-subhead"><span>verified_tags 预览 ({{ verifiedTags.length }})</span></div>
                    <div class="tag-list">
                      <span v-for="(t, i) in verifiedTags" :key="`vt-${i}`" class="tag-chip">{{ t }}<button class="tag-x" @click="removeVerifiedTag(i)">×</button></span>
                      <span v-if="!verifiedTags.length" class="tag-empty">（点击上方「全部 visible → verified_tags」自动汇总）</span>
                    </div>
                    <div class="tag-add-row" style="margin-top:4px">
                      <input v-model="tagInput" @keyup.enter="addVerifiedTag(true)" placeholder="手动添加标签..." class="tag-input" />
                      <button class="secondary" :disabled="!tagInput.trim()" @click="addVerifiedTag(true)">＋</button>
                    </div>
                  </div>
                  <!-- review_anchors -->
                  <div v-if="reviewAnchors.length" class="review-subsection">
                    <div class="review-subhead"><span>⚠️ 待确认锚点</span></div>
                    <div class="tag-list">
                      <span v-for="(a, ai) in reviewAnchors" :key="`ra-${ai}`" class="tag-chip tag-review" :title="a.reason || ''">
                        {{ a.tag }}<button class="tag-add-btn" @click="addVerifiedTag(); tagInput = a.tag; addVerifiedTag(true)">＋</button>
                      </span>
                    </div>
                  </div>
                </div>
                <button @click="confirmStage2" class="primary" style="align-self: flex-end;">✓ 确认校验结果，进入 Stage 3</button>
              </div>
              <div v-else-if="manual.stage > 2 || manual.stage === 'done'" class="stage-summary">
                已审查 · {{ (manual.s2.parsed?.tag_evaluation || []).length }} 条 tag
                <template v-if="manual.s2.parsed?.character_identification">
                  · 角色 {{ manual.s2.parsed.character_identification.consistent === false ? '❌ 不一致' : (manual.s2.parsed.character_identification.consistent ? '✓ 一致' : '—') }}
                </template>
              </div>
            </div>

            <!-- ===== Stage 3 ===== -->
            <div :class="['pipeline-stage', manual.stage === 3 ? 'active' : (manual.stage === 'done' ? 'done' : 'locked')]">
              <div class="stage-header">
                <span class="stage-title">③ Stage 3 · 成文</span>
                <span v-if="manual.stage === 'done'" class="stage-status done">✓</span>
                <span v-else-if="manual.stage === 3" class="stage-status active">当前</span>
              </div>
              <div v-if="manual.stage === 3" class="stage-body">
                <button class="secondary" :disabled="manual.promptBusy" @click="copyStagePrompt(3)">复制 Stage 3 提示词</button>
                <p class="stage-hint">在同一对话粘贴，返回的 JSON（含 tag_caption / natural_caption_en / caption_zh / verified_tags / tag 审计）粘到下方：</p>
                <textarea v-model="manual.s3.pasted" placeholder='粘贴 Stage 3 返回的 JSON...' rows="4"></textarea>
                <div v-if="manual.s3.parseError" class="pipeline-error">⚠️ {{ manual.s3.parseError }}</div>
                <button :disabled="!manual.s3.pasted.trim()" @click="applyFinalCaption" class="primary">✓ 应用最终结果</button>
              </div>
              <div v-else-if="manual.stage === 'done'" class="stage-summary">
                已应用 · 训练文本 {{ tagCaption ? '✓' : '—' }} · 中文 {{ (captionText || '').length }} 字 · 标签 {{ (verifiedTags || []).length }} 个
              </div>
            </div>
          </div>
        </details>

        <!-- Structured outputs -->
        <div v-if="hasStructuredOutput" class="structured-block">
          <div v-if="tagCaption" class="structured-section">
            <div class="structured-label">tag_caption（训练用主文本）</div>
            <pre class="structured-en">{{ tagCaption }}</pre>
          </div>
          <div v-if="captionEn" class="structured-section">
            <div class="structured-label">natural_caption_en（自然英文辅助描述）</div>
            <pre class="structured-en">{{ captionEn }}</pre>
          </div>
          <div class="structured-section">
            <div class="structured-label">verified_tags（{{ (verifiedTags || []).length }}）· 可增删</div>
            <div class="tag-list">
              <span v-for="(t, i) in verifiedTags" :key="`vt2-${i}`" class="tag-chip">{{ t }}<button class="tag-x" @click="removeVerifiedTag(i)">×</button></span>
            </div>
            <div class="tag-add-row">
              <input v-model="tagInput" @keyup.enter="addVerifiedTag(true)" placeholder="添加标签..." class="tag-input" />
              <button class="secondary" :disabled="!tagInput.trim()" @click="addVerifiedTag(true)">＋</button>
            </div>
          </div>
          <div v-if="rejectedTags.length" class="structured-section">
            <div class="structured-label">rejected_tags（{{ rejectedTags.length }}）</div>
            <div class="tag-list">
              <span v-for="(t, i) in rejectedTags" :key="`rej-${i}`" class="tag-chip tag-rejected" :title="t.reason">{{ t.tag }}</span>
            </div>
          </div>
          <div v-if="uncertainTags.length" class="structured-section">
            <div class="structured-label">uncertain_tags（{{ uncertainTags.length }}）</div>
            <div class="tag-list">
              <span v-for="(t, i) in uncertainTags" :key="`unc-${i}`" class="tag-chip tag-uncertain" :title="t.reason">{{ t.tag }}</span>
            </div>
          </div>
        </div>

        <!-- Chinese description -->
        <div class="caption-zh-section" v-if="loaded || captionText">
          <div class="caption-zh-header">
            <h3>📝 中文描述 (caption_zh)</h3>
            <div class="mode-switch">
              <button :class="{ active: markMode }" @click="markMode = true">🖍 标记</button>
              <button :class="{ active: !markMode }" @click="markMode = false">✏️ 编辑</button>
            </div>
          </div>
          <div v-if="markMode" class="caption-zh-display">
            <div id="caption-text-editable" class="caption-text-content" @mouseup="markErrorFromSelection">
              <template v-for="(seg, i) in captionRenderSegments" :key="i">
                <span v-if="!seg.error">{{ seg.text }}</span>
                <span v-else class="error-highlight" :title="`#${seg.idx + 1}: ${(errorTypes.find(t => t.value === errors[seg.idx]?.type) || {}).label || '未分类'}`">{{ seg.text }}</span>
              </template>
              <span v-if="!captionText" class="empty-hint">（暂无描述）</span>
            </div>
            <p class="mark-hint">🖍 拖选文字标记错误 → 填写类型和修正说明 → 构建修正提示词</p>
          </div>
          <textarea v-else v-model="captionText" class="caption-zh-textarea" rows="6" placeholder="中文描述..." @input="dirty = true"></textarea>
          <!-- Error list -->
          <div v-if="errors.length" class="error-list">
            <h4>已标记的错误 ({{ errors.length }})</h4>
            <div v-for="(err, i) in errors" :key="i" class="error-item">
              <div class="error-item-header">
                <span class="error-idx">#{{ i + 1 }}</span>
                <span class="error-text-preview">「{{ err.text }}」</span>
                <button class="ghost" @click="removeError(i)">✕</button>
              </div>
              <div class="error-item-body">
                <select :value="err.type" @change="setErrorType(i, ($event.target).value)">
                  <option value="">选择错误类型...</option>
                  <option v-for="et in errorTypes" :key="et.value" :value="et.value">{{ et.label }}</option>
                </select>
                <input :value="err.note" @input="updateErrorNote(i, ($event.target).value)" placeholder="修正说明" class="error-note" />
              </div>
            </div>
            <div class="error-actions">
              <button class="secondary" @click="clearErrors">清空</button>
              <button class="primary" :disabled="!errors.length || !errors.some(e => e.type)" @click="copyCorrectionPrompt">构建修正提示词</button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- Bottom gallery strip (paginated) -->
    <footer class="caption-gallery-strip" v-if="imageList.length > 1">
      <button class="page-nav" :disabled="galleryPage <= 1" @click="prevGalleryPage">◀</button>
      <div class="strip-scroll">
        <div v-for="(img, i) in pagedImages" :key="img.localPath || i" :class="['strip-thumb', { active: ((galleryPage - 1) * PAGE_SIZE + i) === currentIndex }]" @click="goToImage((galleryPage - 1) * PAGE_SIZE + i)">
          <img v-if="img.localPath && thumbUrls[img.localPath]" :src="thumbUrls[img.localPath]" :alt="img.filename" />
          <span class="strip-label">{{ img.filename }}</span>
        </div>
      </div>
      <button class="page-nav" :disabled="galleryPage >= totalPages" @click="nextGalleryPage">▶</button>
      <span class="page-info">{{ galleryPage }}/{{ totalPages }}</span>
    </footer>
  </div>
</template>

<style scoped>
.caption-page { display: flex; flex-direction: column; height: 100%; background: var(--bg); color: var(--ink); }
.caption-header { display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 20px; box-shadow: 0 14px 30px rgba(87, 58, 25, 0.10); flex-shrink: 0; margin-bottom: 12px; }
.caption-nav { display: flex; align-items: center; gap: 6px; }
.nav-info { font-size: 13px; color: var(--muted); min-width: 60px; text-align: center; }
.caption-header-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.caption-msg { font-size: 12px; color: var(--muted); max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dirty-dot { color: var(--accent); font-size: 10px; }

.caption-body { display: flex; flex: 1; overflow: hidden; }
.caption-image-panel { flex: 0 0 55%; display: flex; flex-direction: column; min-width: 0; background: rgba(32, 23, 15, 0.96); border: 1px solid rgba(74,53,25,0.08); border-radius: 20px; overflow: hidden; box-shadow: 0 14px 30px rgba(87, 58, 25, 0.10); }
.image-container { flex: 1; display: flex; align-items: center; justify-content: center; overflow: auto; padding: 16px; position: relative; min-height: 0; }
.caption-image { max-width: 100%; max-height: 100%; object-fit: contain; transition: transform 0.1s; border-radius: 8px; box-shadow: 0 20px 50px rgba(0, 0, 0, 0.45); }
.image-placeholder { color: rgba(255,255,255,0.55); font-size: 18px; }
.zoom-label { position: absolute; bottom: 24px; right: 24px; background: rgba(0,0,0,0.7); color: #fff; padding: 4px 10px; border-radius: 4px; font-size: 13px; pointer-events: none; }
.image-controls { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; padding: 6px 16px; background: rgba(0,0,0,0.24); border-top: 1px solid rgba(255,255,255,0.08); }
.zoom-hint { color: rgba(255,255,255,0.58); font-size: 11px; }
.copy-controls { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.caption-copy-size { display: flex; align-items: center; gap: 6px; color: rgba(255,255,255,0.62); font-size: 11px; }
.caption-copy-size input { width: 86px; padding: 4px 7px; border: 1px solid rgba(255,255,255,0.18); border-radius: 8px; background: rgba(255,255,255,0.08); color: #fff; font-size: 11px; }
.copy-controls button { padding: 4px 9px; border-radius: 8px; font-size: 11px; }

.caption-workspace { flex: 1; overflow-y: auto; padding: 0 0 0 16px; min-width: 0; }

/* Metadata */
.caption-meta { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 12px; padding: 12px; border: 1px solid rgba(74,53,25,0.08); border-radius: 18px; background: rgba(255,255,255,0.55); font-size: 13px; }
.caption-meta span { color: var(--ink); }

/* Char warning */
.char-warning { background: rgba(212, 143, 47, 0.16); border: 1px solid rgba(212, 143, 47, 0.32); border-radius: 14px; padding: 10px 12px; margin-bottom: 12px; font-size: 13px; color: #8d5a16; }

/* Pipeline */
.pipeline-block { margin-bottom: 16px; border: 1px solid var(--line); border-radius: 18px; overflow: hidden; background: var(--panel); box-shadow: 0 14px 30px rgba(87, 58, 25, 0.10); }
.pipeline-block summary { padding: 12px 16px; background: linear-gradient(135deg, #fbf4eb, #f2e8db); cursor: pointer; font-weight: 700; font-size: 14px; }
.pipeline-stages { padding: 8px; }
.pipeline-stage { margin-bottom: 8px; border: 1px solid rgba(74,53,25,0.08); border-radius: 14px; overflow: hidden; background: rgba(255,255,255,0.55); }
.pipeline-stage.active { border-color: rgba(182, 84, 52, 0.42); box-shadow: 0 8px 18px rgba(182, 84, 52, 0.12); }
.pipeline-stage.done { border-color: rgba(109, 139, 87, 0.42); opacity: 0.9; }
.pipeline-stage.locked { opacity: 0.5; }
.stage-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: linear-gradient(135deg, #fbf4eb, #f2e8db); }
.stage-title { font-size: 13px; font-weight: 600; flex: 1; }
.stage-status { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
.stage-status.active { background: linear-gradient(135deg, var(--accent), var(--accent-deep)); color: #fff; }
.stage-status.done { background: linear-gradient(135deg, #6d8b57, #4d6841); color: #fff; }
.stage-body { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.stage-body textarea { width: 100%; font-size: 12px; font-family: Consolas, monospace; padding: 8px; border: 1px solid var(--line); border-radius: 12px; resize: vertical; color: var(--ink); }
.stage-hint { font-size: 12px; color: var(--muted); margin: 0; }
.stage-summary { padding: 8px 12px; font-size: 12px; color: var(--muted); }
.pipeline-error { color: #9d2c2c; font-size: 12px; }

/* Stage 1 review */
.s1-review, .s2-review { max-height: 500px; overflow-y: auto; }
.review-section h4 { margin: 0 0 8px; font-size: 14px; color: var(--ink); }
.review-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; margin-bottom: 10px; }
.review-grid.small { grid-template-columns: 1fr 1fr; }
.review-field { display: flex; flex-direction: column; gap: 2px; }
.review-field label { font-size: 11px; color: var(--muted); }
.review-field input { font-size: 12px; padding: 4px 8px; border: 1px solid var(--line); border-radius: 10px; }
.review-subsection { margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(74,53,25,0.08); }
.review-subhead { display: flex; align-items: center; justify-content: space-between; font-size: 12px; font-weight: 600; margin-bottom: 4px; color: var(--muted); }
.review-char { background: rgba(255,255,255,0.55); border: 1px solid rgba(74,53,25,0.08); border-radius: 14px; padding: 8px; margin-bottom: 6px; }
.review-char-head { display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 12px; font-weight: 600; }
.review-char input { width: 100%; font-size: 12px; padding: 4px 8px; border: 1px solid var(--line); border-radius: 10px; }
.review-tags { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin-top: 4px; }
.review-tag { display: inline-flex; align-items: center; gap: 2px; background: rgba(109, 139, 87, 0.16); color: #4d6841; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.review-tag input { width: auto; min-width: 60px; font-size: 11px; padding: 1px 4px; border: 1px solid rgba(109, 139, 87, 0.2); border-radius: 8px; background: rgba(255,255,255,0.45); }

/* Stage 2 tag evaluation */
.s2-char-id { background: rgba(255,255,255,0.55); border: 1px solid rgba(74,53,25,0.08); border-radius: 14px; padding: 10px; margin-bottom: 10px; }
.s2-char-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.s2-toggle { font-size: 12px; display: flex; align-items: center; gap: 4px; cursor: pointer; }
.s2-char-detail { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--muted); }
.s2-reason { color: var(--muted); font-style: italic; flex-basis: 100%; }
.s2-tag-list { margin-bottom: 10px; }
.s2-tag-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; font-size: 12px; font-weight: 600; }
.s2-tag-items { max-height: 250px; overflow-y: auto; border: 1px solid rgba(74,53,25,0.08); border-radius: 12px; }
.s2-tag-row { display: flex; align-items: center; gap: 6px; padding: 4px 8px; border-bottom: 1px solid rgba(74,53,25,0.08); font-size: 11px; }
.s2-tag-row:last-child { border-bottom: none; }
.s2-tag-visible { background: rgba(109, 139, 87, 0.14); }
.s2-tag-absent { background: rgba(157, 44, 44, 0.10); opacity: 0.72; }
.s2-tag-uncertain { background: rgba(212, 143, 47, 0.12); }
.s2-tag-name { font-weight: 600; min-width: 120px; color: var(--ink); }
.s2-tag-status { display: flex; gap: 2px; }
.s2-tag-status button { font-size: 10px; padding: 1px 6px; border: 1px solid rgba(74,53,25,0.12); border-radius: 8px; background: linear-gradient(135deg, #fbf4eb, #f2e8db); color: var(--ink); cursor: pointer; }
.s2-tag-status button.on { border-color: transparent; background: linear-gradient(135deg, var(--accent), var(--accent-deep)); color: #fff; }
.s2-tag-reason { color: var(--muted); font-size: 10px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Structured */
.structured-block { margin-bottom: 16px; padding: 12px; border: 1px solid rgba(74,53,25,0.08); border-radius: 18px; background: rgba(255,255,255,0.55); }
.structured-section { margin-bottom: 12px; }
.structured-label { font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 4px; }
.structured-en { font-size: 11px; font-family: Consolas, monospace; white-space: pre-wrap; background: rgba(255,255,255,0.72); padding: 8px; border-radius: 12px; border: 1px solid var(--line); max-height: 150px; overflow-y: auto; }
.tag-list { display: flex; flex-wrap: wrap; gap: 4px; }
.tag-chip { display: inline-flex; align-items: center; gap: 3px; font-size: 11px; background: rgba(109, 139, 87, 0.16); color: #4d6841; padding: 2px 8px; border-radius: 999px; }
.tag-chip.tag-review { background: rgba(212, 143, 47, 0.18); color: #8d5a16; }
.tag-chip.tag-rejected { background: rgba(157, 44, 44, 0.12); color: #9d2c2c; }
.tag-chip.tag-uncertain { background: rgba(212, 143, 47, 0.16); color: #8d5a16; }
.tag-x { background: none; border: none; color: inherit; cursor: pointer; font-size: 14px; line-height: 1; padding: 0; }
.tag-add-btn { background: none; border: none; color: #4d6841; cursor: pointer; font-size: 14px; font-weight: bold; }
.tag-empty { font-size: 11px; color: var(--muted); }
.tag-add-row { display: flex; gap: 6px; }
.tag-input { flex: 1; font-size: 12px; padding: 4px 8px; border: 1px solid var(--line); border-radius: 10px; }

/* Chinese description */
.caption-zh-section { margin-bottom: 16px; padding: 14px; border: 1px solid var(--line); border-radius: 18px; background: var(--panel); box-shadow: 0 14px 30px rgba(87, 58, 25, 0.10); }
.caption-zh-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.caption-zh-header h3 { margin: 0; font-size: 15px; color: var(--ink); }
.mode-switch { display: flex; gap: 4px; }
.mode-switch button { font-size: 11px; padding: 5px 10px; border: 1px solid rgba(74,53,25,0.12); border-radius: 999px; background: linear-gradient(135deg, #fbf4eb, #f2e8db); color: var(--ink); cursor: pointer; }
.mode-switch button.active { background: linear-gradient(135deg, var(--accent), var(--accent-deep)); color: #fff; border-color: transparent; }
.caption-text-content { font-size: 14px; line-height: 1.8; padding: 12px; background: rgba(255,255,255,0.72); border: 1px solid var(--line); border-radius: 14px; min-height: 60px; white-space: pre-wrap; user-select: text; cursor: text; color: var(--ink); }
.error-highlight { background: rgba(157, 44, 44, 0.14); border-bottom: 2px solid #9d2c2c; cursor: pointer; }
.mark-hint { font-size: 11px; color: var(--muted); margin-top: 4px; }
.empty-hint { color: var(--muted); font-style: italic; }
.caption-zh-textarea { width: 100%; font-size: 14px; line-height: 1.8; padding: 12px; border: 1px solid var(--line); border-radius: 14px; resize: vertical; font-family: inherit; color: var(--ink); }

/* Error list */
.error-list { margin-top: 12px; }
.error-list h4 { font-size: 13px; margin: 0 0 8px; color: var(--ink); }
.error-item { border: 1px solid rgba(157, 44, 44, 0.22); border-radius: 14px; padding: 8px 10px; margin-bottom: 6px; background: rgba(157, 44, 44, 0.06); }
.error-item-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.error-idx { font-size: 11px; font-weight: 600; color: #9d2c2c; }
.error-text-preview { font-size: 12px; color: var(--ink); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.error-item-body { display: flex; gap: 6px; }
.error-item-body select { font-size: 11px; padding: 2px 6px; border: 1px solid var(--line); border-radius: 8px; }
.error-note { flex: 1; font-size: 11px; padding: 2px 6px; border: 1px solid var(--line); border-radius: 8px; }
.error-actions { display: flex; gap: 8px; margin-top: 8px; }

/* Bottom gallery strip */
.caption-gallery-strip { flex-shrink: 0; height: 80px; background: var(--panel); border: 1px solid var(--line); border-radius: 18px; box-shadow: 0 14px 30px rgba(87, 58, 25, 0.10); display: flex; align-items: center; gap: 6px; padding: 0 8px; margin-top: 12px; }
.strip-scroll { display: flex; height: 100%; padding: 4px 0; gap: 6px; flex: 1; overflow-x: auto; }
.strip-thumb { flex-shrink: 0; width: 80px; height: 64px; display: flex; align-items: center; justify-content: center; cursor: pointer; border: 2px solid transparent; border-radius: 12px; overflow: hidden; background: linear-gradient(135deg, #f6ecdf, #ead8bf); position: relative; }
.strip-thumb.active { border-color: var(--accent); box-shadow: 0 4px 10px rgba(182, 84, 52, 0.22); }
.strip-thumb img { max-width: 100%; max-height: 100%; object-fit: cover; }
.strip-label { position: absolute; bottom: 0; left: 0; right: 0; font-size: 9px; background: rgba(0,0,0,0.6); color: #fff; padding: 1px 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.page-nav { font-size: 14px; padding: 4px 8px; border: 1px solid rgba(74,53,25,0.12); border-radius: 10px; background: linear-gradient(135deg, #fbf4eb, #f2e8db); color: var(--ink); cursor: pointer; }
.page-nav:disabled { opacity: 0.3; }
.page-info { font-size: 11px; color: var(--muted); min-width: 40px; text-align: center; }

/* Buttons */
button { cursor: pointer; font-size: 12px; padding: 6px 12px; border-radius: 12px; border: 1px solid transparent; }
button.primary { background: linear-gradient(135deg, var(--accent), var(--accent-deep)); color: #fff; border-color: transparent; box-shadow: 0 4px 10px rgba(182, 84, 52, 0.18); }
button.primary:disabled { opacity: 0.5; }
button.secondary { background: linear-gradient(135deg, #fbf4eb, #f2e8db); color: var(--ink); border-color: rgba(74,53,25,0.12); }
button.secondary:disabled { opacity: 0.4; }
button.small { font-size: 10px; padding: 2px 6px; }
button.ghost { background: rgba(182, 84, 52, 0.12); border: none; padding: 2px 6px; color: var(--accent-deep); }
button.ghost:hover { color: var(--ink); }
</style>
