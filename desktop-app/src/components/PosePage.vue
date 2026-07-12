<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import GalleryCalendar from './GalleryCalendar.vue';

const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'webp', 'bmp', 'avif']);
const PAGE_SIZE = 36;

const KEYPOINTS = [
  { id: 'head', zh: '头部' },
  { id: 'neck', zh: '颈部' },
  { id: 'left_shoulder', zh: '左肩' },
  { id: 'right_shoulder', zh: '右肩' },
  { id: 'left_elbow', zh: '左肘' },
  { id: 'right_elbow', zh: '右肘' },
  { id: 'left_wrist', zh: '左手腕' },
  { id: 'right_wrist', zh: '右手腕' },
  { id: 'left_hand', zh: '左手' },
  { id: 'right_hand', zh: '右手' },
  { id: 'left_chest', zh: '左胸部' },
  { id: 'right_chest', zh: '右胸部' },
  { id: 'abdomen', zh: '腹部' },
  { id: 'private_parts', zh: '私处' },
  { id: 'left_hip', zh: '左髋' },
  { id: 'right_hip', zh: '右髋' },
  { id: 'left_knee', zh: '左膝' },
  { id: 'right_knee', zh: '右膝' },
  { id: 'left_ankle', zh: '左脚踝' },
  { id: 'right_ankle', zh: '右脚踝' },
  { id: 'left_foot', zh: '左脚' },
  { id: 'right_foot', zh: '右脚' }
];

const KEYPOINT_LABELS_ZH = Object.fromEntries(KEYPOINTS.map(item => [item.id, item.zh]));

const VISIBILITY_STATES = {
  visible: { zh: '可见', v: 2 },
  occluded: { zh: '画面内遮挡', v: 1 },
  out_of_frame: { zh: '画面外', v: 0 },
  unknown: { zh: '无法判断', v: 0 }
};

const FLAG_OPTIONS = [
  { id: 'truncated', zh: '画面裁切' },
  { id: 'occluded', zh: '人体遮挡' },
  { id: 'tiny', zh: '人物很小' },
  { id: 'uncertain', zh: '归属不确定' }
];

const CONNECTIONS = [
  ['head', 'neck'],
  ['neck', 'left_shoulder'],
  ['neck', 'right_shoulder'],
  ['left_shoulder', 'left_elbow'],
  ['left_elbow', 'left_wrist'],
  ['left_wrist', 'left_hand'],
  ['right_shoulder', 'right_elbow'],
  ['right_elbow', 'right_wrist'],
  ['right_wrist', 'right_hand'],
  ['neck', 'left_chest'],
  ['neck', 'right_chest'],
  ['left_shoulder', 'left_chest'],
  ['right_shoulder', 'right_chest'],
  ['left_chest', 'right_chest'],
  ['left_chest', 'abdomen'],
  ['right_chest', 'abdomen'],
  ['abdomen', 'private_parts'],
  ['private_parts', 'left_hip'],
  ['private_parts', 'right_hip'],
  ['left_hip', 'right_hip'],
  ['left_hip', 'left_knee'],
  ['left_knee', 'left_ankle'],
  ['left_ankle', 'left_foot'],
  ['right_hip', 'right_knee'],
  ['right_knee', 'right_ankle'],
  ['right_ankle', 'right_foot']
];

const PERSON_COLORS = ['#e4572e', '#2d7dd2', '#2e9d57', '#8f5bd5', '#d88c1b', '#0f8b8d', '#c43c68'];

const gallery = ref({
  selectedDate: '',
  availableDates: [],
  availableDateFolders: [],
  today: '',
  images: [],
  page: 1,
  search: '',
  filter: 'todo'
});

const poseByPath = ref({});
const poseByName = ref({});
const loading = ref(false);
const saving = ref(false);
const message = ref('');
const dirty = ref(false);
const currentIndex = ref(0);
const currentUrl = ref('');
const activePersonId = ref('');
const activeKeypointId = ref('head');
const editMode = ref('keypoint');
const imageEl = ref(null);
const imageSize = ref({ naturalWidth: 0, naturalHeight: 0 });
const dragState = ref(null);

const annotation = ref(createEmptyAnnotation());

const currentImage = computed(() => gallery.value.images[currentIndex.value] || null);
const currentPerson = computed(() =>
  annotation.value.people.find(person => person.id === activePersonId.value) || null
);
const currentPoint = computed(() =>
  currentPerson.value?.keypoints?.[activeKeypointId.value] || emptyPoint()
);
const activeKeypointLabel = computed(() =>
  KEYPOINT_LABELS_ZH[activeKeypointId.value] || activeKeypointId.value
);
const overlayViewBox = computed(() =>
  `0 0 ${Math.max(1, imageSize.value.naturalWidth)} ${Math.max(1, imageSize.value.naturalHeight)}`
);

