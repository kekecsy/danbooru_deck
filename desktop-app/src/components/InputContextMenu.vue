<script setup>
// 搜索框 / 文本框的右键菜单：粘贴、复制、剪切、全选、清空。
// 替代 Electron 在可编辑元素上偶尔不出默认菜单 / 中文用户更习惯显式中文按钮的场景。
// 父组件 App.vue 全局挂一个 contextmenu 监听，命中 input/textarea 时把目标元素传进来。
import { computed, onBeforeUnmount, ref, watch } from 'vue';

const props = defineProps({
  state: { type: Object, required: true },  // { open, x, y, target, isEditable, hasSelection, isReadonly, value }
});
const emit = defineEmits(['close']);

const MENU_W = 168;
const ITEM_H = 30;  // 含 padding，约 30px 一行
const MENU_ITEMS = 5;  // 最多 5 项，含 1px 分隔线高度
const MARGIN = 8;
const adjustedPos = ref({ x: 0, y: 0 });

// 根据 value 动态算"清空"项该不该显示：非空 + 非只读才显示
const items = computed(() => {
  const t = props.state.target;
  const readonly = !!props.state.isReadonly;
  const disabled = !t || t.disabled;
  const hasValue = (props.state.value || '').length > 0;
  return [
    { key: 'paste',  label: '粘贴',  hint: 'Ctrl+V', enabled: !readonly && !disabled },
    { key: 'copy',   label: '复制',  hint: 'Ctrl+C', enabled: props.state.hasSelection && !disabled },
    { key: 'cut',    label: '剪切',  hint: 'Ctrl+X', enabled: !readonly && props.state.hasSelection && !disabled },
    { key: 'select', label: '全选',  hint: 'Ctrl+A', enabled: !disabled },
    { key: 'clear',  label: '清空',  hint: '',       enabled: !readonly && hasValue && !disabled },
  ];
});

watch(() => props.state.open, (open) => {
  if (!open) return;
  // 视口边界兜底：菜单宽度 MENU_W，5 行含分隔线总高 ITEM_H * 5 + 6 约 156
  const MENU_H = ITEM_H * MENU_ITEMS + 6;
  let x = props.state.x;
  let y = props.state.y;
  if (x + MENU_W + MARGIN > window.innerWidth) x = window.innerWidth - MENU_W - MARGIN;
  if (y + MENU_H + MARGIN > window.innerHeight) y = window.innerHeight - MENU_H - MARGIN;
  if (x < MARGIN) x = MARGIN;
  if (y < MARGIN) y = MARGIN;
  adjustedPos.value = { x, y };
});

// 统一的"修改输入框值 + 让 v-model / @input 同步更新"小工具。
// 必须派发 input 事件，否则 Vue 不会重读 .value，搜索过滤不会重跑。
function setValue(target, newValue) {
  if (!target) return;
  const oldValue = target.value;
  target.value = newValue;
  if (oldValue !== newValue) {
    target.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

function focusAndSetCursor(target, start, end) {
  if (!target) return;
  target.focus({ preventScroll: true });
  try {
    target.setSelectionRange(start, end);
  } catch (e) {
    // 某些 input 类型不支持 setSelectionRange（number/email），安静吞掉
  }
}

async function readClipboardText() {
  // 优先 navigator.clipboard.readText；旧版 Electron 不可用时退回主进程 IPC
  if (navigator?.clipboard?.readText) {
    try {
      return await navigator.clipboard.readText();
    } catch (e) {
      // 权限/未激活焦点会抛错，落到主进程再试一次
    }
  }
  if (window.desktopAPI?.clipboard?.readText) {
    try {
      const t = await window.desktopAPI.clipboard.readText();
      return t || '';
    } catch (e) {
      return '';
    }
  }
  return '';
}

async function writeClipboardText(text) {
  if (navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) { /* fall through */ }
  }
  if (window.desktopAPI?.clipboard?.writeText) {
    try {
      await window.desktopAPI.clipboard.writeText(text);
      return true;
    } catch (e) { return false; }
  }
  return false;
}

async function doItem(key) {
  const t = props.state.target;
  emit('close');
  if (!t || t.disabled) return;

  if (key === 'select') {
    // 全选：直接 select()，并把焦点拉回
    try { t.select(); } catch (e) { /* ignore */ }
    focusAndSetCursor(t, 0, t.value.length);
    return;
  }

  if (key === 'clear') {
    if (t.readOnly) return;
    setValue(t, '');
    focusAndSetCursor(t, 0, 0);
    return;
  }

  if (key === 'copy') {
    const sel = (t.value || '').slice(t.selectionStart || 0, t.selectionEnd || 0);
    if (!sel) return;
    await writeClipboardText(sel);
    flashToast('已复制');
    return;
  }

  if (key === 'cut') {
    if (t.readOnly) return;
    const start = t.selectionStart || 0;
    const end = t.selectionEnd || 0;
    const sel = (t.value || '').slice(start, end);
    if (!sel) return;
    await writeClipboardText(sel);
    const next = (t.value || '').slice(0, start) + (t.value || '').slice(end);
    setValue(t, next);
    focusAndSetCursor(t, start, start);
    return;
  }

  if (key === 'paste') {
    if (t.readOnly) return;
    const text = await readClipboardText();
    if (!text) {
      flashToast('剪贴板为空');
      return;
    }
    const start = t.selectionStart || 0;
    const end = t.selectionEnd || 0;
    const v = t.value || '';
    const next = v.slice(0, start) + text + v.slice(end);
    setValue(t, next);
    // 把光标放在粘贴内容的尾部
    const cursor = start + text.length;
    focusAndSetCursor(t, cursor, cursor);
    return;
  }
}

// 极简 toast：派发 CustomEvent 给 App.vue 复用其 copyToast 系统
let toastTimer = null;
const localToast = ref({ show: false, text: '' });
function flashToast(text) {
  localToast.value = { show: true, text };
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { localToast.value.show = false; }, 1200);
}
onBeforeUnmount(() => { if (toastTimer) clearTimeout(toastTimer); });
</script>

