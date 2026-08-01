<script setup>
// 加入画师收藏分组弹窗。
// 状态、加载、保存逻辑都留在父组件 CrawlerPage；本组件只负责呈现和事件分发。
import { computed } from 'vue';

const props = defineProps({
  state: { type: Object, required: true },          // { open, artist, loading, saving, groups, selectedGroups, newGroupName }
  groupList: { type: Array, required: true },       // [{ name, count }] —— 父组件排序好的分组列表
});
const emit = defineEmits(['update:open', 'toggle-group', 'create-group', 'save']);

const open = computed({
  get: () => props.state.open,
  set: (v) => emit('update:open', v),
});

function close() { open.value = false; }
</script>

<template>
  <div
    v-if="state.open"
    class="viewer-overlay"
    @click.self="close"
    style="z-index: 10020; display: flex; justify-content: center; align-items: center; padding: 24px;"
  >
    <div class="fav-add-modal">
      <div class="fav-add-head">
        <div>
          <h3 style="margin: 0; color: var(--accent-deep); font-size: 17px;">加入画师收藏</h3>
          <p class="muted compact-text" style="margin: 4px 0 0;">画师：<strong style="color: var(--ink); font-family: Consolas, monospace;">{{ state.artist }}</strong></p>
        </div>
        <button class="ghost" @click="close" style="color: var(--muted);">×</button>
      </div>

      <div v-if="state.loading" class="muted compact-text" style="text-align: center; padding: 20px;">加载分组中...</div>
      <template v-else>
        <div class="fav-add-list">
          <label v-for="g in groupList" :key="g.name" class="fav-add-row">
            <input
              type="checkbox"
              :checked="state.selectedGroups.includes(g.name)"
              @change="emit('toggle-group', g.name)"
            />
            <span class="fav-add-name">{{ g.name }}</span>
            <span class="fav-add-count">{{ g.count }}</span>
          </label>
          <div v-if="!groupList.length" class="muted compact-text" style="text-align: center; padding: 12px;">
            还没有分组，请在下方创建一个
          </div>
        </div>

        <div class="fav-add-new">
          <input
            v-model="state.newGroupName"
            type="text"
            placeholder="新分组名称，例如：厚涂大佬"
            @keyup.enter="emit('create-group')"
          />
          <button class="secondary" @click="emit('create-group')" style="white-space: nowrap;">+ 新建</button>
        </div>
      </template>

      <div class="fav-add-foot">
        <span class="muted compact-text">已勾选 {{ state.selectedGroups.length }} 个分组</span>
        <div style="display: flex; gap: 8px;">
          <button class="ghost" @click="close" style="color: var(--accent-deep);">取消</button>
          <button @click="emit('save')" :disabled="state.saving" style="min-width: 90px;">
            {{ state.saving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
