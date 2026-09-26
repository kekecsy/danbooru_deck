<script setup>
// 跨盘合并 viewer_data.json 的配置 + 预览 + 确认 modal。
// 替代原本依赖 window.prompt / window.confirm 的实现——Electron renderer 里
// 这俩 API 是空实现（点完没反应），详见 FavoritesPage.vue 的同款注释。
import { computed, ref, watch } from 'vue';

const props = defineProps({
  state: { type: Object, required: true },   // { open, date, roots: [{id,label,path,isDefault}] }
  isBusy: { type: Boolean, default: false },
});
const emit = defineEmits(['update:open', 'success', 'roots-changed']);

const roots = ref([]);
const rootSummary = ref([]);
const rootsBusy = ref(false);
const sourceIdx = ref(null);
const targetIdx = ref(null);
const moveFiles = ref(false);
const preview = ref(null);
const previewing = ref(false);
const executing = ref(false);
const error = ref('');

function normalizeRoots(input) {
  return (Array.isArray(input) ? input : []).map(root => ({
    ...root,
    isDefault: !!(root?.isDefault || root?.is_default),
  }));
}

watch(() => props.state.open, (open) => {
  if (open) {
    roots.value = normalizeRoots(props.state.roots);
    sourceIdx.value = null;
    targetIdx.value = null;
    moveFiles.value = false;
    preview.value = null;
    previewing.value = false;
    executing.value = false;
    error.value = '';
    refreshRootSummary();
  }
});

watch(() => props.state.roots, (next) => {
  roots.value = normalizeRoots(next);
}, { deep: true });

async function refreshRootSummary() {
  if (!props.state.date) return;
  try {
    const resp = await fetch(`http://127.0.0.1:18765/api/library_root_summary?date=${encodeURIComponent(props.state.date)}`);
    const data = await resp.json();
    rootSummary.value = data?.ok && Array.isArray(data.roots) ? data.roots : [];
  } catch {
    rootSummary.value = [];
  }
}

async function addRoot() {
  if (!window.desktopAPI?.libraryRoots?.add || rootsBusy.value) {
    error.value = '当前运行环境不支持选择图库文件夹，请从 Electron 桌面端操作';
    return;
  }
  rootsBusy.value = true;
  error.value = '';
  try {
    const result = await window.desktopAPI.libraryRoots.add();
    if (result?.canceled) return;
    if (!result?.ok) {
      error.value = result?.msg || '添加图库文件夹失败';
      return;
    }
    roots.value = Array.isArray(result.roots) ? normalizeRoots(result.roots) : roots.value;
    emit('roots-changed', roots.value);
    await refreshRootSummary();
  } catch (e) {
    error.value = `添加图库文件夹失败：${e?.message || e}`;
  } finally {
    rootsBusy.value = false;
  }
}

async function removeRoot(root) {
  if (!root || root.isDefault || rootsBusy.value) return;
  rootsBusy.value = true;
  error.value = '';
  try {
    const result = await window.desktopAPI.libraryRoots.remove(root.id);
    if (!result?.ok) {
      error.value = result?.msg || '移除图库文件夹失败';
      return;
    }
    roots.value = Array.isArray(result.roots)
      ? normalizeRoots(result.roots)
      : roots.value.filter(item => item.id !== root.id);
    sourceIdx.value = null;
    targetIdx.value = null;
    preview.value = null;
    emit('roots-changed', roots.value);
    await refreshRootSummary();
  } catch (e) {
    error.value = `移除图库文件夹失败：${e?.message || e}`;
  } finally {
    rootsBusy.value = false;
  }
}

