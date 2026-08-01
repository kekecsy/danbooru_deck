<script setup>
// 未翻译角色 / 角色字典 列表弹窗。
// 状态由父组件持有（state prop 传 ref，自动 unwrap），过滤、搜索、保存、导入业务都在父组件。
// 纯展示 + 事件分发。
import { computed } from 'vue';

const props = defineProps({
  state: { type: Object, required: true },          // { open, mode, list, search, loading, importing, targetTag }
  filtered: { type: Array, required: true },        // 父组件算好的过滤后列表
  selectedDate: { type: String, default: '' },      // 顶部标题里展示的当前画廊日期
});
const emit = defineEmits(['update:open', 'search', 'open-detail', 'import']);

const open = computed({
  get: () => props.state.open,
  set: (v) => emit('update:open', v),
});

function close() { open.value = false; }
</script>

<template>
  <div v-if="state.open" class="viewer-overlay translation-overlay" @click.self="close">
    <div class="translation-card">
      <div class="translation-head">
        <div>
          <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">
            {{ state.mode === 'dictionary' ? '角色字典' : `未翻译角色 · ${selectedDate || ''}` }}
          </h3>
          <p class="muted compact-text" style="margin: 4px 0 0;">
            {{ state.mode === 'dictionary' ? '可按 tag、中文名或 source_hint 搜索' : `共 ${state.list.length} 个 · 已筛选 ${filtered.length} 个` }}
          </p>
        </div>
        <button class="ghost" @click="close" style="color: var(--muted);">×</button>
      </div>

      <div class="character-dict-search-row">
        <input
          v-model="state.search"
          class="search-input"
          type="text"
          :placeholder="state.mode === 'dictionary' ? '搜索 tag、中文名或作品来源' : '搜索 tag 或回退名'"
          @keyup.enter="state.mode === 'dictionary' && emit('search')"
        />
        <button v-if="state.mode === 'dictionary'" class="secondary" @click="emit('search')">搜索</button>
      </div>

      <button
        v-if="state.mode === 'dictionary' && state.targetTag"
        class="character-dict-target"
        @click="emit('open-detail', { tag: state.targetTag, fallback_name: state.targetTag })"
      >编辑当前精确 tag：{{ state.targetTag }}</button>

      <div class="translation-list">
        <div v-if="state.loading" class="gallery-empty" style="min-height: 120px;">正在加载...</div>
        <div v-else-if="!filtered.length" class="gallery-empty" style="min-height: 120px;">
          {{ state.mode === 'dictionary' ? '没有匹配的字典记录，可直接编辑当前精确 tag' : (state.list.length ? '没有匹配的角色' : '当前日期没有未翻译的角色') }}
        </div>
        <div
          v-else
          v-for="item in filtered"
          :key="item.tag"
          class="translation-row"
          @click="emit('open-detail', item)"
        >
          <div class="translation-row-main">
            <span class="translation-row-tag">{{ item.tag }}</span>
            <span class="translation-row-fallback">
              {{ state.mode === 'dictionary' ? (item.chinese_name || item.fallback_name) : item.fallback_name }}
              <template v-if="state.mode === 'dictionary' && item.source_hint"> · {{ item.source_hint }}</template>
            </span>
          </div>
          <span v-if="state.mode === 'untranslated'" class="translation-row-count">出现 {{ item.post_count }} 次</span>
          <span v-else class="translation-row-count">编辑</span>
        </div>
      </div>

      <div class="translation-foot">
        <span class="muted compact-text">保存单条记录后会立即同步到画廊；右键角色标签可快速进入这里。</span>
        <div style="display: flex; gap: 8px;">
          <button class="ghost" @click="close" style="color: var(--accent-deep);">关闭</button>
          <button
            v-if="state.mode === 'untranslated'"
            @click="emit('import')"
            :disabled="state.importing"
            style="min-width: 130px;"
          >{{ state.importing ? '导入中...' : '导入到画廊' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
