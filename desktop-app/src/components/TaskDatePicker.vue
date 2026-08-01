<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '选择日期' },
  clearable: { type: Boolean, default: true },
  availableDates: { type: Array, default: () => [] },
  dateFolders: { type: Array, default: () => [] }
});

const emit = defineEmits(['update:modelValue']);

const rootEl = ref(null);
const panelEl = ref(null);
const open = ref(false);
const dateView = ref('days');
const currentYear = ref(0);
const currentMonth = ref(0);
const panelStyle = ref({});

function today() {
  const d = new Date();
  return formatDate(d.getFullYear(), d.getMonth() + 1, d.getDate());
}

function yesterday() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return formatDate(d.getFullYear(), d.getMonth() + 1, d.getDate());
}

function parseDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
  if (!match) return null;
  return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
}

function formatDate(year, month, day) {
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function toFiniteCount(value, fallback = 0) {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? count : fallback;
}

const dateFolderRecords = computed(() => {
  const records = new Map();
  for (const item of props.dateFolders || []) {
    const date = item?.date || item?.folder;
    if (!parseDate(date)) continue;
    const hasCount = item.imageCount != null || item.image_count != null || item.count != null;
    const imageCount = hasCount ? toFiniteCount(item.imageCount ?? item.image_count ?? item.count) : null;
    records.set(date, {
      date,
      imageCount,
      sourceCount: toFiniteCount(item.sourceCount ?? item.source_count, 1),
      hasImages: hasCount ? imageCount > 0 : Boolean(item.hasImages ?? item.has_images ?? true),
      countKnown: hasCount
    });
  }
  for (const value of props.availableDates || []) {
    if (!parseDate(value) || records.has(value)) continue;
    records.set(value, {
      date: value,
      imageCount: null,
      sourceCount: 1,
      hasImages: true,
      countKnown: false
    });
  }
  return Array.from(records.values()).sort((a, b) => b.date.localeCompare(a.date));
});

const dateFolderMap = computed(() => {
  const out = new Map();
  for (const item of dateFolderRecords.value) out.set(item.date, item);
  return out;
});

function syncMonth() {
  const parsed = parseDate(props.modelValue) || parseDate(today());
  currentYear.value = parsed.year;
  currentMonth.value = parsed.month;
}

watch(() => props.modelValue, syncMonth, { immediate: true });
watch(open, value => {
  if (value) {
    syncMonth();
    dateView.value = 'days';
    nextTick(positionPanel);
    window.addEventListener('resize', positionPanel);
    window.addEventListener('scroll', positionPanel, true);
  } else {
    window.removeEventListener('resize', positionPanel);
    window.removeEventListener('scroll', positionPanel, true);
  }
});
watch(dateView, () => {
  if (open.value) nextTick(positionPanel);
});

const triggerLabel = computed(() => props.modelValue || props.placeholder);
const yearBlockStart = computed(() => Math.floor((currentYear.value || new Date().getFullYear()) / 12) * 12);

const cells = computed(() => {
  if (!currentYear.value || !currentMonth.value) return [];
  const first = new Date(currentYear.value, currentMonth.value - 1, 1);
  const firstWeekday = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(currentYear.value, currentMonth.value, 0).getDate();
  const prevMonthDays = new Date(currentYear.value, currentMonth.value - 1, 0).getDate();
  const result = [];

  for (let i = firstWeekday - 1; i >= 0; i -= 1) {
    const year = currentMonth.value === 1 ? currentYear.value - 1 : currentYear.value;
    const month = currentMonth.value === 1 ? 12 : currentMonth.value - 1;
    result.push({ year, month, day: prevMonthDays - i, otherMonth: true });
  }
  for (let day = 1; day <= daysInMonth; day += 1) {
    result.push({ year: currentYear.value, month: currentMonth.value, day, otherMonth: false });
  }
  while (result.length < 42) {
    const day = result.length - (firstWeekday + daysInMonth) + 1;
    const year = currentMonth.value === 12 ? currentYear.value + 1 : currentYear.value;
    const month = currentMonth.value === 12 ? 1 : currentMonth.value + 1;
    result.push({ year, month, day, otherMonth: true });
  }

  const selected = props.modelValue;
  const todayValue = today();
  return result.map(item => {
    const date = formatDate(item.year, item.month, item.day);
    const folder = dateFolderMap.value.get(date) || null;
    return {
      ...item,
      date,
      hasFolder: !!folder,
      hasImages: !!folder?.hasImages,
      countKnown: !!folder?.countKnown,
      imageCount: folder?.imageCount ?? null,
      selected: date === selected,
      today: date === todayValue
    };
  });
});

const monthItems = computed(() => {
  const selected = parseDate(props.modelValue);
  const todayValue = parseDate(today());
  return Array.from({ length: 12 }, (_, idx) => {
    const month = idx + 1;
    const prefix = `${String(currentYear.value).padStart(4, '0')}-${String(month).padStart(2, '0')}-`;
    const folders = dateFolderRecords.value.filter(item => item.date.startsWith(prefix));
    const imageCount = folders.reduce((sum, item) => sum + (item.imageCount || 0), 0);
    return {
      month,
      label: `${month}月`,
      folderCount: folders.length,
      imageCount,
      hasImages: folders.some(item => item.hasImages),
      countKnown: folders.some(item => item.countKnown),
      selected: !!selected && selected.year === currentYear.value && selected.month === month,
      today: todayValue.year === currentYear.value && todayValue.month === month
    };
  });
});

const yearItems = computed(() => {
  const selected = parseDate(props.modelValue);
  const todayValue = parseDate(today());
  return Array.from({ length: 12 }, (_, idx) => {
    const year = yearBlockStart.value + idx;
    const prefix = `${String(year).padStart(4, '0')}-`;
    const folders = dateFolderRecords.value.filter(item => item.date.startsWith(prefix));
    const imageCount = folders.reduce((sum, item) => sum + (item.imageCount || 0), 0);
    return {
      year,
      folderCount: folders.length,
      imageCount,
      hasImages: folders.some(item => item.hasImages),
      countKnown: folders.some(item => item.countKnown),
      selected: !!selected && selected.year === year,
      today: todayValue.year === year
    };
  });
});

function prevMonth() {
  if (currentMonth.value === 1) {
    currentMonth.value = 12;
    currentYear.value -= 1;
  } else {
    currentMonth.value -= 1;
  }
}

function nextMonth() {
  if (currentMonth.value === 12) {
    currentMonth.value = 1;
    currentYear.value += 1;
  } else {
    currentMonth.value += 1;
  }
}

function prevYear() {
  currentYear.value -= 1;
}

function nextYear() {
  currentYear.value += 1;
}

function prevYearBlock() {
  currentYear.value -= 12;
}

function nextYearBlock() {
  currentYear.value += 12;
}

function toggleDateView() {
  dateView.value = dateView.value === 'days' ? 'months' : (dateView.value === 'months' ? 'years' : 'days');
}

function pickMonth(month) {
  currentMonth.value = month;
  dateView.value = 'days';
}

function pickYear(year) {
  currentYear.value = year;
  dateView.value = 'months';
}

function imageCountLabel(count) {
  if (count == null) return '';
  if (count > 999) return '999+';
  return String(count);
}

function dateCellTitle(cell) {
  if (!cell.hasFolder) return cell.date;
  if (!cell.countKnown) return `${cell.date} · 已有日期文件夹`;
  return cell.hasImages ? `${cell.date} · 有图片` : `${cell.date} · 文件夹为空`;
}

function periodTitle(label, item) {
  if (!item.folderCount) return label;
  if (!item.countKnown) return `${label} · ${item.folderCount} 个日期文件夹`;
  return item.hasImages
    ? `${label} · ${item.folderCount} 个日期`
    : `${label} · ${item.folderCount} 个日期 · 文件夹为空`;
}

function jumpLatestGalleryDate() {
  const latest = dateFolderRecords.value[0]?.date;
  if (!latest) return;
  jumpMonth(latest);
}

function selectDate(date) {
  emit('update:modelValue', date);
  open.value = false;
}

function clearDate() {
  emit('update:modelValue', '');
  open.value = false;
}

function jumpMonth(date) {
  const parsed = parseDate(date);
  if (!parsed) return;
  currentYear.value = parsed.year;
  currentMonth.value = parsed.month;
  dateView.value = 'days';
}

function positionPanel() {
  if (!open.value || !rootEl.value) return;
  const trigger = rootEl.value.querySelector('.task-date-trigger');
  if (!trigger) return;
  const triggerRect = trigger.getBoundingClientRect();
  const boundaryRect = rootEl.value.closest('.control-panel')?.getBoundingClientRect();
  const margin = 8;
  const boundaryLeft = Math.max(margin, boundaryRect ? boundaryRect.left + margin : margin);
  const boundaryRight = Math.min(window.innerWidth - margin, boundaryRect ? boundaryRect.right - margin : window.innerWidth - margin);
  const availableWidth = Math.max(220, boundaryRight - boundaryLeft);
  const width = Math.min(268, availableWidth);
  let left = triggerRect.left;
  if (left + width > boundaryRight) left = boundaryRight - width;
  if (left < boundaryLeft) left = boundaryLeft;

  const panelHeight = panelEl.value?.getBoundingClientRect().height || 330;
  const belowTop = triggerRect.bottom + 6;
  const aboveTop = triggerRect.top - panelHeight - 6;
  const top = belowTop + panelHeight > window.innerHeight - margin && aboveTop > margin
    ? aboveTop
    : Math.min(belowTop, window.innerHeight - panelHeight - margin);

  panelStyle.value = {
    position: 'fixed',
    left: `${Math.round(left)}px`,
    top: `${Math.max(margin, Math.round(top))}px`,
    width: `${Math.round(width)}px`
  };
}

function toggleOpen() {
  open.value = !open.value;
}

function onDocumentPointerDown(event) {
  if (!open.value || rootEl.value?.contains(event.target) || panelEl.value?.contains(event.target)) return;
  open.value = false;
}

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown);
});

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown);
  window.removeEventListener('resize', positionPanel);
  window.removeEventListener('scroll', positionPanel, true);
});
</script>

