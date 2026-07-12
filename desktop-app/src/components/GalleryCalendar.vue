<script setup>
import { computed, ref, watch, onMounted } from 'vue';

const props = defineProps({
  availableDates: { type: Array, default: () => [] },
  dateFolders: { type: Array, default: () => [] },
  // Tag 文件夹列表，结构 [{folder, display}]；后端 /api/gallery_data 已返。
  availableTags: { type: Array, default: () => [] },
  selectedDate: { type: String, default: '' },
  today: { type: String, default: '' }
});

const emit = defineEmits(['select']);

const open = ref(false);
const activeTab = ref('date');
const tagSearch = ref('');
const dateView = ref('days'); // days | months | years

function isTagFolder(s) { return (s || '').startsWith('tag_'); }
function tagFolderLabel(folder) {
  if (!folder) return '';
  const body = folder.startsWith('tag_') ? folder.slice(4) : folder;
  // sanitize_tag_folder 把空格转 __、冒号转 __c__，这里逆向还原
  return body.replace(/__c__/g, ':').replace(/__/g, ' ');
}

const isTagSelected = computed(() => isTagFolder(props.selectedDate));

// 打开面板时默认 tab 跟随当前选中类型
watch(open, (v) => {
  if (v) {
    activeTab.value = isTagSelected.value ? 'tag' : 'date';
    dateView.value = 'days';
    tagSearch.value = '';
  }
});

const triggerLabel = computed(() => {
  if (!props.selectedDate) return '选择日期或标签';
  if (isTagSelected.value) return tagFolderLabel(props.selectedDate);
  return props.selectedDate;
});

// ---------------- 最近使用过的 tag（localStorage 持久化） ----------------
const RECENT_KEY = 'gallery_recent_tags';
const RECENT_MAX = 8;
const recentTags = ref([]);

function loadRecent() {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    recentTags.value = Array.isArray(arr) ? arr.filter(s => typeof s === 'string') : [];
  } catch {
    recentTags.value = [];
  }
}
function pushRecent(folder) {
  if (!folder) return;
  const next = [folder, ...recentTags.value.filter(t => t !== folder)].slice(0, RECENT_MAX);
  recentTags.value = next;
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch {}
}
function removeRecent(folder) {
  recentTags.value = recentTags.value.filter(t => t !== folder);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(recentTags.value)); } catch {}
}
onMounted(loadRecent);

const tagMap = computed(() => {
  const m = Object.create(null);
  for (const t of (props.availableTags || [])) {
    if (t && t.folder) m[t.folder] = t;
  }
  return m;
});

// 最近用过 ∩ 当前磁盘上还存在的 tag —— 删过的 tag 文件夹不再出现在"最近"
const recentTagItems = computed(() =>
  recentTags.value.map(f => tagMap.value[f]).filter(Boolean)
);

const allTagItems = computed(() => {
  const list = [...(props.availableTags || [])];
  list.sort((a, b) => (a.display || a.folder || '').localeCompare(b.display || b.folder || ''));
  return list;
});

const filteredTags = computed(() => {
  const q = tagSearch.value.trim().toLowerCase();
  if (!q) return allTagItems.value;
  return allTagItems.value.filter(t => {
    const dn = (t.display || '').toLowerCase();
    const fn = (t.folder || '').toLowerCase();
    return dn.includes(q) || fn.includes(q);
  });
});

// ---------------- 日历逻辑（保留原 GalleryCalendar 实现） ----------------
const currentYear = ref(0);
const currentMonth = ref(0);

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

const sortedAvailableDates = computed(() => dateFolderRecords.value.map(item => item.date));
const dateFolderMap = computed(() => {
  const out = new Map();
  for (const item of dateFolderRecords.value) out.set(item.date, item);
  return out;
});

function syncMonth() {
  // tag 模式下：日历定位到 today 或最新可用日期
  const referenceDate = isTagSelected.value
    ? (isTagFolder(props.today) ? (sortedAvailableDates.value[0] || '') : props.today)
    : (props.selectedDate || props.today);
  const parsed = parseDate(referenceDate);
  if (parsed) {
    currentYear.value = parsed.year;
    currentMonth.value = parsed.month;
    return;
  }
  const now = new Date();
  currentYear.value = now.getFullYear();
  currentMonth.value = now.getMonth() + 1;
}
watch(() => [props.selectedDate, props.today, sortedAvailableDates.value.join('|')], syncMonth, { immediate: true });