const filteredImages = computed(() => {
  const q = gallery.value.search.trim().toLowerCase();
  const filter = gallery.value.filter;
  return gallery.value.images.filter(item => {
    if (q) {
      const text = `${item.filename || ''} ${item.artist || ''} ${Array.isArray(item.characters) ? item.characters.join(' ') : item.characters || ''}`.toLowerCase();
      if (!text.includes(q)) return false;
    }
    const ann = annotationForImage(item);
    if (filter === 'todo') return ann.status !== 'done';
    if (filter === 'done') return ann.status === 'done';
    return true;
  });
});

const totalPages = computed(() => Math.max(1, Math.ceil(filteredImages.value.length / PAGE_SIZE)));
const pagedImages = computed(() => {
  const start = (gallery.value.page - 1) * PAGE_SIZE;
  return filteredImages.value.slice(start, start + PAGE_SIZE);
});

const doneCount = computed(() => gallery.value.images.filter(item => annotationForImage(item).status === 'done').length);
const annotatedCount = computed(() => gallery.value.images.filter(item => (annotationForImage(item).people || []).length > 0).length);
const activePointStateLabel = computed(() => VISIBILITY_STATES[currentPoint.value.state]?.zh || '无法判断');

function isImageFile(name) {
  const ext = String(name || '').split('.').pop().toLowerCase();
  return IMAGE_EXTS.has(ext);
}

function sortByScore(items) {
  return [...items].sort((a, b) =>
    (b.score || 0) - (a.score || 0) ||
    (b.favCount || 0) - (a.favCount || 0) ||
    String(a.filename || '').localeCompare(String(b.filename || ''))
  );
}

function normalizePath(value) {
  return String(value || '').replace(/\\/g, '/').toLowerCase();
}

function emptyPoint(state = 'unknown') {
  return {
    x: null,
    y: null,
    v: VISIBILITY_STATES[state]?.v ?? 0,
    state
  };
}

function createEmptyKeypoints() {
  return Object.fromEntries(KEYPOINTS.map(item => [item.id, emptyPoint()]));
}

function createEmptyAnnotation() {
  return {
    status: 'unlabeled',
    people: [],
    updated_at: ''
  };
}

function nextPersonId(people) {
  const used = new Set((people || []).map(person => String(person.id || '')));
  let n = 1;
  while (used.has(`p${n}`)) n += 1;
  return `p${n}`;
}

function createPerson() {
  return {
    id: nextPersonId(annotation.value.people),
    bbox: null,
    keypoints: createEmptyKeypoints(),
    flags: []
  };
}

function normalizePoint(raw) {
  if (!raw || typeof raw !== 'object') return emptyPoint();
  const state = VISIBILITY_STATES[raw.state] ? raw.state : (raw.v === 2 ? 'visible' : (raw.v === 1 ? 'occluded' : 'unknown'));
  const v = VISIBILITY_STATES[state]?.v ?? 0;
  const hasCoords = Number.isFinite(Number(raw.x)) && Number.isFinite(Number(raw.y));
  if (v === 0 || !hasCoords) {
    return { x: null, y: null, v, state };
  }
  return {
    x: Number(raw.x),
    y: Number(raw.y),
    v,
    state
  };
}

function normalizeBbox(raw) {
  if (!Array.isArray(raw) || raw.length < 4) return null;
  const nums = raw.slice(0, 4).map(value => Number(value));
  if (nums.some(value => !Number.isFinite(value))) return null;
  const [x1, y1, x2, y2] = nums;
  return [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)];
}

function normalizePerson(raw, index) {
  const keypoints = createEmptyKeypoints();
  for (const item of KEYPOINTS) {
    keypoints[item.id] = normalizePoint(raw?.keypoints?.[item.id]);
  }
  return {
    id: String(raw?.id || `p${index + 1}`),
    bbox: normalizeBbox(raw?.bbox),
    keypoints,
    flags: Array.isArray(raw?.flags) ? raw.flags.filter(flag => typeof flag === 'string') : []
  };
}

function normalizeAnnotation(raw) {
  if (!raw || typeof raw !== 'object') return createEmptyAnnotation();
  const people = Array.isArray(raw.people) ? raw.people.map(normalizePerson) : [];
  return {
    status: ['unlabeled', 'in_progress', 'done'].includes(raw.status) ? raw.status : (people.length ? 'in_progress' : 'unlabeled'),
    people,
    updated_at: String(raw.updated_at || '')
  };
}