<template>
  <div ref="rootEl" class="calendar task-date-picker">
    <div class="calendar-toolbar task-date-toolbar">
      <button
        type="button"
        class="calendar-trigger task-date-trigger"
        :class="{ empty: !modelValue }"
        @click="toggleOpen"
      >
        <span class="trigger-text">{{ triggerLabel }}</span>
        <span class="trigger-caret">▾</span>
      </button>
    </div>

    <Teleport to="body">
    <div
      v-if="open"
      ref="panelEl"
      class="calendar-panel task-date-panel"
      :style="panelStyle"
      @click.stop
      @keydown.esc="open = false"
    >
      <div class="calendar-header task-date-header">
        <button type="button" class="small-square" title="上一年" @click="prevYear">«</button>
        <button type="button" class="small-square" title="上一月" @click="prevMonth">‹</button>
        <button type="button" class="date-heading-btn" title="切换年/月选择" @click="toggleDateView">
          <strong v-if="dateView === 'days'">{{ currentYear }}-{{ String(currentMonth).padStart(2, '0') }}</strong>
          <strong v-else-if="dateView === 'months'">{{ currentYear }}</strong>
          <strong v-else>{{ yearBlockStart }}-{{ yearBlockStart + 11 }}</strong>
        </button>
        <button type="button" class="small-square" title="下一月" @click="nextMonth">›</button>
        <button type="button" class="small-square" title="下一年" @click="nextYear">»</button>
      </div>

      <div class="date-quickbar task-date-quickbar">
        <button type="button" class="date-quick" @click="selectDate(yesterday())">昨天</button>
        <button type="button" class="date-quick" @click="selectDate(today())">今天</button>
        <button type="button" class="date-quick" @click="jumpMonth(today())">本月</button>
        <button type="button" class="date-quick" :disabled="!dateFolderRecords.length" @click="jumpLatestGalleryDate">最新目录</button>
        <button type="button" class="date-quick" @click="dateView = 'months'">选月份</button>
        <button type="button" class="date-quick" @click="dateView = 'years'">选年份</button>
      </div>

      <template v-if="dateView === 'days'">
        <div class="calendar-week">
          <span v-for="item in ['一','二','三','四','五','六','日']" :key="item">{{ item }}</span>
        </div>
        <div class="calendar-grid">
          <button
            v-for="cell in cells"
            :key="cell.date"
            type="button"
            class="day-cell"
            :class="{ selected: cell.selected, other: cell.otherMonth, today: cell.today, 'has-folder': cell.hasFolder, 'has-images': cell.hasImages, 'empty-folder': cell.hasFolder && cell.countKnown && !cell.hasImages }"
            :title="dateCellTitle(cell)"
            @click="selectDate(cell.date)"
          >
            <span>{{ cell.day }}</span>
          </button>
        </div>
      </template>

      <div v-else-if="dateView === 'months'" class="month-grid">
        <button
          v-for="item in monthItems"
          :key="item.month"
          type="button"
          class="month-cell"
          :class="{ selected: item.selected, today: item.today, 'has-folder': item.folderCount, 'has-images': item.hasImages, 'empty-folder': item.folderCount && item.countKnown && !item.hasImages }"
          :title="periodTitle(`${currentYear} 年 ${item.label}`, item)"
          @click="pickMonth(item.month)"
        >
          <span>{{ item.label }}</span>
        </button>
      </div>

      <div v-else class="year-picker">
        <div class="year-picker-nav">
          <button type="button" class="small-square" title="上一组年份" @click="prevYearBlock">‹</button>
          <strong>{{ yearBlockStart }}-{{ yearBlockStart + 11 }}</strong>
          <button type="button" class="small-square" title="下一组年份" @click="nextYearBlock">›</button>
        </div>
        <div class="year-grid">
          <button
            v-for="item in yearItems"
            :key="item.year"
            type="button"
            class="year-cell"
            :class="{ selected: item.selected, today: item.today, 'has-folder': item.folderCount, 'has-images': item.hasImages, 'empty-folder': item.folderCount && item.countKnown && !item.hasImages }"
            :title="periodTitle(`${item.year} 年`, item)"
            @click="pickYear(item.year)"
          >
            <span>{{ item.year }}</span>
          </button>
        </div>
      </div>

      <button
        v-if="clearable"
        type="button"
        class="task-date-clear"
        @click="clearDate"
      >清空日期</button>
    </div>
    </Teleport>
  </div>
