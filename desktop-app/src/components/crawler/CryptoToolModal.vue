<script setup>
// ID 加密 / 解密工具弹窗。
// state 由父组件持有（state prop 传 ref，自动 unwrap）。
// 工具方法（compressIds / decompressIds / parsePastedIds）从 src/utils/idCodec.js 导入。
// 「把已选 ID 载入输入框」是父组件能力，emit load-from-selection 让父组件执行；
// 其他加/解密/复制/交换都在本组件内完成，toast 通过 notify emit。
import { computed } from 'vue';
import { parsePastedIds, compressIds, decompressIds } from '../../utils/idCodec.js';

const props = defineProps({
  state: { type: Object, required: true },          // { open, input, output }
  selectionCount: { type: Number, default: 0 },
});
const emit = defineEmits(['update:open', 'load-from-selection', 'notify']);

const open = computed({
  get: () => props.state.open,
  set: (v) => emit('update:open', v),
});
function close() { open.value = false; }
function clearInput() { props.state.input = ''; }
function swapIO() {
  const tmp = props.state.input;
  props.state.input = props.state.output;
  props.state.output = tmp;
}

function encrypt() {
  const ids = parsePastedIds(props.state.input);
  if (!ids.length) {
    emit('notify', { message: '没解析到任何 ID', type: 'warning' });
    return;
  }
  const out = compressIds(ids);
  props.state.output = out;
  const savedPct = props.state.input.length > 0
    ? Math.round((1 - out.length / props.state.input.length) * 100)
    : 0;
  emit('notify', { message: `加密完成 · ${ids.length} 个 ID · ${out.length} 字符（比输入省 ${savedPct >= 0 ? savedPct : 0}%）`, type: 'success' });
}

function decrypt() {
  const decoded = decompressIds(props.state.input);
  if (!decoded || !decoded.length) {
    // 不是压缩格式：尝试明文解析，让用户也能用来"规范化/去重"
    const fallback = parsePastedIds(props.state.input);
    if (!fallback.length) {
      emit('notify', { message: '没解析到任何 ID', type: 'warning' });
      return;
    }
    props.state.output = fallback.join(',');
    emit('notify', { message: `输入是明文，已规范化为 ${fallback.length} 个 ID（${props.state.output.length} 字符）`, type: 'info' });
    return;
  }
  props.state.output = decoded.join(',');
  emit('notify', { message: `解密完成 · ${decoded.length} 个 ID（${props.state.output.length} 字符）`, type: 'success' });
}

async function copyOutput() {
  if (!props.state.output) {
    emit('notify', { message: '输出框是空的', type: 'warning' });
    return;
  }
  try {
    await navigator.clipboard.writeText(props.state.output);
    emit('notify', { message: `已复制输出（${props.state.output.length} 字符）`, type: 'success' });
  } catch (e) {
    emit('notify', { message: `复制失败: ${e.message || e}`, type: 'error' });
  }
}
</script>

<template>
  <div
    v-if="state.open"
    class="viewer-overlay"
    @click.self="close"
    style="z-index: 10000; display: flex; justify-content: center; align-items: center; padding: 24px;"
  >
    <div class="crypto-tool-card">
      <div class="crypto-tool-head">
        <div>
          <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">🗜 ID 加密 / 解密工具</h3>
          <p class="muted compact-text" style="margin: 4px 0 0;">把长长的 ID 列表压成短字符串方便分享；也能反向还原。</p>
        </div>
        <button class="ghost" @click="close" style="color: var(--muted);">×</button>
      </div>

      <div class="crypto-tool-row">
        <span class="crypto-tool-label">输入</span>
        <span class="muted compact-text">{{ state.input.length }} 字符</span>
        <button class="ghost crypto-tool-mini" @click="emit('load-from-selection')" :disabled="!selectionCount" title="把当前选择的所有 IDs 填到输入框">⬇ 载入当前选择 ({{ selectionCount }})</button>
        <button class="ghost crypto-tool-mini" @click="clearInput" :disabled="!state.input">清空</button>
      </div>
      <textarea
        v-model="state.input"
        class="crypto-tool-textarea"
        rows="4"
        placeholder="粘贴你要加密的明文 IDs（逗号 / 空格 / 换行 / URL 都行），或粘贴压缩格式 dbids:... 用于解密"
      />

      <div class="crypto-tool-actions">
        <button class="secondary" @click="encrypt" :disabled="!state.input.trim()">加密（压缩）</button>
        <button class="secondary" @click="decrypt" :disabled="!state.input.trim()">🔓 解密（还原）</button>
        <button class="ghost" @click="swapIO" :disabled="!state.output" title="把输出搬回输入，方便再次加/解密">⇅ 交换</button>
      </div>

      <div class="crypto-tool-row">
        <span class="crypto-tool-label">输出</span>
        <span class="muted compact-text">{{ state.output.length }} 字符</span>
      </div>
      <textarea
        v-model="state.output"
        class="crypto-tool-textarea"
        rows="4"
        readonly
        placeholder="结果会出现在这里"
      />

      <div class="crypto-tool-foot">
        <span class="muted compact-text">压缩格式 = base36 增量编码，100+ 个 ID 通常省 60%+</span>
        <button class="ghost" @click="close">关闭</button>
        <button @click="copyOutput" :disabled="!state.output">复制输出</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.crypto-tool-mini {
  padding: 3px 8px;
  font-size: 11px;
  border-radius: 6px;
}
.crypto-tool-row .crypto-tool-mini + .crypto-tool-mini {
  margin-left: 4px;
}
.crypto-tool-textarea {
  width: 100%;
  font-family: Consolas, monospace;
  font-size: 12px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.85);
  color: var(--ink);
  resize: vertical;
}
.crypto-tool-textarea[readonly] {
  background: rgba(0, 0, 0, 0.03);
}
.crypto-tool-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.crypto-tool-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}
.crypto-tool-foot > .muted {
  flex: 1;
  font-size: 11px;
}
</style>