const availableSet = computed(() => new Set(sortedAvailableDates.value));
const availableYearSet = computed(() => {
  const years = new Set();
  for (const item of dateFolderRecords.value) {
    const parsed = parseDate(item.date);
    if (parsed) years.add(parsed.year);
  }
  return years;
});
const selectedIndex = computed(() =>
  isTagSelected.value ? -1 : sortedAvailableDates.value.indexOf(props.selectedDate)
);
const canJumpToday = computed(() =>
  !!props.today &&
  !isTagFolder(props.today) &&
  props.selectedDate !== props.today &&
  availableSet.value.has(props.today)
);

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
  return result.map(item => {
    const date = formatDate(item.year, item.month, item.day);
    const folder = dateFolderMap.value.get(date) || null;
    return {
      ...item,
      date,
      enabled: !!folder,
      hasImages: !!folder?.hasImages,
      countKnown: !!folder?.countKnown,
      imageCount: folder?.imageCount ?? null,
      selected: !isTagSelected.value && date === props.selectedDate,
      today: !isTagFolder(props.today) && date === props.today
    };
  });
});

const monthItems = computed(() => {
  const labels = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'];
  const selected = parseDate(props.selectedDate);
  const today = parseDate(props.today);
  return labels.map((label, idx) => {
    const month = idx + 1;
    const prefix = `${String(currentYear.value).padStart(4, '0')}-${String(month).padStart(2, '0')}-`;
    const folders = dateFolderRecords.value.filter(item => item.date.startsWith(prefix));
    const imageCount = folders.reduce((sum, item) => sum + (item.imageCount || 0), 0);
    return {
      month,
      label,
      enabled: folders.length > 0,
      folderCount: folders.length,
      imageCount,
      hasImages: folders.some(item => item.hasImages),
      countKnown: folders.some(item => item.countKnown),
      selected: !!selected && selected.year === currentYear.value && selected.month === month,
      today: !!today && today.year === currentYear.value && today.month === month
    };
  });
});

const yearBlockStart = computed(() => Math.floor((currentYear.value || new Date().getFullYear()) / 12) * 12);
const yearItems = computed(() => {
  const selected = parseDate(props.selectedDate);
  const today = parseDate(props.today);
  return Array.from({ length: 12 }, (_, i) => {
    const year = yearBlockStart.value + i;
    const prefix = `${String(year).padStart(4, '0')}-`;
    const folders = dateFolderRecords.value.filter(item => item.date.startsWith(prefix));
    const imageCount = folders.reduce((sum, item) => sum + (item.imageCount || 0), 0);
    return {
      year,
      enabled: folders.length > 0,
      folderCount: folders.length,
      imageCount,
      hasImages: folders.some(item => item.hasImages),
      countKnown: folders.some(item => item.countKnown),
      selected: !!selected && selected.year === year,
      today: !!today && today.year === year
    };
  });
});

function imageCountLabel(count) {
  if (count == null) return '';
  if (count > 999) return '999+';
  return String(count);
}

function dateCellTitle(cell) {
  if (!cell.enabled) return `${cell.date} 没有日期文件夹`;
  if (!cell.countKnown) return `查看 ${cell.date}`;
  return cell.hasImages ? `${cell.date} · ${cell.imageCount} 张图片` : `${cell.date} · 文件夹为空`;
}

function periodTitle(label, item) {
  if (!item.enabled) return `${label} 没有日期文件夹`;
  if (!item.countKnown) return `查看 ${label}`;
  return item.hasImages
    ? `${label} · ${item.folderCount} 个日期 · ${item.imageCount} 张图片`
    : `${label} · ${item.folderCount} 个日期 · 文件夹为空`;
}

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
function jumpLatestDate() {
  const latest = sortedAvailableDates.value[0];
  if (!latest) return;
  emit('select', latest);
  open.value = false;
}
function jumpTodayMonth() {
  const parsed = parseDate(props.today);
  if (!parsed) return;
  currentYear.value = parsed.year;
  currentMonth.value = parsed.month;
  dateView.value = 'days';
}

function pickDate(date) {
  emit('select', date);
  open.value = false;
}
function pickTag(folder) {
  if (!folder) return;
  pushRecent(folder);
  emit('select', folder);
  open.value = false;
}
</script>

