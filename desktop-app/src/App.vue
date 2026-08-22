<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';
import CrawlerPage from './components/CrawlerPage.vue';
import EditorPage from './components/EditorPage.vue';
import FavoritesPage from './components/FavoritesPage.vue';
import CaptionPage from './components/CaptionPage.vue';
import PosePage from './components/PosePage.vue';
import InputContextMenu from './components/InputContextMenu.vue';

const activePage = ref('crawler');
const editorSource = ref(null);
const captionSource = ref(null);
const storedNavState = localStorage.getItem('desktopNavOpen');
const navOpen = ref(storedNavState === null ? true : storedNavState === 'true');

watch(navOpen, value => localStorage.setItem('desktopNavOpen', String(value)));

// 全局"右键复制成功"轻提示：主进程拦截 context-menu 后会推 IPC 过来。
// showToast 是各组件私有的，这里自己挂一个最简版本，不影响既有交互。
const copyToast = ref({ show: false, text: '' });
let copyToastTimer = null;
let unsubscribeContextCopy = null;
function flashCopyToast(data) {
  const text = data.length > 40
    ? `已复制 ${data.length} 字符：「${data.preview}…」`
    : `已复制 ${data.length} 字符`;
  copyToast.value.text = text;
  copyToast.value.show = true;
  if (copyToastTimer) clearTimeout(copyToastTimer);
  copyToastTimer = setTimeout(() => { copyToast.value.show = false; }, 1600);
}

// 全局"搜索框 / 文本框右键菜单"：命中 input/textarea 时拦截默认菜单，
// 弹 InputContextMenu 提供 粘贴 / 复制 / 剪切 / 全选 / 清空。
// 已有自定义菜单（char chip / pose SVG / pic_web canvas）都已经在 target 上
// @contextmenu.prevent，主进程会拿到"没有 selectionText"自然放过，无冲突。
const inputCtxMenu = ref({
  open: false, x: 0, y: 0,
  target: null, isReadonly: false, hasSelection: false,
  value: '',
});
function isEditableTarget(el) {
  if (!el || el.nodeType !== 1) return false;
  const tag = el.tagName;
  if (tag === 'TEXTAREA') return true;
  if (tag === 'INPUT') {
    // type=text/search/url/email/tel/password/number 都允许粘贴；
    // type=button/checkbox/radio/submit 等"非文本输入"不弹菜单
    const t = (el.type || 'text').toLowerCase();
    return ['text', 'search', 'url', 'email', 'tel', 'password', 'number', ''].includes(t);
  }
  return false;
}
function onGlobalContextMenu(event) {
  const el = event.target;
  if (!isEditableTarget(el)) return;  // 让别的自定义菜单 / 主进程默认行为照旧生效
  event.preventDefault();
  event.stopPropagation();
  const start = el.selectionStart ?? 0;
  const end = el.selectionEnd ?? 0;
  inputCtxMenu.value = {
    open: true,
    x: event.clientX,
    y: event.clientY,
    target: el,
    isReadonly: !!el.readOnly,
    hasSelection: end > start && (el.value || '').slice(start, end).length > 0,
    value: el.value || '',
  };
}
function closeInputCtxMenu() {
  if (inputCtxMenu.value.open) {
    inputCtxMenu.value = { ...inputCtxMenu.value, open: false, target: null };
  }
}
function onInputCtxDismiss(event) {
  if (!inputCtxMenu.value.open) return;
  if (event?.target && typeof event.target.closest === 'function' && event.target.closest('.input-ctx-menu')) return;
  closeInputCtxMenu();
}
function onInputCtxKey(event) {
  if (event.key === 'Escape') closeInputCtxMenu();
}

onMounted(() => {
  if (window.desktopAPI?.onContextCopy) {
    unsubscribeContextCopy = window.desktopAPI.onContextCopy(flashCopyToast);
  }
  // capture 阶段：先于任何子组件的 @contextmenu 拦截
  document.addEventListener('contextmenu', onGlobalContextMenu, true);
  document.addEventListener('mousedown', onInputCtxDismiss, true);
  document.addEventListener('scroll', onInputCtxDismiss, true);
  document.addEventListener('keydown', onInputCtxKey, true);
});
onBeforeUnmount(() => {
  if (unsubscribeContextCopy) unsubscribeContextCopy();
  if (copyToastTimer) clearTimeout(copyToastTimer);
  document.removeEventListener('contextmenu', onGlobalContextMenu, true);
  document.removeEventListener('mousedown', onInputCtxDismiss, true);
  document.removeEventListener('scroll', onInputCtxDismiss, true);
  document.removeEventListener('keydown', onInputCtxKey, true);
});