function cloneAnnotation(raw) {
  return normalizeAnnotation(JSON.parse(JSON.stringify(raw || createEmptyAnnotation())));
}

function annotationForImage(item) {
  if (!item) return createEmptyAnnotation();
  const byPath = poseByPath.value[normalizePath(item.localPath)];
  if (byPath) return byPath;
  return poseByName.value[item.filename] || createEmptyAnnotation();
}

function countPointsByState(ann, state) {
  let count = 0;
  for (const person of ann.people || []) {
    for (const key of Object.keys(person.keypoints || {})) {
      if (person.keypoints[key]?.state === state) count += 1;
    }
  }
  return count;
}

function imageStatusLabel(item) {
  const ann = annotationForImage(item);
  const peopleCount = ann.people?.length || 0;
  if (ann.status === 'done') return `${peopleCount}人 已完成`;
  if (peopleCount) return `${peopleCount}人 标注中`;
  return '未标注';
}

function imageStatusClass(item) {
  const ann = annotationForImage(item);
  if (ann.status === 'done') return 'done';
  if ((ann.people || []).length) return 'progress';
  return 'todo';
}

function personColor(person) {
  const idx = Math.max(0, annotation.value.people.findIndex(item => item.id === person?.id));
  return PERSON_COLORS[idx % PERSON_COLORS.length];
}

function getPoint(person, id) {
  return person?.keypoints?.[id] || emptyPoint();
}

function pointHasCoords(point) {
  return point && point.v > 0 && Number.isFinite(point.x) && Number.isFinite(point.y);
}

function visiblePoints(person) {
  return KEYPOINTS
    .map(item => ({ ...item, point: getPoint(person, item.id) }))
    .filter(item => pointHasCoords(item.point));
}

function skeletonSegments(person) {
  return CONNECTIONS.map(([from, to]) => {
    const a = getPoint(person, from);
    const b = getPoint(person, to);
    if (!pointHasCoords(a) || !pointHasCoords(b)) return null;
    return { from, to, a, b };
  }).filter(Boolean);
}

function bboxRect(bbox) {
  if (!bbox) return null;
  const [x1, y1, x2, y2] = bbox;
  return { x: x1, y: y1, width: Math.max(0, x2 - x1), height: Math.max(0, y2 - y1) };
}

function stateClass(state) {
  return state || 'unknown';
}

function touchAnnotation() {
  dirty.value = true;
  if (annotation.value.status === 'unlabeled') annotation.value.status = 'in_progress';
}

function setMessage(text) {
  message.value = text;
  if (!text) return;
  window.clearTimeout(setMessage._timer);
  setMessage._timer = window.setTimeout(() => {
    if (message.value === text) message.value = '';
  }, 4200);
}

async function hydrateThumbs(items) {
  await Promise.all(items.map(async item => {
    if (item.thumbUrl || !item.localPath) return;
    try {
      item.thumbUrl = await window.desktopAPI.file.toThumbUrl(item.localPath, 260);
    } catch {
      item.thumbUrl = '';
    }
  }));
}

async function loadPoseIndex(date) {
  const byPath = {};
  const byName = {};
  if (!window.desktopAPI?.pose?.listForDate) {
    setMessage('姿态接口未加载，请重启桌面端');
    poseByPath.value = byPath;
    poseByName.value = byName;
    return;
  }
  try {
    const entries = await window.desktopAPI.pose.listForDate(date);
    for (const entry of Array.isArray(entries) ? entries : []) {
      const ann = normalizeAnnotation(entry.annotation);
      if (entry.localPath) byPath[normalizePath(entry.localPath)] = ann;
      if (entry.filename && !byName[entry.filename]) byName[entry.filename] = ann;
    }
  } catch (error) {
    setMessage(`读取 pose.json 失败: ${error.message}`);
  }
  poseByPath.value = byPath;
  poseByName.value = byName;
}

