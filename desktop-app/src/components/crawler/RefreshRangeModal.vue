<script setup>
// 刷新指定范围页的热度 Modal。
// 状态（open / startPage / endPage）由父组件持有，通过 `state` prop 传入（直接传 ref 会自动 unwrap）。
// 业务执行（startRefreshScoresRange）由父组件处理，这里只 emit `confirm`。
import { computed } from 'vue';

const props = defineProps({
  state: { type: Object, required: true },          // { open, startPage, endPage }
  activeTotalPages: { type: Number, required: true },
  activePage: { type: Number, required: true },
  pageSize: { type: Number, required: true },
  count: { type: Number, required: true },          // 父组件算好的范围内图片数（依赖 filteredLocalImages）
  isRunning: { type: Boolean, default: false },
});
const emit = defineEmits(['update:open', 'confirm']);

const open = computed({
  get: () => props.state.open,
  set: (v) => emit('update:open', v),
});

function close() { open.value = false; }
function confirm() { emit('confirm'); }
</script>

<template>
  <div
    v-if="state.open"
    class="viewer-overlay"
    @click.self="close"
    style="z-index: 10020; display: flex; justify-content: center; align-items: center; padding: 24px;"
  >
    <div class="range-refresh-modal">
      <div class="range-refresh-head">
        <h3 style="margin: 0; color: var(--accent-deep); font-size: 17px;">刷新指定范围页的热度</h3>
        <button class="ghost" @click="close" style="color: var(--muted);">×</button>
      </div>
      <p class="muted compact-text" style="margin: 0;">
        当前共 {{ activeTotalPages }} 页 · 每页 {{ pageSize }} 张 · 当前所在第 {{ activePage }} 页
      </p>
      <div class="field-grid">
        <label>
          <span>起始页</span>
          <input v-model.number="state.startPage" type="number" min="1" :max="activeTotalPages" />
        </label>
        <label>
          <span>结束页</span>
          <input v-model.number="state.endPage" type="number" min="1" :max="activeTotalPages" />
        </label>
      </div>
      <p class="muted compact-text" style="margin: 0;">
        将刷新 <strong style="color: var(--accent-deep);">{{ count }}</strong> 张图片的 score / 收藏数 / 画师（孤立文件会反查补全）
      </p>
      <div style="display: flex; justify-content: flex-end; gap: 8px;">
        <button class="ghost" @click="close" style="color: var(--accent-deep);">取消</button>
        <button @click="confirm" :disabled="!count || isRunning" style="min-width: 100px;">
          {{ isRunning ? '刷新中...' : '确定刷新' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.range-refresh-modal {
  width: 400px;
  max-width: 92vw;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.range-refresh-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
