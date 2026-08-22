<script setup>
// 跨盘合并 viewer_data.json 的配置 + 预览 + 确认 modal。
// 替代原本依赖 window.prompt / window.confirm 的实现——Electron renderer 里
// 这俩 API 是空实现（点完没反应），详见 FavoritesPage.vue 的同款注释。
import { computed, ref, watch } from 'vue';

const props = defineProps({
  state: { type: Object, required: true },   // { open, date, roots: [{id,label,path,isDefault}] }
  isBusy: { type: Boolean, default: false },
});
const emit = defineEmits(['update:open', 'success']);

const sourceIdx = ref(null);
const targetIdx = ref(null);
const preview = ref(null);
const previewing = ref(false);
const executing = ref(false);
const error = ref('');

watch(() => props.state.open, (open) => {
  if (open) {
    sourceIdx.value = null;
    targetIdx.value = null;
    preview.value = null;
    previewing.value = false;
    executing.value = false;
    error.value = '';
  }
});

watch([sourceIdx, targetIdx], async ([s, t]) => {
  preview.value = null;
  error.value = '';
  if (s == null || t == null) return;
  if (s === t) {
    error.value = '源和目标不能是同一个 root';
    return;
  }
  await runPreview();
});

async function runPreview() {
  previewing.value = true;
  try {
    const resp = await fetch('http://127.0.0.1:8000/api/merge_viewer_data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: props.state.date,
        source_root: props.state.roots[sourceIdx.value].path,
        target_root: props.state.roots[targetIdx.value].path,
        dry_run: true
      })
    });
    const data = await resp.json();
    if (!data || data.ok !== true) {
      error.value = data?.msg || '预览失败';
      return;
    }
    preview.value = data;
  } catch (e) {
    error.value = `预览失败：${e?.message || e}`;
  } finally {
    previewing.value = false;
  }
}

async function runMerge() {
  if (!preview.value || preview.value.merged_count === 0) return;
  executing.value = true;
  try {
    const resp = await fetch('http://127.0.0.1:8000/api/merge_viewer_data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: props.state.date,
        source_root: props.state.roots[sourceIdx.value].path,
        target_root: props.state.roots[targetIdx.value].path,
        dry_run: false
      })
    });
    const data = await resp.json();
    if (!data || data.ok !== true) {
      error.value = data?.msg || '合并失败';
      return;
    }
    const source = props.state.roots[sourceIdx.value];
    emit('success', { result: data, source });
    close();
  } catch (e) {
    error.value = `合并失败：${e?.message || e}`;
  } finally {
    executing.value = false;
  }
}

function close() {
  if (executing.value) return;
  emit('update:open', false);
}

const hasSourceAndTarget = computed(() =>
  sourceIdx.value != null && targetIdx.value != null && sourceIdx.value !== targetIdx.value
);
const canConfirm = computed(() =>
  hasSourceAndTarget.value && !previewing.value && !executing.value
    && preview.value && preview.value.merged_count > 0
);
const controlsDisabled = computed(() => props.isBusy || previewing.value || executing.value);
</script>