<template>
  <Teleport to="body">
    <div
      v-if="state.open"
      class="input-ctx-menu"
      :style="{ left: adjustedPos.x + 'px', top: adjustedPos.y + 'px' }"
      @contextmenu.stop.prevent
    >
      <button
        v-for="(item, i) in items"
        :key="item.key"
        class="input-ctx-item"
        :class="{ divider: item.key === 'select' }"
        :disabled="!item.enabled"
        @click="doItem(item.key)"
      >
        <span class="input-ctx-label">{{ item.label }}</span>
        <span v-if="item.hint" class="input-ctx-hint">{{ item.hint }}</span>
      </button>
    </div>
    <Transition name="copy-toast">
      <div v-if="localToast.show" class="input-ctx-flash">{{ localToast.text }}</div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 复用 char-ctx-menu 风格：白底圆角 + 1px 边 + 阴影 + 柔和弹出动画 */
.input-ctx-menu {
  position: fixed;
  z-index: 10060;  /* 高于 viewer-overlay(9999) / caption-panel(10025) / toast(10030) / 合并 modal(10020) */
  min-width: 168px;
  background: var(--panel, #fdf6e3);
  border: 1px solid var(--line, rgba(0, 0, 0, 0.12));
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.22), 0 2px 4px rgba(0, 0, 0, 0.08);
  padding: 4px;
  font-size: 13px;
  user-select: none;
  animation: input-ctx-pop 0.12s ease-out;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
@keyframes input-ctx-pop {
  from { opacity: 0; transform: translateY(-4px) scale(0.98); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
.input-ctx-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 6px 10px;
  background: transparent;
  border: 0;
  border-radius: 5px;
  color: var(--ink, #2a1f10);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition: background 0.12s;
}
.input-ctx-item:hover:not(:disabled) {
  background: rgba(var(--accent-rgb, 196 130 60), 0.14);
}
.input-ctx-item:disabled {
  color: rgba(0, 0, 0, 0.32);
  cursor: not-allowed;
}
/* 在「全选」上方加分隔线，把"破坏性"和"导航性"操作视觉分组 */
.input-ctx-item.divider {
  border-top: 1px solid var(--line, rgba(0, 0, 0, 0.08));
  border-radius: 0 0 5px 5px;
  padding-top: 7px;
  margin-top: 3px;
}
.input-ctx-label { flex: 1 1 auto; }
.input-ctx-hint {
  font-size: 11px;
  color: var(--muted, #806a4a);
  font-family: Consolas, "Courier New", monospace;
  opacity: 0.7;
}

.input-ctx-flash {
  position: fixed;
  bottom: 28px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(20, 20, 28, 0.86);
  color: #fff;
  font-size: 12.5px;
  padding: 6px 14px;
  border-radius: 999px;
  pointer-events: none;
  z-index: 10070;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}
</style>
