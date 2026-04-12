<script setup>
import { computed, ref } from 'vue';
import CrawlerPage from './components/CrawlerPage.vue';
import EditorPage from './components/EditorPage.vue';

const activePage = ref('crawler');
const editorSource = ref(null);
const editorHint = computed(() => {
  if (editorSource.value?.filename) return `当前图片: ${editorSource.value.filename}`;
  return '可直接选择本地图片，或从抓图结果进入';
});

function openEditorWithImage(item) {
  editorSource.value = item;
  activePage.value = 'editor';
}
</script>

<template>
  <div class="shell shell-compact">
    <aside class="sidebar mini-sidebar">
      <span class="eyebrow">Desktop</span>
      <div class="mini-nav vertical">
        <button class="nav-chip" :class="{ active: activePage === 'crawler' }" @click="activePage = 'crawler'">
          抓图
        </button>
        <button class="nav-chip" :class="{ active: activePage === 'editor' }" @click="activePage = 'editor'">
          打码
        </button>
      </div>
      <p class="sidebar-note">{{ activePage === 'editor' ? editorHint : '先抓图，再挑图进入打码。' }}</p>
    </aside>

    <main class="content">
      <CrawlerPage v-if="activePage === 'crawler'" @edit-image="openEditorWithImage" />
      <EditorPage v-else :source-item="editorSource" @back="activePage = 'crawler'" />
    </main>
  </div>
</template>