</template>

<style scoped>
.task-date-picker {
  margin: 0;
  position: relative;
}

.task-date-toolbar {
  margin: 0;
  display: block;
}

.task-date-trigger {
  width: 100%;
  min-width: 0;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 5px 8px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  font-size: 12px;
}

.task-date-trigger.empty {
  background: var(--surface-muted);
  color: var(--accent-deep);
  font-weight: 500;
}

.trigger-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trigger-caret {
  flex: 0 0 auto;
  font-size: 11px;
  opacity: 0.6;
}

.task-date-panel {
  width: 268px;
  padding: 10px;
  border-radius: 12px;
  z-index: 10050;
}

.task-date-header {
  display: grid;
  grid-template-columns: 24px 24px minmax(0, 1fr) 24px 24px;
  gap: 5px;
  align-items: center;
  margin-bottom: 8px;
}

.task-date-header .small-square,
.year-picker-nav .small-square {
  width: 24px;
  min-width: 24px;
  height: 24px;
  padding: 0;
  border-radius: 7px;
  line-height: 1;
}

.date-heading-btn {
  height: 24px;
  min-width: 0;
  padding: 0 8px;
  border: 1px solid rgba(30, 41, 82, 0.16);
  border-radius: 7px;
  background: rgba(255, 252, 246, 0.86);
  color: var(--accent-deep);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}

