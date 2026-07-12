<script setup>
import { ref, watch } from 'vue';
import CrawlerPage from './components/CrawlerPage.vue';
import EditorPage from './components/EditorPage.vue';
import FavoritesPage from './components/FavoritesPage.vue';
import CaptionPage from './components/CaptionPage.vue';
import PosePage from './components/PosePage.vue';

const activePage = ref('crawler');
const editorSource = ref(null);
const captionSource = ref(null);
const navOpen = ref(localStorage.getItem('desktopNavOpen') === 'true');

watch(navOpen, value => localStorage.setItem('desktopNavOpen', String(value)));

const navItems = [
  { id: 'crawler', icon: '↓', label: '抓图' },
  { id: 'editor', icon: '▦', label: '打码' },
  { id: 'favorites', icon: '★', label: '收藏' },
  { id: 'caption', icon: '✎', label: '描述' },
  { id: 'pose', icon: '◇', label: '姿态' },
];

function selectPage(page) {
  activePage.value = page;
  navOpen.value = false;
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
    <aside class="sidebar mini-sidebar nav-drawer" :class="{ open: navOpen }">
      <button
        class="nav-drawer-toggle"
        :title="navOpen ? '收起功能导航' : '展开功能导航'"
        :aria-label="navOpen ? '收起功能导航' : '展开功能导航'"
        @click="navOpen = !navOpen"
      >{{ navOpen ? '‹' : '›' }}</button>
      <span v-if="navOpen" class="eyebrow">Desktop</span>
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
          <span class="nav-chip-icon">{{ item.icon }}</span>
          <span v-if="navOpen" class="nav-chip-label">{{ item.label }}</span>
        </button>
      </div>
    </aside>

    <main class="content">
      <CrawlerPage v-show="activePage === 'crawler'" @edit-image="openEditorWithImage" @caption-image="openCaptionWithImage" />
      <EditorPage v-if="activePage === 'editor'" :source-item="editorSource" @back="activePage = 'crawler'" />
      <FavoritesPage v-if="activePage === 'favorites'" @edit-image="openEditorWithImage" />
      <CaptionPage v-if="activePage === 'caption'" :source-item="captionSource" @back="activePage = 'crawler'" />
      <PosePage v-if="activePage === 'pose'" />
    </main>
  </div>
</template>