async function loadGallery(date = gallery.value.selectedDate) {
  if (dirty.value) {
    const ok = await saveCurrent(false);
    if (!ok) return;
  }
  loading.value = true;
  try {
    const data = await window.desktopAPI.gallery.getByDate(date || gallery.value.selectedDate);
    const images = sortByScore((data.images || [])
      .filter(item => isImageFile(item.filename))
      .map(item => ({
        ...item,
        thumbUrl: '',
        charactersText: Array.isArray(item.characters) ? item.characters.join(' ') : (item.characters || '')
    })));
    gallery.value.selectedDate = data.selectedDate;
    gallery.value.availableDates = Array.isArray(data.availableDates) ? data.availableDates : [];
    gallery.value.availableDateFolders = Array.isArray(data.availableDateFolders) ? data.availableDateFolders : [];
    gallery.value.today = data.today || gallery.value.today;
    gallery.value.images = images;
    gallery.value.page = 1;
    currentIndex.value = 0;
    await loadPoseIndex(data.selectedDate);
    await hydrateThumbs(images.slice(0, PAGE_SIZE));
    await switchToImage(0, true);
  } catch (error) {
    setMessage(`加载图库失败: ${error.message}`);
  } finally {
    loading.value = false;
  }
}

async function switchToImage(index, skipSave = false) {
  if (!skipSave && dirty.value) {
    const ok = await saveCurrent(false);
    if (!ok) return;
  }
  const bounded = Math.max(0, Math.min(index, gallery.value.images.length - 1));
  currentIndex.value = bounded;
  const item = gallery.value.images[bounded];
  imageSize.value = { naturalWidth: 0, naturalHeight: 0 };
  currentUrl.value = '';
  if (!item) {
    annotation.value = createEmptyAnnotation();
    activePersonId.value = '';
    dirty.value = false;
    return;
  }
  annotation.value = cloneAnnotation(annotationForImage(item));
  activePersonId.value = annotation.value.people[0]?.id || '';
  activeKeypointId.value = 'head';
  dirty.value = false;
  try {
    currentUrl.value = await window.desktopAPI.file.toLocalUrl(item.localPath);
  } catch {
    currentUrl.value = '';
  }
  await nextTick();
}

async function selectImage(item) {
  const index = gallery.value.images.findIndex(candidate => (candidate.localPath || candidate.filename) === (item.localPath || item.filename));
  if (index >= 0) await switchToImage(index);
}

async function prevImage() {
  if (currentIndex.value > 0) await switchToImage(currentIndex.value - 1);
}

async function nextImage() {
  if (currentIndex.value < gallery.value.images.length - 1) await switchToImage(currentIndex.value + 1);
}

function onImageLoad(event) {
  imageSize.value = {
    naturalWidth: event.target.naturalWidth || 1,
    naturalHeight: event.target.naturalHeight || 1
  };
}

function toImagePoint(event) {
  const img = imageEl.value;
  if (!img) return null;
  const rect = img.getBoundingClientRect();
  if (!rect.width || !rect.height || !imageSize.value.naturalWidth || !imageSize.value.naturalHeight) return null;
  const x = Math.max(0, Math.min(imageSize.value.naturalWidth, (event.clientX - rect.left) * imageSize.value.naturalWidth / rect.width));
  const y = Math.max(0, Math.min(imageSize.value.naturalHeight, (event.clientY - rect.top) * imageSize.value.naturalHeight / rect.height));
  return { x, y };
}

function roundCoord(value) {
  return Number(Number(value).toFixed(2));
}

function ensureActivePerson() {
  if (currentPerson.value) return currentPerson.value;
  const person = createPerson();
  annotation.value.people.push(person);
  activePersonId.value = person.id;
  activeKeypointId.value = 'head';
  editMode.value = 'keypoint';
  touchAnnotation();
  return person;
}

function addPerson() {
  const person = createPerson();
  annotation.value.people.push(person);
  activePersonId.value = person.id;
  activeKeypointId.value = 'head';
  editMode.value = 'keypoint';
  annotation.value.status = 'in_progress';
  dirty.value = true;
  setMessage(`${person.id} 已新增，从头部开始标注`);
}

function deleteActivePerson() {
  if (!currentPerson.value) return;
  const idx = annotation.value.people.findIndex(person => person.id === activePersonId.value);
  if (idx < 0) return;
  annotation.value.people.splice(idx, 1);
  activePersonId.value = annotation.value.people[Math.max(0, idx - 1)]?.id || annotation.value.people[0]?.id || '';
  touchAnnotation();
}

function selectPerson(id) {
  activePersonId.value = id;
}

function advanceActiveKeypoint() {
  const idx = KEYPOINTS.findIndex(item => item.id === activeKeypointId.value);
  const next = KEYPOINTS[idx + 1];
  if (next) {
    activeKeypointId.value = next.id;
  } else {
    setMessage('已到最后一个关键点');
  }
}

