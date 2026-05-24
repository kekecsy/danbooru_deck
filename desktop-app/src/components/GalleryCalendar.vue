<script setup>
import { computed, ref, watch } from 'vue';

const props = defineProps({
  availableDates: { type: Array, default: () => [] },
  selectedDate: { type: String, default: '' },
  today: { type: String, default: '' }
});

const emit = defineEmits(['select']);

const open = ref(false);
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
  const parsed = parseDate(props.selectedDate || props.today);
  if (!parsed) return;
  currentYear.value = parsed.year;
  currentMonth.value = parsed.month;
}

watch(() => [props.selectedDate, props.today], syncMonth, { immediate: true });

const availableSet = computed(() => new Set(props.availableDates));
const selectedIndex = computed(() => props.availableDates.indexOf(props.selectedDate));
const canJumpToday = computed(() => !!props.today && props.selectedDate !== props.today && availableSet.value.has(props.today));

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
      selected: date === props.selectedDate,
      today: date === props.today
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
</script>

<template>
  <div class="calendar">
    <div class="calendar-toolbar">
      <button class="small-btn" :disabled="selectedIndex < 0 || selectedIndex >= props.availableDates.length - 1" @click="$emit('select', props.availableDates[selectedIndex + 1])">上一天</button>
      <button class="calendar-trigger" @click="open = !open">{{ props.selectedDate || '选择日期' }}</button>
      <button class="small-btn" :disabled="selectedIndex <= 0" @click="$emit('select', props.availableDates[selectedIndex - 1])">下一天</button>
      <button class="today-btn" :disabled="!canJumpToday" @click="$emit('select', props.today)">当前正在下载的日期</button>
    </div>

    <div v-if="open" class="calendar-panel">
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
        >
          {{ cell.day }}
        </button>
      </div>
    </div>
  </div>
</template>
