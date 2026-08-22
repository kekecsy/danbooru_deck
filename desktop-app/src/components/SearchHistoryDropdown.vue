<!--
  搜索历史下拉（画廊「搜索作者/角色」+ Tag 浏览共用）
  浏览器地址栏风格：focus 即出现、↑↓ 高亮、Enter 选中、Esc 关闭、
  每条 × 删除（hover / 键盘高亮时显现）、顶部「清空全部」。

  使用方式：
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
    <!-- 父级 input 需要把 keydown 透传进来，组件会处理 ↑↓ Enter Esc -->
    <input @keydown="searchHistoryRef?.handleKeydown($event)" ... />

  父级只需要管 open / items / commit，键盘 nav 全部在组件内。
-->
<script setup>
import { ref, watch } from 'vue';

const props = defineProps({
  items: { type: Array, required: true },
  open: { type: Boolean, default: false },
  headerLabel: { type: String, default: '最近搜索' },
});

const emit = defineEmits([
  'pick',   // (value: string) 选中一条（含键盘 Enter）
  'remove', // (value: string) 点条目上的 ×
  'clear',  // () 点「清空全部」
  'close',  // () Esc / 选中后由组件通知
]);

const activeIndex = ref(0);
// items 变化（搜索/删除/清空）时把高亮重置到第一项
watch(() => props.items, () => { activeIndex.value = 0; });

function onPick(entry) {
  emit('pick', entry);
  emit('close');
}

function onRemove(event, entry) {
  // 阻止冒泡到 li 的 mousedown.prevent（避免同时触发 pick）
  event?.stopPropagation?.();
  event?.preventDefault?.();
  emit('remove', entry);
  // 删除后 activeIndex 可能会越界，watch 会重置到 0
}

function onClear(event) {
  event?.preventDefault?.();
  emit('clear');
}

// 暴露给父级：父级在 <input> 上 @keydown="ref?.handleKeydown($event)"
// 这里集中处理 ↑↓ Enter Esc 四个键，其它键原样放行
function handleKeydown(event) {
  if (!props.open || !props.items.length) return;
  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault();
      activeIndex.value = (activeIndex.value + 1) % props.items.length;
      break;
    case 'ArrowUp':
      event.preventDefault();
      activeIndex.value = (activeIndex.value - 1 + props.items.length) % props.items.length;
      break;
    case 'Enter': {
      // 注意：画廊的「Enter 提交搜索」逻辑是在父级 input 的 @keyup.enter 上独立处理的，
      // 这里只在下拉可见 + 键盘高亮在某一项时，把「Enter 选中历史」当成 pick 行为。
      // 父级如果想「Enter 始终提交当前输入」，不要把 keydown 透传进来即可。
      const entry = props.items[activeIndex.value];
      if (entry != null) {
        event.preventDefault();
        onPick(entry);
      }
      break;
    }
    case 'Escape':
      event.preventDefault();
      emit('close');
      break;
    default:
      break;
  }
}

defineExpose({ handleKeydown });
</script>

<template>
  <div v-if="open && items.length" class="search-history-dropdown">
    <div class="search-history-header">
      <span>{{ headerLabel }}</span>
      <button
        type="button"
        class="search-history-clear"
        title="清空所有历史"
        @mousedown.prevent="onClear"
      >清空全部</button>
    </div>
    <ul role="listbox">
      <li
        v-for="(entry, i) in items"
        :key="entry"
        :class="['search-history-item', { active: i === activeIndex }]"
        @mousedown.prevent="onPick(entry)"
        @mouseenter="activeIndex = i"
      >
        <span class="search-history-text">{{ entry }}</span>
        <button
          type="button"
          class="search-history-remove"
          title="删除这条"
          @mousedown.stop.prevent="onRemove($event, entry)"
        >×</button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
/* 搜索历史下拉：浏览器地址栏风格。绝对定位，父级需要 position: relative
   （.search-input-wrap / .browse-query-wrap 已经是）。 */
.search-history-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 100;
  background: var(--panel, #fdf6e3);
  border: 1px solid var(--line, rgba(0, 0, 0, 0.15));
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  max-height: 320px;
  overflow-y: auto;
  font-size: 13px;
  color: var(--ink, #2a1f10);
}
.search-history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  border-bottom: 1px solid var(--line, rgba(0, 0, 0, 0.1));
  font-size: 12px;
  color: var(--muted, #6b5a3c);
}
.search-history-clear {
  background: none;
  border: none;
  padding: 2px 6px;
  color: var(--muted, #6b5a3c);
  font-size: 12px;
  cursor: pointer;
  border-radius: 3px;
  box-shadow: none;
  transform: none;
}
.search-history-clear:hover {
  background: rgba(157, 44, 44, 0.1);
  color: #9d2c2c;
  filter: none;
  box-shadow: none;
  transform: none;
}
.search-history-dropdown ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.search-history-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}
.search-history-item:last-child {
  border-bottom: none;
}
.search-history-item:hover {
  background: rgba(196, 130, 60, 0.12);
}
/* 键盘高亮态：和 hover 同色调，色稍重 */
.search-history-item.active {
  background: rgba(196, 130, 60, 0.20);
}
.search-history-text {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.search-history-remove {
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--muted, #6b5a3c);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease, background-color 0.12s ease;
  display: grid;
  place-items: center;
  box-shadow: none;
  transform: none;
}
.search-history-item:hover .search-history-remove,
.search-history-item.active .search-history-remove {
  opacity: 1;
}
.search-history-remove:hover {
  background: rgba(157, 44, 44, 0.18);
  color: #9d2c2c;
  filter: none;
  box-shadow: none;
  transform: none;
}
</style>