function placeActiveKeypoint(event) {
  const point = toImagePoint(event);
  if (!point) return;
  const person = ensureActivePerson();
  const label = activeKeypointLabel.value;
  person.keypoints[activeKeypointId.value] = {
    x: roundCoord(point.x),
    y: roundCoord(point.y),
    v: 2,
    state: 'visible'
  };
  touchAnnotation();
  advanceActiveKeypoint();
  setMessage(`${label} 已标注`);
}

function setActivePointState(state) {
  const person = ensureActivePerson();
  const target = person.keypoints[activeKeypointId.value] || emptyPoint();
  const v = VISIBILITY_STATES[state]?.v ?? 0;
  if (v === 0) {
    person.keypoints[activeKeypointId.value] = { x: null, y: null, v, state };
  } else if (pointHasCoords(target)) {
    person.keypoints[activeKeypointId.value] = { ...target, v, state };
  } else {
    setMessage('先在画面内放置该关键点');
    return;
  }
  touchAnnotation();
}

function clearActiveKeypoint() {
  const person = currentPerson.value;
  if (!person) return;
  person.keypoints[activeKeypointId.value] = emptyPoint();
  touchAnnotation();
}

function skipActiveKeypoint(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  if (editMode.value !== 'keypoint') return;
  const person = ensureActivePerson();
  const label = activeKeypointLabel.value;
  person.keypoints[activeKeypointId.value] = emptyPoint('out_of_frame');
  touchAnnotation();
  advanceActiveKeypoint();
  setMessage(`${label} 已标为画面外`);
}

function toggleFlag(flag) {
  const person = ensureActivePerson();
  const set = new Set(person.flags || []);
  if (set.has(flag)) set.delete(flag);
  else set.add(flag);
  person.flags = Array.from(set);
  touchAnnotation();
}

function onCanvasPointerDown(event) {
  if (!currentImage.value || event.button !== 0) return;
  if (editMode.value === 'keypoint') {
    placeActiveKeypoint(event);
    return;
  }
  const point = toImagePoint(event);
  if (!point) return;
  const person = ensureActivePerson();
  const svg = event.currentTarget;
  svg.setPointerCapture?.(event.pointerId);
  person.bbox = [roundCoord(point.x), roundCoord(point.y), roundCoord(point.x), roundCoord(point.y)];
  dragState.value = { type: 'bbox', personId: person.id, start: point };
  touchAnnotation();
}

function onCanvasPointerMove(event) {
  if (!dragState.value) return;
  const point = toImagePoint(event);
  if (!point) return;
  if (dragState.value.type === 'bbox') {
    const person = annotation.value.people.find(item => item.id === dragState.value.personId);
    if (!person) return;
    const start = dragState.value.start;
    person.bbox = normalizeBbox([start.x, start.y, point.x, point.y]);
    touchAnnotation();
  } else if (dragState.value.type === 'keypoint') {
    const person = annotation.value.people.find(item => item.id === dragState.value.personId);
    if (!person) return;
    person.keypoints[dragState.value.keypointId] = {
      x: roundCoord(point.x),
      y: roundCoord(point.y),
      v: person.keypoints[dragState.value.keypointId]?.v || 2,
      state: person.keypoints[dragState.value.keypointId]?.state || 'visible'
    };
    touchAnnotation();
  }
}

function onCanvasPointerUp(event) {
  event.currentTarget?.releasePointerCapture?.(event.pointerId);
  if (dragState.value?.type === 'bbox' && currentPerson.value?.bbox) {
    const [x1, y1, x2, y2] = currentPerson.value.bbox;
    if (Math.abs(x2 - x1) < 4 || Math.abs(y2 - y1) < 4) currentPerson.value.bbox = null;
  }
  dragState.value = null;
}

function startKeypointDrag(event, personId, keypointId) {
  if (event.button !== 0) return;
  event.stopPropagation();
  const svg = event.currentTarget.ownerSVGElement;
  svg?.setPointerCapture?.(event.pointerId);
  activePersonId.value = personId;
  activeKeypointId.value = keypointId;
  dragState.value = { type: 'keypoint', personId, keypointId };
}

function setStatus(status) {
  annotation.value.status = status;
  dirty.value = true;
}

function serializePoint(point) {
  const state = VISIBILITY_STATES[point?.state] ? point.state : 'unknown';
  const v = VISIBILITY_STATES[state].v;
  if (v === 0) return { x: null, y: null, v, state };
  if (!pointHasCoords(point)) return { x: null, y: null, v: 0, state: 'unknown' };
  return {
    x: roundCoord(point.x),
    y: roundCoord(point.y),
    v,
    state
  };
}