<template>
  <div
    v-if="state.open"
    class="viewer-overlay"
    @click.self="close"
    style="z-index: 10020; display: flex; justify-content: center; align-items: center; padding: 24px;"
  >
    <div class="merge-viewer-modal">
      <div class="merge-viewer-head">
        <h3 class="merge-viewer-title">合并 viewer_data · {{ state.date }}</h3>
        <button class="merge-viewer-close" @click="close" :disabled="executing" title="关闭">×</button>
      </div>

      <p class="muted compact-text merge-viewer-hint">
        按 <strong>post_url</strong> 去重后增量同步到目标 root，<strong>local_path</strong> 改写到目标盘（可重复执行）
      </p>

      <div class="merge-viewer-section">
        <div class="merge-viewer-section-title">
          <span>源 root</span>
          <span class="muted compact-text" style="font-size: 11px;">从哪增量同步</span>
        </div>
        <div class="merge-viewer-list">
          <label
            v-for="(r, idx) in state.roots"
            :key="`src-${r.id}`"
            class="merge-viewer-row"
            :class="{
              selected: sourceIdx === idx,
              disabled: controlsDisabled
            }"
          >
            <input
              type="radio"
              name="merge-src"
              :value="idx"
              :disabled="controlsDisabled"
              v-model="sourceIdx"
            />
            <div class="merge-viewer-row-text">
              <div class="merge-viewer-row-line">
                <span class="merge-viewer-row-name">{{ r.label || r.id }}</span>
                <span v-if="r.isDefault" class="merge-viewer-row-tag">默认</span>
              </div>
              <div class="merge-viewer-row-path">{{ r.path }}</div>
            </div>
          </label>
        </div>
      </div>

      <div class="merge-viewer-section">
        <div class="merge-viewer-section-title">
          <span>目标 root</span>
          <span class="muted compact-text" style="font-size: 11px;">合并写入</span>
        </div>
        <div class="merge-viewer-list">
          <label
            v-for="(r, idx) in state.roots"
            :key="`tgt-${r.id}`"
            class="merge-viewer-row"
            :class="{
              selected: targetIdx === idx,
              disabled: controlsDisabled
            }"
          >
            <input
              type="radio"
              name="merge-tgt"
              :value="idx"
              :disabled="controlsDisabled"
              v-model="targetIdx"
            />
            <div class="merge-viewer-row-text">
              <div class="merge-viewer-row-line">
                <span class="merge-viewer-row-name">{{ r.label || r.id }}</span>
                <span v-if="r.isDefault" class="merge-viewer-row-tag">默认</span>
              </div>
              <div class="merge-viewer-row-path">{{ r.path }}</div>
            </div>
          </label>
        </div>
      </div>

      <div class="merge-viewer-preview" v-if="hasSourceAndTarget || error">
        <div v-if="previewing" class="muted compact-text">正在读取 viewer_data 并预览…</div>
        <div v-else-if="error" class="merge-viewer-error">{{ error }}</div>
        <div v-else-if="preview" class="merge-viewer-summary muted compact-text">
          源 <strong>{{ preview.source_count }}</strong> 条
          ·
          目标已有 <strong>{{ preview.target_count_before }}</strong> 条
          ·
          <span v-if="preview.merged_count === 0" class="merge-viewer-ok">全部已存在，无需合并</span>
          <span v-else>
            预计新增 <strong class="merge-viewer-accent">{{ preview.merged_count }}</strong> 条
          </span>
        </div>
      </div>

      <div class="merge-viewer-foot">
        <div></div>
        <div style="display: flex; gap: 8px;">
          <button class="ghost" @click="close" :disabled="executing" style="color: var(--accent-deep);">取消</button>
          <button @click="runMerge" :disabled="!canConfirm" style="min-width: 100px;">
            {{ executing ? '合并中…' : '确定合并' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 上下堆叠的两段式列表，避开 grid + flex 在跨列布局里宽度计算不稳的坑。
   样式沿用项目里 fav-add-row 的 13px 主色、5-6px 内边距、soft-violet 高亮风格。 */
.merge-viewer-modal {
  width: 520px;
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
.merge-viewer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
.merge-viewer-title {
  margin: 0;
  color: var(--accent-deep);
  font-size: 17px;
  font-weight: 600;
}
.merge-viewer-close {
  background: none;
  border: none;
  color: var(--muted);
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  padding: 0 6px;
  border-radius: 4px;
}
.merge-viewer-close:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.05);
  color: var(--accent-deep);
}
.merge-viewer-hint {
  margin: 0;
}
.merge-viewer-hint strong {
  color: var(--accent-deep);
  font-weight: 600;
}
.merge-viewer-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.merge-viewer-section-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 0 2px;
  font-size: 13px;
  color: var(--accent-deep);
  font-weight: 600;
}
.merge-viewer-list {
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.merge-viewer-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--ink);
  border: 1px solid transparent;
  background: transparent;
  transition: background 0.12s, border-color 0.12s;
}
.merge-viewer-row:hover:not(.disabled) {
  background: rgba(99, 102, 241, 0.08);
}
.merge-viewer-row.selected {
  background: rgba(99, 102, 241, 0.13);
  border-color: rgba(99, 102, 241, 0.45);
}
.merge-viewer-row.disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.merge-viewer-row input[type="radio"] {
  width: auto;
  margin: 3px 0 0;
  flex-shrink: 0;
  cursor: pointer;
}
.merge-viewer-row.disabled input[type="radio"] {
  cursor: not-allowed;
}
.merge-viewer-row-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.merge-viewer-row-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.merge-viewer-row-name {
  font-weight: 600;
  color: var(--ink);
  font-size: 13.5px;
}
.merge-viewer-row-tag {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--soft-violet, rgba(139, 92, 246, 0.15));
  color: var(--accent-deep);
  line-height: 1.4;
}
.merge-viewer-row-path {
  font-size: 12px;
  color: var(--muted);
  font-family: Consolas, "Courier New", monospace;
  word-break: break-all;
  line-height: 1.5;
  white-space: normal;
}
.merge-viewer-preview {
  padding: 0 2px;
  font-size: 13px;
  color: var(--muted);
}
.merge-viewer-preview strong {
  color: var(--ink);
  font-weight: 600;
}
.merge-viewer-summary {
  line-height: 1.6;
}
.merge-viewer-ok {
  color: #10b981;
  font-weight: 500;
}
.merge-viewer-accent {
  color: var(--accent-deep);
  font-weight: 600;
}
.merge-viewer-error {
  color: #b91c1c;
  font-size: 13px;
}
.merge-viewer-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
</style>
