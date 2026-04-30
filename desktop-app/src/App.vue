<script setup>
import { ref } from 'vue';
import CrawlerPage from './components/CrawlerPage.vue';
import EditorPage from './components/EditorPage.vue';

const activePage = ref('crawler');
const editorSource = ref(null);

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
    </aside>

    <main class="content">
      <CrawlerPage v-show="activePage === 'crawler'" @edit-image="openEditorWithImage" />
      <EditorPage v-if="activePage === 'editor'" :source-item="editorSource" @back="activePage = 'crawler'" />
    </main>
  </div>
</template>