function serializeAnnotation() {
  return {
    status: annotation.value.status,
    people: annotation.value.people.map(person => ({
      id: person.id,
      bbox: normalizeBbox(person.bbox),
      keypoints: Object.fromEntries(KEYPOINTS.map(item => [item.id, serializePoint(person.keypoints?.[item.id])])),
      flags: Array.isArray(person.flags) ? person.flags.filter(flag => typeof flag === 'string') : []
    }))
  };
}

async function saveCurrent(showToast = true) {
  if (!currentImage.value) {
    setMessage('未选择图片，无法保存');
    return false;
  }
  if (!currentImage.value.localPath) {
    setMessage('当前图片没有本地路径，无法保存');
    return false;
  }
  if (!window.desktopAPI?.pose?.save) {
    setMessage('姿态保存接口未加载，请重启桌面端后再保存');
    return false;
  }
  saving.value = true;
  try {
    const payload = serializeAnnotation();
    const result = await window.desktopAPI.pose.save(currentImage.value.localPath, payload);
    if (!result?.ok) {
      setMessage(`保存失败: ${result?.error || '未知错误'}`);
      return false;
    }
    const saved = normalizeAnnotation({ ...payload, updated_at: new Date().toISOString() });
    poseByPath.value = { ...poseByPath.value, [normalizePath(currentImage.value.localPath)]: saved };
    poseByName.value = { ...poseByName.value, [currentImage.value.filename]: saved };
    dirty.value = false;
    if (showToast) setMessage(`已保存到 ${result.path || 'pose.json'}`);
    return true;
  } catch (error) {
    setMessage(`保存失败: ${error.message}`);
    return false;
  } finally {
    saving.value = false;
  }
}

async function markDone() {
  annotation.value.status = 'done';
  dirty.value = true;
  await saveCurrent(true);
}

function setPage(page) {
  gallery.value.page = Math.max(1, Math.min(totalPages.value, page));
  hydrateThumbs(pagedImages.value);
}

function onKeyDown(event) {
  const tag = document.activeElement?.tagName?.toLowerCase();
  if (tag === 'input' || tag === 'select' || tag === 'textarea' || document.activeElement?.isContentEditable) return;
  const key = event.key.toLowerCase();
  if (event.ctrlKey && key === 's') {
    event.preventDefault();
    saveCurrent(true);
    return;
  }
  if (key === 'a') { event.preventDefault(); prevImage(); }
  else if (key === 'd') { event.preventDefault(); nextImage(); }
  else if (key === 'n') { event.preventDefault(); addPerson(); }
  else if (key === 'tab') {
    event.preventDefault();
    if (!annotation.value.people.length) return;
    const idx = annotation.value.people.findIndex(person => person.id === activePersonId.value);
    const next = annotation.value.people[(idx + 1) % annotation.value.people.length];
    activePersonId.value = next.id;
  } else if (key === 'b') { editMode.value = 'bbox'; }
  else if (key === 'k') { editMode.value = 'keypoint'; }
  else if (key === 'v') { setActivePointState('visible'); }
  else if (key === 'o') { setActivePointState('occluded'); }
  else if (key === 'f') { setActivePointState('out_of_frame'); }
  else if (key === 'u') { setActivePointState('unknown'); }
  else if (key === ' ') { event.preventDefault(); markDone(); }
  else if (key === 'delete' || key === 'backspace') {
    event.preventDefault();
    if (pointHasCoords(currentPoint.value) || currentPoint.value.state !== 'unknown') clearActiveKeypoint();
    else deleteActivePerson();
  } else if (/^[1-9]$/.test(key)) {
    const idx = Number(key) - 1;
    if (KEYPOINTS[idx]) activeKeypointId.value = KEYPOINTS[idx].id;
  }
}

onMounted(async () => {
  await loadGallery();
  window.addEventListener('keydown', onKeyDown);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown);
});
</script>