const navItems = [
  { id: 'crawler', icon: 'M12 3v12m0 0 4-4m-4 4-4-4M5 19h14', label: '抓图', hint: 'Fetch' },
  { id: 'editor', icon: 'M4 7V4h3M17 4h3v3M20 17v3h-3M7 20H4v-3M8 8h8v8H8z', label: '打码', hint: 'Edit' },
  { id: 'favorites', icon: 'm12 3 2.75 5.57 6.15.9-4.45 4.33 1.05 6.12L12 18.1 6.5 21l1.05-6.12L3.1 10.55l6.15-.9L12 3z', label: '收藏', hint: 'Library' },
  { id: 'caption', icon: 'M5 19.5 6.5 14 15 5.5a2.12 2.12 0 0 1 3 3L9.5 17 5 19.5zM13.5 7l3 3', label: '描述', hint: 'Caption' },
  { id: 'pose', icon: 'M12 5.25a2.25 2.25 0 1 0 0-4.5 2.25 2.25 0 0 0 0 4.5zM8 9l4-2 4 2m-4-2v6m-4 8 4-8 4 8', label: '姿态', hint: 'Pose' },
];

function selectPage(page) {
  activePage.value = page;
  if (window.innerWidth <= 720) navOpen.value = false;
}

function openEditorWithImage(item) {
  editorSource.value = item;
  activePage.value = 'editor';
}

function openCaptionWithImage(item) {
  captionSource.value = item;
  activePage.value = 'caption';
}
</script>

<template>
  <div class="shell shell-compact" :class="{ 'nav-open': navOpen }">
    <div class="app-atmosphere" aria-hidden="true">
      <span></span><span></span><span></span>
    </div>

    <aside class="sidebar app-sidebar nav-drawer" :class="{ open: navOpen }">
      <div class="sidebar-brand">
        <div class="brand-mark" aria-hidden="true">
          <span>DD</span>
        </div>
        <div v-if="navOpen" class="brand-copy">
          <strong>Danbooru Deck</strong>
          <small>anime workspace</small>
        </div>
      </div>

      <button
        class="nav-drawer-toggle"
        :title="navOpen ? '收起功能导航' : '展开功能导航'"
        :aria-label="navOpen ? '收起功能导航' : '展开功能导航'"
        @click="navOpen = !navOpen"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path :d="navOpen ? 'm15 18-6-6 6-6' : 'm9 18 6-6-6-6'" />
        </svg>
      </button>

      <div class="mini-nav vertical">
        <button
          v-for="item in navItems"
          :key="item.id"
          class="nav-chip"
          :class="{ active: activePage === item.id }"
          :title="item.label"
          :aria-label="item.label"
          @click="selectPage(item.id)"
        >
          <span class="nav-chip-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="item.icon" /></svg>
          </span>
          <span v-if="navOpen" class="nav-chip-copy">
            <strong class="nav-chip-label">{{ item.label }}</strong>
            <small>{{ item.hint }}</small>
          </span>
          <span class="nav-active-dot" aria-hidden="true"></span>
        </button>
      </div>

      <div v-if="navOpen" class="sidebar-foot">
        <span class="local-status"><i></i> Local workspace</span>
        <small>轻量模式 · v0.1</small>
      </div>
    </aside>

    <button v-if="navOpen" class="nav-scrim" aria-label="关闭导航" @click="navOpen = false"></button>

    <main class="content">
      <div class="content-frame">
        <CrawlerPage v-show="activePage === 'crawler'" @edit-image="openEditorWithImage" @caption-image="openCaptionWithImage" />
        <EditorPage v-if="activePage === 'editor'" :source-item="editorSource" @back="activePage = 'crawler'" />
        <FavoritesPage v-if="activePage === 'favorites'" @edit-image="openEditorWithImage" />
        <CaptionPage v-if="activePage === 'caption'" :source-item="captionSource" @back="activePage = 'crawler'" />
        <PosePage v-if="activePage === 'pose'" />
      </div>
    </main>

    <!-- 全局右键复制成功轻提示：固定底部、pointer-events: none，不抢交互 -->
    <Transition name="copy-toast">
      <div v-if="copyToast.show" class="copy-toast-pill">{{ copyToast.text }}</div>
    </Transition>

    <!-- 全局搜索框 / 文本框右键菜单：粘贴 / 复制 / 剪切 / 全选 / 清空 -->
    <InputContextMenu :state="inputCtxMenu" @close="closeInputCtxMenu" />
  </div>
</template>
