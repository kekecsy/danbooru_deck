<script setup>
import { ref } from 'vue';
import CrawlerPage from './components/CrawlerPage.vue';
import EditorPage from './components/EditorPage.vue';
import FavoritesPage from './components/FavoritesPage.vue';
import CaptionPage from './components/CaptionPage.vue';

const activePage = ref('crawler');
const editorSource = ref(null);
const captionSource = ref(null);

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
        <button class="nav-chip" :class="{ active: activePage === 'favorites' }" @click="activePage = 'favorites'">
          收藏
        </button>
        <button class="nav-chip" :class="{ active: activePage === 'caption' }" @click="activePage = 'caption'">
          描述
        </button>
      </div>
    </aside>

    <main class="content">
      <CrawlerPage v-show="activePage === 'crawler'" @edit-image="openEditorWithImage" @caption-image="openCaptionWithImage" />
      <EditorPage v-if="activePage === 'editor'" :source-item="editorSource" @back="activePage = 'crawler'" />
      <FavoritesPage v-if="activePage === 'favorites'" @edit-image="openEditorWithImage" />
      <CaptionPage v-if="activePage === 'caption'" :source-item="captionSource" @back="activePage = 'crawler'" />
    </main>
  </div>
</template>