<template>
  <div class="pose-layout">
    <aside class="panel card pose-browser">
      <div class="panel-head">
        <div>
          <span class="eyebrow">Pose</span>
          <h2>姿态</h2>
        </div>
        <button class="secondary" :disabled="loading" @click="loadGallery(gallery.selectedDate)">刷新</button>
      </div>

      <GalleryCalendar
        :available-dates="gallery.availableDates"
        :date-folders="gallery.availableDateFolders"
        :available-tags="[]"
        :selected-date="gallery.selectedDate"
        :today="gallery.today"
        @select="loadGallery"
      />

      <div class="pose-stats">
        <span>{{ doneCount }}/{{ gallery.images.length }} 完成</span>
        <span>{{ annotatedCount }} 已有人物</span>
      </div>

      <input v-model="gallery.search" class="search-input" type="text" placeholder="搜索文件 / 画师 / 角色">
      <div class="pose-filter-row">
        <button :class="['secondary', { active: gallery.filter === 'todo' }]" @click="gallery.filter = 'todo'; setPage(1)">未完成</button>
        <button :class="['secondary', { active: gallery.filter === 'done' }]" @click="gallery.filter = 'done'; setPage(1)">已完成</button>
        <button :class="['secondary', { active: gallery.filter === 'all' }]" @click="gallery.filter = 'all'; setPage(1)">全部</button>
      </div>

      <div class="pose-image-list">
        <button
          v-for="item in pagedImages"
          :key="item.localPath || item.filename"
          :class="['pose-image-row', { active: currentImage && (currentImage.localPath || currentImage.filename) === (item.localPath || item.filename) }]"
          @click="selectImage(item)"
          @mouseenter="hydrateThumbs([item])"
        >
          <img v-if="item.thumbUrl" :src="item.thumbUrl" alt="">
          <span v-else class="pose-thumb-placeholder">{{ (item.filename || '?').split('.').pop() }}</span>
          <span class="pose-row-main">
            <strong>{{ item.filename }}</strong>
            <small>score {{ item.score || 0 }} · {{ imageStatusLabel(item) }}</small>
          </span>
          <span :class="['pose-row-status', imageStatusClass(item)]"></span>
        </button>
      </div>

      <div class="pagination-bar">
        <button class="secondary" :disabled="gallery.page <= 1" @click="setPage(gallery.page - 1)">上一页</button>
        <span>{{ gallery.page }} / {{ totalPages }}</span>
        <button class="secondary" :disabled="gallery.page >= totalPages" @click="setPage(gallery.page + 1)">下一页</button>
      </div>
    </aside>

    <main class="panel pose-workspace">
      <div v-if="message" class="pose-toast">{{ message }}</div>

      <div class="pose-topbar">
        <div class="pose-compact-meta">
          <span>{{ gallery.selectedDate || 'Date' }}</span>
          <em>{{ gallery.images.length ? currentIndex + 1 : 0 }} / {{ gallery.images.length }}</em>
        </div>
        <div class="pose-actions">
          <button class="secondary" :disabled="currentIndex <= 0" @click="prevImage">上一张</button>
          <button class="secondary" :disabled="currentIndex >= gallery.images.length - 1" @click="nextImage">下一张</button>
          <button :class="['secondary', { active: editMode === 'keypoint' }]" @click="editMode = 'keypoint'">关键点</button>
          <button :class="['secondary', { active: editMode === 'bbox' }]" @click="editMode = 'bbox'">框选</button>
          <button class="ghost" :disabled="!currentImage || saving" @click="saveCurrent(true)">{{ saving ? '保存中' : '保存' }}</button>
          <button :disabled="!currentImage || saving" @click="markDone">完成</button>
        </div>
      </div>

      <div class="pose-target-banner">
        <span>正在标注</span>
        <strong>{{ activeKeypointLabel }}</strong>
        <em>{{ currentPerson?.id || '自动新增人物' }}</em>
        <small>左键放点，右键标为画面外并跳过</small>
      </div>

      <div class="pose-stage">
        <div v-if="currentImage && currentUrl" class="pose-image-frame">
          <img ref="imageEl" class="pose-main-image" :src="currentUrl" alt="" @load="onImageLoad">
          <svg
            class="pose-overlay"
            :viewBox="overlayViewBox"
            preserveAspectRatio="none"
            @pointerdown="onCanvasPointerDown"
            @pointermove="onCanvasPointerMove"
            @pointerup="onCanvasPointerUp"
            @pointerleave="onCanvasPointerUp"
            @contextmenu.prevent="skipActiveKeypoint"
          >
            <g
              v-for="person in annotation.people"
              :key="person.id"
              :class="['pose-person-layer', { active: person.id === activePersonId }]"
              :style="{ '--person-color': personColor(person) }"
            >
              <rect
                v-if="bboxRect(person.bbox)"
                class="pose-bbox"
                :x="bboxRect(person.bbox).x"
                :y="bboxRect(person.bbox).y"
                :width="bboxRect(person.bbox).width"
                :height="bboxRect(person.bbox).height"
                @pointerdown.stop="selectPerson(person.id)"
              />
              <text
                v-if="bboxRect(person.bbox)"
                class="pose-person-label"
                :x="bboxRect(person.bbox).x + 8"
                :y="Math.max(18, bboxRect(person.bbox).y + 18)"
              >{{ person.id }}</text>
              <line
                v-for="seg in skeletonSegments(person)"
                :key="`${person.id}-${seg.from}-${seg.to}`"
                class="pose-bone"
                :x1="seg.a.x"
                :y1="seg.a.y"
                :x2="seg.b.x"
                :y2="seg.b.y"
              />
              <circle
                v-for="kp in visiblePoints(person)"
                :key="`${person.id}-${kp.id}`"
                :class="['pose-point', stateClass(kp.point.state), { active: person.id === activePersonId && kp.id === activeKeypointId }]"
                :cx="kp.point.x"
                :cy="kp.point.y"
                r="7"
                @pointerdown="startKeypointDrag($event, person.id, kp.id)"
              />
            </g>
          </svg>
        </div>
        <div v-else class="gallery-empty">
          <span>{{ loading ? '读取中' : '没有可标注图片' }}</span>
        </div>
      </div>

      <div class="pose-statusbar">
        <span>{{ dirty ? '有未保存改动' : '已同步' }}</span>
        <span>当前：{{ currentPerson?.id || '无人物' }} · {{ activeKeypointLabel }} · {{ activePointStateLabel }}</span>
        <span v-if="message">{{ message }}</span>
      </div>
    </main>

    <aside class="panel card pose-inspector">
      <div class="panel-head">
        <div>
          <span class="eyebrow">People</span>
          <h2>人物</h2>
        </div>
        <button @click="addPerson">新增</button>
      </div>

      <div class="pose-people-list">
        <button
          v-for="person in annotation.people"
          :key="person.id"
          :class="['secondary', { active: person.id === activePersonId }]"
          :style="{ '--person-color': personColor(person) }"
          @click="selectPerson(person.id)"
        >
          <span class="pose-person-dot"></span>
          {{ person.id }}
          <small>{{ visiblePoints(person).length }}/{{ KEYPOINTS.length }}</small>
        </button>
      </div>

      <div class="button-row compact">
        <button class="secondary" :disabled="!currentPerson" @click="editMode = 'bbox'">重框</button>
        <button class="ghost" :disabled="!currentPerson" @click="deleteActivePerson">删除人物</button>
      </div>

      <div class="pose-fieldset">
        <h3>状态</h3>
        <div class="button-row compact">
          <button :class="['secondary', { active: annotation.status === 'unlabeled' }]" @click="setStatus('unlabeled')">未标</button>
          <button :class="['secondary', { active: annotation.status === 'in_progress' }]" @click="setStatus('in_progress')">标注中</button>
          <button :class="['secondary', { active: annotation.status === 'done' }]" @click="setStatus('done')">完成</button>
        </div>
      </div>

      <div class="pose-fieldset">
        <h3>标记</h3>
        <div class="pose-flag-grid">
          <button
            v-for="flag in FLAG_OPTIONS"
            :key="flag.id"
            :class="['secondary', { active: currentPerson?.flags?.includes(flag.id) }]"
            :disabled="!currentPerson"
            @click="toggleFlag(flag.id)"
          >{{ flag.zh }}</button>
        </div>
      </div>

      <div class="pose-fieldset pose-active-keypoint-panel">
        <h3>当前：{{ activeKeypointLabel }}</h3>
        <div class="pose-state-grid">
          <button class="secondary" @click="setActivePointState('visible')">可见</button>
          <button class="secondary" @click="setActivePointState('occluded')">遮挡</button>
          <button class="secondary" @click="setActivePointState('out_of_frame')">画面外</button>
          <button class="secondary" @click="setActivePointState('unknown')">无法判断</button>
        </div>
        <button class="ghost pose-wide-btn" :disabled="!currentPerson" @click="clearActiveKeypoint">清除当前点</button>
      </div>

      <div class="pose-fieldset">
        <h3>关键点</h3>
        <div class="pose-keypoint-list">
          <button
            v-for="(kp, idx) in KEYPOINTS"
            :key="kp.id"
            :class="['pose-keypoint-row', { active: activeKeypointId === kp.id }]"
            @click="activeKeypointId = kp.id"
          >
            <span class="pose-key-index">{{ idx + 1 }}</span>
            <span>
              <strong>{{ kp.zh }}</strong>
              <small>{{ kp.id }}</small>
            </span>
            <em :class="stateClass(currentPerson?.keypoints?.[kp.id]?.state || 'unknown')">
              {{ VISIBILITY_STATES[currentPerson?.keypoints?.[kp.id]?.state || 'unknown'].zh }}
            </em>
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>