<template>
  <div class="calendar">
    <div class="calendar-toolbar">
      <button
        class="small-btn"
        :disabled="selectedIndex < 0 || selectedIndex >= sortedAvailableDates.length - 1"
        @click="$emit('select', sortedAvailableDates[selectedIndex + 1])"
      >上一天</button>
      <button
        class="calendar-trigger"
        :class="{ 'is-tag': isTagSelected }"
        @click="open = !open"
        :title="isTagSelected ? `tag 文件夹：${tagFolderLabel(props.selectedDate)}（点击切换）` : '点击切换日期或标签'"
      >
        <span class="trigger-text">{{ triggerLabel }}</span>
        <span class="trigger-caret">▾</span>
      </button>
      <button
        class="small-btn"
        :disabled="selectedIndex <= 0"
        @click="$emit('select', sortedAvailableDates[selectedIndex - 1])"
      >下一天</button>
      <button
        class="today-btn"
        :disabled="!canJumpToday"
        :title="canJumpToday ? `跳转到 ${props.today}` : (isTagFolder(props.today) ? '后端 today 字段被下载任务覆盖了（已知 bug）' : '已经在今天 / 今天还没有图')"
        @click="$emit('select', props.today)"
      >今天</button>
    </div>

    <div v-if="open" class="calendar-panel" @click.stop>
      <div class="selector-tabs">
        <button
          class="selector-tab"
          :class="{ active: activeTab === 'date' }"
          @click="activeTab = 'date'"
        >日期 <span class="selector-tab-badge">{{ sortedAvailableDates.length }}</span></button>
        <button
          class="selector-tab"
          :class="{ active: activeTab === 'tag' }"
          @click="activeTab = 'tag'"
        >标签 <span class="selector-tab-badge">{{ allTagItems.length }}</span></button>
      </div>

      <div v-if="activeTab === 'date'">
        <div class="calendar-header calendar-header-redesigned">
          <button class="small-square" title="上一年" @click="prevYear">«</button>
          <button class="small-square" title="上一月" @click="prevMonth">‹</button>
          <button class="date-heading-btn" title="切换年/月选择" @click="toggleDateView">
            <strong v-if="dateView === 'days'">{{ currentYear }}-{{ String(currentMonth).padStart(2, '0') }}</strong>
            <strong v-else-if="dateView === 'months'">{{ currentYear }}</strong>
            <strong v-else>{{ yearBlockStart }}-{{ yearBlockStart + 11 }}</strong>
          </button>
          <button class="small-square" title="下一月" @click="nextMonth">›</button>
          <button class="small-square" title="下一年" @click="nextYear">»</button>
        </div>
        <div class="date-quickbar">
          <button class="date-quick" :disabled="!sortedAvailableDates.length" @click="jumpLatestDate">最新</button>
          <button class="date-quick" :disabled="!props.today" @click="jumpTodayMonth">今年今月</button>
          <button class="date-quick" @click="dateView = 'years'">选年份</button>
          <button class="date-quick" @click="dateView = 'months'">选月份</button>
        </div>

        <template v-if="dateView === 'days'">
          <div class="calendar-week">
            <span v-for="item in ['一','二','三','四','五','六','日']" :key="item">{{ item }}</span>
          </div>
          <div class="calendar-grid">
            <button
              v-for="cell in cells"
              :key="cell.date"
              class="day-cell"
              :class="{ disabled: !cell.enabled, selected: cell.selected, other: cell.otherMonth, today: cell.today, 'has-folder': cell.enabled, 'has-images': cell.hasImages, 'empty-folder': cell.enabled && cell.countKnown && !cell.hasImages }"
              :title="dateCellTitle(cell)"
              :disabled="!cell.enabled"
              @click="pickDate(cell.date)"
            >
              <span>{{ cell.day }}</span>
            </button>
          </div>
        </template>

        <div v-else-if="dateView === 'months'" class="month-grid">
          <button
            v-for="m in monthItems"
            :key="m.month"
            class="month-cell"
            :class="{ disabled: !m.enabled, selected: m.selected, today: m.today, 'has-images': m.hasImages, 'empty-folder': m.enabled && m.countKnown && !m.hasImages }"
            :disabled="!m.enabled"
            :title="periodTitle(`${currentYear} 年 ${m.label}`, m)"
            @click="pickMonth(m.month)"
          >
            <span>{{ m.label }}</span>
            <small v-if="m.countKnown">{{ imageCountLabel(m.imageCount) }}图</small>
          </button>
        </div>

        <div v-else class="year-picker">
          <div class="year-picker-nav">
            <button class="small-square" title="上一组年份" @click="prevYearBlock">‹</button>
            <span>{{ yearBlockStart }}-{{ yearBlockStart + 11 }}</span>
            <button class="small-square" title="下一组年份" @click="nextYearBlock">›</button>
          </div>
          <div class="year-grid">
            <button
              v-for="y in yearItems"
              :key="y.year"
              class="year-cell"
              :class="{ disabled: !y.enabled, selected: y.selected, today: y.today, 'has-images': y.hasImages, 'empty-folder': y.enabled && y.countKnown && !y.hasImages }"
              :disabled="!y.enabled"
              :title="periodTitle(`${y.year} 年`, y)"
              @click="pickYear(y.year)"
            >
              <span>{{ y.year }}</span>
              <small v-if="y.countKnown">{{ imageCountLabel(y.imageCount) }}图</small>
            </button>
          </div>
        </div>
        <div class="year-strip" v-if="dateView !== 'years'">
          <button
            v-for="y in yearItems"
            :key="`strip-${y.year}`"
            class="year-strip-btn"
            :class="{ active: y.year === currentYear, disabled: !y.enabled }"
            :disabled="!y.enabled"
            @click="currentYear = y.year"
          >{{ y.year }}</button>
        </div>
      </div>

      <div v-else class="tag-tab">
        <input
          v-model="tagSearch"
          type="text"
          class="tag-search"
          placeholder="搜索标签..."
          autocomplete="off"
        />

        <div v-if="recentTagItems.length && !tagSearch" class="tag-section">
          <div class="tag-section-title">最近使用</div>
          <div class="tag-list tag-list-recent">
            <div
              v-for="t in recentTagItems"
              :key="`r-${t.folder}`"
              class="tag-row"
              :class="{ selected: props.selectedDate === t.folder }"
            >
              <button class="tag-name" @click="pickTag(t.folder)" :title="t.folder">
                {{ t.display || t.folder }}
              </button>
              <button
                class="tag-remove"
                title="从'最近使用'里移除（不会删除文件夹）"
                @click.stop="removeRecent(t.folder)"
              >×</button>
            </div>
          </div>
        </div>

        <div class="tag-section">
          <div class="tag-section-title">
            {{ tagSearch ? `搜索结果 · ${filteredTags.length}` : `全部 · ${allTagItems.length}` }}
          </div>
          <div v-if="filteredTags.length" class="tag-list">
            <button
              v-for="t in filteredTags"
              :key="t.folder"
              class="tag-row-flat"
              :class="{ selected: props.selectedDate === t.folder }"
              :title="t.folder"
              @click="pickTag(t.folder)"
            >{{ t.display || t.folder }}</button>
          </div>
          <div v-else class="tag-empty">
            {{ tagSearch ? '没有匹配的标签' : '还没有 tag 文件夹 —— 用「标签下载」模式抓一次就会出现在这里' }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 触发按钮的 tag 状态：日期模式沿用 style.css 的 .calendar-trigger，
   tag 模式给出渐变背景区分一眼能看出当前选中的是哪一类 */
.calendar-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  justify-content: space-between;
}
.calendar-trigger .trigger-text {
  flex: 1;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.calendar-trigger .trigger-caret {
  font-size: 11px;
  opacity: 0.6;
}
.calendar-trigger.is-tag {
  background: linear-gradient(135deg, #faecd9, #f5dbb8);
  border-color: rgba(74, 53, 25, 0.35);
  font-weight: 600;
}

.calendar-header-redesigned {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: space-between;
}
.calendar-header-redesigned .small-square {
  width: 26px;
  min-width: 26px;
  height: 26px;
  padding: 0;
  flex: 0 0 26px;
  line-height: 1;
}
.date-heading-btn {
  flex: 1 1 auto;
  min-width: 104px;
  height: 28px;
  border: 1px solid rgba(74, 53, 25, 0.16);
  border-radius: 7px;
  background: rgba(255, 252, 246, 0.86);
  color: var(--accent-deep);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
.date-heading-btn:hover {
  background: rgba(243, 223, 212, 0.8);
}
.date-quickbar {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  margin: 8px 0 10px;
}
.date-quick {
  height: 26px;
  min-width: 0;
  padding: 0 6px;
  border: 1px solid rgba(74, 53, 25, 0.14);
  border-radius: 7px;
  background: rgba(255, 252, 246, 0.78);
  color: var(--muted, #846a55);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.date-quick:hover:not(:disabled) {
  background: rgba(243, 223, 212, 0.75);
  color: var(--accent-deep);
}
.date-quick:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.day-cell {
  position: relative;
  display: grid;
  place-items: center;
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

.month-grid,
.year-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}
.month-cell,
.year-cell {
  height: 34px;
  min-width: 0;
  border: 1px solid rgba(74, 53, 25, 0.16);
  border-radius: 7px;
  background: rgba(255, 252, 246, 0.86);
  color: var(--ink, #2d2417);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  display: grid;
  place-items: center;
  gap: 1px;
}
.month-cell small,
.year-cell small {
  font-size: 9px;
  line-height: 1;
  color: var(--muted, #846a55);
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
  border-color: rgba(74, 53, 25, 0.1);
  color: #a99684;
}
.month-cell:hover:not(:disabled),
.year-cell:hover:not(:disabled) {
  background: rgba(243, 223, 212, 0.8);
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
.month-cell.disabled,
.year-cell.disabled {
  opacity: 0.32;
  cursor: not-allowed;
}
.year-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.year-picker-nav {
  display: grid;
  grid-template-columns: 28px 1fr 28px;
  align-items: center;
  gap: 8px;
  color: var(--accent-deep);
  font-size: 13px;
  font-weight: 700;
  text-align: center;
}
.year-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 4px;
  margin-top: 10px;
}
.year-strip-btn {
  height: 24px;
  min-width: 0;
  padding: 0 4px;
  border: 1px solid rgba(74, 53, 25, 0.12);
  border-radius: 6px;
  background: rgba(255, 252, 246, 0.72);
  color: var(--muted, #846a55);
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
}
.year-strip-btn.active {
  background: var(--accent-deep);
  border-color: transparent;
  color: #fff;
  font-weight: 700;
}
.year-strip-btn.disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

/* tab 切换条：在 .calendar-panel 顶端，分日期 / 标签 */
.selector-tabs {
  display: flex;
  gap: 6px;
  border-bottom: 1px solid rgba(74, 53, 25, 0.12);
  margin: -4px -4px 10px;
  padding: 0 4px;
}
.selector-tab {
  background: transparent;
  border: none;
  padding: 6px 12px;
  font-family: inherit;
  font-size: 13px;
  color: var(--muted, #846a55);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  border-radius: 0;
  min-width: 0;
}
.selector-tab.active {
  color: var(--accent-deep);
  border-bottom-color: var(--accent-deep);
  font-weight: 700;
}
.selector-tab-badge {
  display: inline-block;
  margin-left: 4px;
  padding: 0 6px;
  background: rgba(74, 53, 25, 0.1);
  border-radius: 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-deep);
}
.selector-tab.active .selector-tab-badge {
  background: var(--accent-deep);
  color: #fff;
}

/* tag tab —— 只在 .calendar-panel 内出现，沿用 panel 自身的 320px 宽度 */
.tag-tab {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tag-search {
  padding: 6px 10px;
  border: 1px solid rgba(74, 53, 25, 0.18);
  border-radius: 8px;
  font-family: inherit;
  font-size: 13px;
  background: rgba(255, 252, 246, 0.9);
  color: var(--ink, #2d2417);
}
.tag-search:focus {
  outline: none;
  border-color: var(--accent-deep);
  box-shadow: 0 0 0 2px rgba(139, 90, 43, 0.15);
}

.tag-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tag-section-title {
  font-size: 11px;
  color: var(--muted, #846a55);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 700;
}
.tag-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 220px;
  overflow-y: auto;
  padding-right: 2px;
}
.tag-list-recent { max-height: 140px; }

.tag-row {
  display: flex;
  align-items: center;
  background: transparent;
  border-radius: 6px;
  padding: 0;
  width: 100%;
}
.tag-row.selected { background: linear-gradient(135deg, var(--accent), var(--accent-deep)); }
.tag-name {
  flex: 1;
  background: transparent;
  border: none;
  padding: 5px 8px;
  font-family: inherit;
  font-size: 13px;
  color: var(--ink, #2d2417);
  cursor: pointer;
  text-align: left;
  border-radius: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.tag-row:not(.selected) .tag-name:hover { background: rgba(243, 223, 212, 0.7); }
.tag-row.selected .tag-name { color: #fff; font-weight: 600; }
.tag-remove {
  background: transparent;
  border: none;
  padding: 2px 6px;
  color: var(--muted, #846a55);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  border-radius: 4px;
  min-width: 0;
}
.tag-remove:hover {
  background: rgba(230, 224, 214, 0.6);
  color: var(--accent-deep);
}
.tag-row.selected .tag-remove { color: rgba(255, 255, 255, 0.85); }
.tag-row.selected .tag-remove:hover { background: rgba(255, 255, 255, 0.18); color: #fff; }

.tag-row-flat {
  background: transparent;
  border: none;
  padding: 5px 10px;
  font-family: inherit;
  font-size: 13px;
  color: var(--ink, #2d2417);
  cursor: pointer;
  text-align: left;
  border-radius: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.tag-row-flat:hover { background: rgba(243, 223, 212, 0.7); }
.tag-row-flat.selected {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  font-weight: 600;
}

.tag-empty {
  padding: 12px 8px;
  text-align: center;
  color: var(--muted, #846a55);
  font-size: 12px;
  font-style: italic;
  line-height: 1.5;
}
</style>
