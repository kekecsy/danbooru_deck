<script setup>
// 已选清单弹窗：分两块——本日期可定位的（有缩略图 + 跳转），其他日期 / 当前过滤外的（仅 ID chip）。
// 跳转 / 移除逻辑在父组件，本组件纯呈现。
import { computed } from 'vue';

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  selectionSize: { type: Number, required: true },
  // [{ id, item, page }] —— 本日期能定位的条目
  currentEntries: { type: Array, required: true },
  // [id] —— 本日期 / 当前过滤外找不到的 ID
  otherIds: { type: Array, required: true },
});
const emit = defineEmits(['update:modelValue', 'jump-to', 'remove']);

const open = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
});
function close() { open.value = false; }
</script>

<template>
  <div
    v-if="open"
    class="viewer-overlay"
    @click.self="close"
    style="z-index: 10000; display: flex; justify-content: center; align-items: center; padding: 24px;"
  >
    <div class="selection-list-card">
      <div class="selection-list-head">
        <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">已选清单 · {{ selectionSize }} 个</h3>
        <button class="ghost" @click="close" style="color: var(--muted);">×</button>
      </div>
      <div v-if="!selectionSize" class="gallery-empty" style="min-height: 120px;">还没有选择任何图片</div>
      <div v-else class="selection-list-body">
        <div v-if="currentEntries.length" class="selection-list-section">
          <div class="selection-list-section-title">本日期可定位 · {{ currentEntries.length }} 个</div>
          <div class="selection-list-grid">
            <div v-for="entry in currentEntries" :key="`cur-${entry.id}`" class="selection-list-item">
              <img class="selection-list-thumb" :src="entry.item.thumbUrl" :alt="entry.id" loading="lazy" />
              <div class="selection-list-item-info">
                <span class="selection-list-id">#{{ entry.id }}</span>
                <span class="muted compact-text">第 {{ entry.page }} 页</span>
              </div>
              <div class="selection-list-item-actions">
                <button class="secondary" @click="emit('jump-to', entry.id)" title="跳转到该图所在页">跳转</button>
                <button class="ghost" @click="emit('remove', entry.id)" title="从选择中移除">移除</button>
              </div>
            </div>
          </div>
        </div>
        <div v-if="otherIds.length" class="selection-list-section">
          <div class="selection-list-section-title">其他日期 / 当前过滤外 · {{ otherIds.length }} 个</div>
          <div class="selection-list-other">
            <span v-for="id in otherIds" :key="`oth-${id}`" class="selection-chip">
              #{{ id }}
              <button @click="emit('remove', id)" title="移除">×</button>
            </span>
          </div>
          <p class="muted compact-text" style="margin: 8px 0 0;">提示：这些 ID 在当前日期 / 过滤条件下找不到。切换日期或关掉「只看高分」「格式过滤」可能能看到。</p>
        </div>
      </div>
      <div class="selection-list-foot">
        <button class="ghost" @click="close">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
  background: rgba(99, 102, 241, 0.4);
  border: 1px solid rgba(79, 118, 224, 0.14);
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
  background: rgba(99, 102, 241, 0.1);
  border: 1px solid rgba(79, 118, 224, 0.18);
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
</style>