.date-heading-btn:hover {
  background: rgba(99, 102, 241, 0.12);
}

.task-date-quickbar {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px;
  margin: 0 0 8px;
}

.date-quick {
  height: 24px;
  min-width: 0;
  padding: 0 5px;
  border: 1px solid rgba(30, 41, 82, 0.14);
  border-radius: 7px;
  background: rgba(255, 252, 246, 0.78);
  color: var(--muted);
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
}

.date-quick:hover:not(:disabled) {
  background: rgba(99, 102, 241, 0.11);
  color: var(--accent-deep);
}

.date-quick:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.calendar-week,
.calendar-grid {
  gap: 4px;
}

.calendar-week {
  margin-bottom: 6px;
  font-size: 11px;
}

.day-cell {
  position: relative;
  display: grid;
  place-items: center;
  border-radius: 8px;
  font-size: 12px;
}

.day-cell.has-images:not(.selected) {
  background: rgba(77, 139, 87, 0.18);
  color: #276136;
  box-shadow: inset 0 0 0 1px rgba(77, 139, 87, 0.36);
  font-weight: 700;
}

.day-cell.empty-folder:not(.selected) {
  background: rgba(230, 224, 214, 0.58);
  color: #a99684;
  box-shadow: none;
}

.day-cell:not(.has-folder):not(.selected) {
  background: rgba(230, 224, 214, 0.42);
  color: #b8aa99;
}