watch([sourceIdx, targetIdx, moveFiles], async ([s, t]) => {
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
    const resp = await fetch('http://127.0.0.1:18765/api/merge_viewer_data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: props.state.date,
        source_root: roots.value[sourceIdx.value].path,
        target_root: roots.value[targetIdx.value].path,
        dry_run: true,
        move_files: moveFiles.value
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
  if (!preview.value || (
    preview.value.merged_count === 0
    && (!moveFiles.value || !(preview.value.move_count > 0))
  )) return;
  executing.value = true;
  try {
    const resp = await fetch('http://127.0.0.1:18765/api/merge_viewer_data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: props.state.date,
        source_root: roots.value[sourceIdx.value].path,
        target_root: roots.value[targetIdx.value].path,
        dry_run: false,
        move_files: moveFiles.value
      })
    });
    const data = await resp.json();
    if (!data || data.ok !== true) {
      error.value = data?.msg || '合并失败';
      return;
    }
    const source = roots.value[sourceIdx.value];
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
    && preview.value
    && (preview.value.merged_count > 0 || (moveFiles.value && preview.value.move_count > 0))
);
const controlsDisabled = computed(() => props.isBusy || previewing.value || executing.value);
function summaryFor(root) {
  return rootSummary.value.find(item => item.id === root.id);
}
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

      <div class="merge-viewer-root-tools">
        <div>
          <strong>图库根目录</strong>
          <span class="muted compact-text">同一天可同时存在于多个 root；移除只取消管理，不删除磁盘文件。</span>
        </div>
        <button class="secondary" @click="addRoot" :disabled="rootsBusy || executing">添加文件夹</button>
      </div>

      <label v-if="hasSourceAndTarget" class="merge-viewer-move-option">
        <input v-model="moveFiles" type="checkbox" :disabled="controlsDisabled" />
        <span>
          <strong>同时移动源图片到目标 root</strong>
          <small>仅移动源日期目录中的图片；目标已有同名文件时不覆盖，保留源文件并报告冲突。</small>
        </span>
      </label>

      <div class="merge-viewer-section">
        <div class="merge-viewer-section-title">
          <span>源 root</span>
          <span class="muted compact-text" style="font-size: 11px;">从哪增量同步</span>
        </div>
        <div class="merge-viewer-list">
          <label
            v-for="(r, idx) in roots"
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
                <span v-if="r.isDefault || r.is_default" class="merge-viewer-row-tag">默认</span>
              </div>
              <div class="merge-viewer-row-path">{{ r.path }}</div>
              <div v-if="summaryFor(r)" class="merge-viewer-row-meta">
                {{ summaryFor(r).accessible ? `${summaryFor(r).media_count} 张图 · ${summaryFor(r).viewer_count} 条 viewer_data` : '当前日期不可访问' }}
              </div>
              <button
                v-if="!(r.isDefault || r.is_default)"
                type="button"
                class="merge-viewer-remove"
                :disabled="rootsBusy || executing"
                title="仅从 library_roots.json 移除，不删除文件"
                @click.stop="removeRoot(r)"
              >移除</button>
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
            v-for="(r, idx) in roots"
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
                <span v-if="r.isDefault || r.is_default" class="merge-viewer-row-tag">默认</span>
              </div>
              <div class="merge-viewer-row-path">{{ r.path }}</div>
              <div v-if="summaryFor(r)" class="merge-viewer-row-meta">
                {{ summaryFor(r).accessible ? `${summaryFor(r).media_count} 张图 · ${summaryFor(r).viewer_count} 条 viewer_data` : '当前日期不可访问' }}
              </div>
              <button
                v-if="!(r.isDefault || r.is_default)"
                type="button"
                class="merge-viewer-remove"
                :disabled="rootsBusy || executing"
                title="仅从 library_roots.json 移除，不删除文件"
                @click.stop="removeRoot(r)"
              >移除</button>
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
          <span v-if="moveFiles" class="merge-viewer-move-summary">
            · 将移动 <strong>{{ preview.move_count || 0 }}</strong> 张
            <span v-if="preview.move_conflict_count">，冲突 {{ preview.move_conflict_count }} 张</span>
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
.merge-viewer-root-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.58);
  font-size: 13px;
}
.merge-viewer-root-tools > div {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.merge-viewer-move-option {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid rgba(16, 185, 129, 0.28);
  border-radius: 8px;
  background: rgba(16, 185, 129, 0.06);
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
}
.merge-viewer-move-option input {
  width: auto;
  margin-top: 3px;
  flex: 0 0 auto;
}
.merge-viewer-move-option span {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.merge-viewer-move-option small {
  color: var(--muted);
  font-size: 11.5px;
  line-height: 1.45;
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
.merge-viewer-row-meta {
  font-size: 11.5px;
  color: var(--accent-deep);
}
.merge-viewer-remove {
  align-self: flex-start;
  margin-top: 2px;
  padding: 2px 8px;
  border: 1px solid rgba(185, 28, 28, 0.25);
  border-radius: 4px;
  background: rgba(185, 28, 28, 0.06);
  color: #b91c1c;
  font-size: 11px;
  line-height: 1.6;
  cursor: pointer;
}
.merge-viewer-remove:hover:not(:disabled) {
  background: rgba(185, 28, 28, 0.12);
}
.merge-viewer-remove:disabled {
  opacity: 0.45;
  cursor: not-allowed;
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
.merge-viewer-move-summary {
  color: #047857;
}
.merge-viewer-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}
</style>
