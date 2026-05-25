<script setup>
import { computed, ref, watch, onMounted } from 'vue';

const props = defineProps({
  availableDates: { type: Array, default: () => [] },
  // Tag 文件夹列表，结构 [{folder, display}]；后端 /api/gallery_data 已返。
  availableTags: { type: Array, default: () => [] },
  selectedDate: { type: String, default: '' },
  today: { type: String, default: '' }
});

const emit = defineEmits(['select']);

const open = ref(false);
const activeTab = ref('date');
const tagSearch = ref('');

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
    tagSearch.value = '';
  }
});

const triggerLabel = computed(() => {
  if (!props.selectedDate) return '选择日期或标签';
  if (isTagSelected.value) return `🏷 ${tagFolderLabel(props.selectedDate)}`;
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
function syncMonth() {
  // tag 模式下：日历定位到 today 或最新可用日期
  const referenceDate = isTagSelected.value
    ? (isTagFolder(props.today) ? (props.availableDates[0] || '') : props.today)
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
watch(() => [props.selectedDate, props.today], syncMonth, { immediate: true });

const availableSet = computed(() => new Set(props.availableDates));
const selectedIndex = computed(() =>
  isTagSelected.value ? -1 : props.availableDates.indexOf(props.selectedDate)
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
    return {
      ...item,
      date,
      enabled: availableSet.value.has(date),
      selected: !isTagSelected.value && date === props.selectedDate,
      today: !isTagFolder(props.today) && date === props.today
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
        :disabled="selectedIndex < 0 || selectedIndex >= props.availableDates.length - 1"
        @click="$emit('select', props.availableDates[selectedIndex + 1])"
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
        @click="$emit('select', props.availableDates[selectedIndex - 1])"
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
        >📅 日期 <span class="selector-tab-badge">{{ props.availableDates.length }}</span></button>
        <button
          class="selector-tab"
          :class="{ active: activeTab === 'tag' }"
          @click="activeTab = 'tag'"
        >🏷 标签 <span class="selector-tab-badge">{{ allTagItems.length }}</span></button>
      </div>

      <div v-if="activeTab === 'date'">
        <div class="calendar-header">
          <button class="small-square" @click="prevMonth">‹</button>
          <strong>{{ currentYear }}-{{ String(currentMonth).padStart(2, '0') }}</strong>
          <button class="small-square" @click="nextMonth">›</button>
        </div>
        <div class="calendar-week">
          <span v-for="item in ['一','二','三','四','五','六','日']" :key="item">{{ item }}</span>
        </div>
        <div class="calendar-grid">
          <button
            v-for="cell in cells"
            :key="cell.date"
            class="day-cell"
            :class="{ disabled: !cell.enabled, selected: cell.selected, other: cell.otherMonth, today: cell.today }"
            :title="cell.enabled ? `查看 ${cell.date}` : `${cell.date} 没有图片`"
            :disabled="!cell.enabled"
            @click="pickDate(cell.date)"
          >{{ cell.day }}</button>
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