.month-grid,
.year-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px;
}

.month-cell,
.year-cell {
  height: 30px;
  min-width: 0;
  border: 1px solid rgba(30, 41, 82, 0.16);
  border-radius: 7px;
  background: rgba(255, 252, 246, 0.86);
  color: var(--ink);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  display: grid;
  place-items: center;
  gap: 1px;
}

.month-cell small,
.year-cell small {
  color: var(--muted);
  font-size: 9px;
  line-height: 1;
}

.month-cell.has-images small,
.year-cell.has-images small {
  color: #2d7a3e;
}

.month-cell.empty-folder small,
.year-cell.empty-folder small {
  color: #9a6610;
}

.month-cell.has-images:not(.selected),
.year-cell.has-images:not(.selected) {
  background: rgba(77, 139, 87, 0.16);
  border-color: rgba(77, 139, 87, 0.46);
  color: #276136;
  font-weight: 700;
}

.month-cell.empty-folder:not(.selected),
.year-cell.empty-folder:not(.selected) {
  background: rgba(230, 224, 214, 0.58);
  border-color: rgba(30, 41, 82, 0.1);
  color: #a99684;
}

.month-cell:not(.has-folder):not(.selected),
.year-cell:not(.has-folder):not(.selected) {
  background: rgba(230, 224, 214, 0.42);
  border-color: rgba(30, 41, 82, 0.08);
  color: #b8aa99;
}

.month-cell:hover,
.year-cell:hover {
  background: rgba(99, 102, 241, 0.12);
}

.month-cell.selected,
.year-cell.selected {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  border-color: transparent;
  color: #fff;
  font-weight: 700;
}

.month-cell.selected small,
.year-cell.selected small {
  color: rgba(255, 255, 255, 0.82);
}

.month-cell.today:not(.selected),
.year-cell.today:not(.selected) {
  border-color: rgba(80, 130, 92, 0.55);
  color: #2d7a3e;
  font-weight: 700;
}

.year-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.year-picker-nav {
  display: grid;
  grid-template-columns: 24px 1fr 24px;
  align-items: center;
  gap: 6px;
  color: var(--accent-deep);
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}

.task-date-clear {
  width: 100%;
  height: 26px;
  margin-top: 8px;
  padding: 0 8px;
  border: 1px solid rgba(30, 41, 82, 0.12);
  border-radius: 7px;
  background: rgba(79, 118, 224, 0.1);
  color: var(--accent-deep);
  font-size: 11px;
}

.task-date-clear:hover {
  background: rgba(79, 118, 224, 0.18);
}

/* Lightweight anime theme */
.task-date-trigger { background: var(--accent-gradient); box-shadow: 0 4px 10px rgba(var(--accent-rgb), 0.16); }
.task-date-trigger.empty,
.date-heading-btn,
.date-quick,
.month-cell,
.year-cell { border-color: var(--line); background: var(--surface-muted); color: var(--ink); }
.date-heading-btn { color: var(--accent-deep); }
.date-heading-btn:hover,
.date-quick:hover:not(:disabled),
.month-cell:hover,
.year-cell:hover { background: var(--soft-violet); color: var(--accent-deep); }
.day-cell:not(.has-folder):not(.selected),
.month-cell:not(.has-folder):not(.selected),
.year-cell:not(.has-folder):not(.selected) { background: #f1f0f6; border-color: var(--line); color: #aaa6b7; }
.month-cell.selected,
.year-cell.selected { background: var(--accent-gradient); }
.task-date-clear { border-color: rgba(var(--violet-rgb), 0.12); background: var(--soft-violet); color: var(--accent-deep); }
.task-date-clear:hover { background: #e8e4ff; }
</style>
