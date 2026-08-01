<script setup>
// 教程弹窗：hosts 直连 + ffmpeg 安装。状态（open）由父组件 v-model 传入。
// notify 事件用于通知父组件显示 toast —— 保持 toast 系统单一来源。
import { computed } from 'vue';

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  safeMode: { type: Boolean, default: true },
});
const emit = defineEmits(['update:modelValue', 'notify']);

const open = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
});

function close() { open.value = false; }

async function openHostsFolder() {
  await window.desktopAPI.external.open('file:///C:/Windows/System32/drivers/etc/');
}

async function openFfmpegTutorial() {
  // 知乎 ffmpeg 安装教程；在系统默认浏览器打开
  await window.desktopAPI.external.open('https://zhuanlan.zhihu.com/p/662421567');
}

async function copyHostsSnippet() {
  const text = props.safeMode
    ? '104.26.11.39 safebooru.donmai.us'
    : '104.26.11.39 danbooru.donmai.us';
  try {
    await navigator.clipboard.writeText(text);
    emit('notify', { message: '已复制 hosts 内容', type: 'success' });
  } catch (e) {
    emit('notify', { message: '复制失败：' + (e.message || e), type: 'error' });
  }
}
</script>

<template>
  <div
    v-if="open"
    class="viewer-overlay"
    @click.self="close"
    style="z-index: 10000; display: flex; justify-content: center; align-items: center;"
  >
    <div
      class="card panel"
      style="width: 540px; max-width: 92vw; background: rgba(255, 255, 255, 0.96); box-shadow: 0 20px 50px rgba(0,0,0,0.3); display: flex; flex-direction: column; gap: 16px; padding: 22px 24px;"
    >
      <div style="display: flex; align-items: center; justify-content: space-between;">
        <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">教程 / Tutorials</h3>
        <button class="ghost" @click="close" style="min-width: 36px;">×</button>
      </div>

      <div class="tutorial-card">
        <div class="tutorial-card-head">
          <span class="tutorial-card-index">1</span>
          <div>
            <div class="tutorial-card-title">修改 hosts · 让 Danbooru 可直连</div>
            <div class="tutorial-card-desc">当 Danbooru / Safebooru 走默认 DNS 解析失败时，把下列 IP 写进 hosts 即可直连。</div>
          </div>
        </div>
        <ol class="tutorial-steps">
          <li>用记事本（管理员）打开：<code>C:\Windows\System32\drivers\etc\hosts</code></li>
          <li>在文件末尾添加下面这一行（按你当前模式选一条）：</li>
        </ol>
        <textarea
          readonly
          style="width: 100%; height: 56px; font-family: Consolas, monospace; font-size: 13px; resize: none; background: rgba(0,0,0,0.04); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: 10px; outline: none; cursor: text;"
          onfocus="this.select()"
        >{{ safeMode ? '104.26.11.39 safebooru.donmai.us' : '104.26.11.39 danbooru.donmai.us' }}</textarea>
        <div style="display: flex; gap: 10px; margin-top: 10px; justify-content: flex-end;">
          <button class="secondary" @click="openHostsFolder">打开 hosts 所在目录</button>
          <button @click="copyHostsSnippet">复制 hosts 内容</button>
        </div>
      </div>

      <div class="tutorial-card">
        <div class="tutorial-card-head">
          <span class="tutorial-card-index">2</span>
          <div>
            <div class="tutorial-card-title">安装 ffmpeg · 让 zip 动画能转 GIF、MP4 缩略图能取首帧</div>
            <div class="tutorial-card-desc">没装 ffmpeg 时，"批量转 GIF" 按钮和 MP4 卡片缩略图会失效。点下面按钮看知乎图文教程。</div>
          </div>
        </div>
        <div style="display: flex; gap: 10px; margin-top: 8px; justify-content: flex-end;">
          <a
            href="https://zhuanlan.zhihu.com/p/662421567"
            target="_blank"
            rel="noopener"
            class="tutorial-link-btn"
            @click.prevent="openFfmpegTutorial"
          >打开知乎 ffmpeg 教程 →</a>
        </div>
      </div>

      <div style="display: flex; justify-content: flex-end; margin-top: 4px;">
        <button @click="close" style="min-width: 80px;">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 教程弹窗里的卡片 */
.tutorial-card {
  border: 1px solid rgba(30, 41, 82, 0.16);
  border-radius: 10px;
  padding: 14px 16px;
  background: rgba(255, 252, 246, 0.7);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.tutorial-card-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.tutorial-card-index {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  font-weight: 800;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  margin-top: 2px;
}
.tutorial-card-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  line-height: 1.4;
}
.tutorial-card-desc {
  font-size: 12px;
  color: var(--muted, #846a55);
  margin-top: 2px;
  line-height: 1.5;
}
.tutorial-steps {
  margin: 4px 0 0;
  padding-left: 22px;
  font-size: 12px;
  color: var(--ink);
  line-height: 1.7;
}
.tutorial-steps code {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: Consolas, monospace;
  font-size: 11.5px;
}
.tutorial-link-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 7px;
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  text-decoration: none;
  font-size: 13px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  transition: filter 0.15s ease;
}
.tutorial-link-btn:hover {
  filter: brightness(1.08);
}
</style>
