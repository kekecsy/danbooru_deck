<script setup>
// 加入角色收藏分组弹窗。
// 与 ArtistFavoriteModal 同构：状态 + 业务逻辑都在父组件，本组件纯呈现。
// 差异：额外显示 source_hint，空提示和新分组 placeholder 都按角色场景定制。
import { computed } from 'vue';

const props = defineProps({
  state: { type: Object, required: true },          // { open, character, sourceHint, loading, saving, groups, selectedGroups, newGroupName }
  groupList: { type: Array, required: true },
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
          <h3 style="margin: 0; color: var(--accent-deep); font-size: 17px;">加入角色收藏</h3>
          <p class="muted compact-text" style="margin: 4px 0 0;">
            角色：<strong style="color: var(--ink); font-family: Consolas, monospace;">{{ state.character }}</strong>
          </p>
          <p v-if="state.sourceHint" class="muted compact-text" style="margin: 2px 0 0;">
            出处 (source_hint)：<strong style="color: var(--accent-deep);">{{ state.sourceHint }}</strong>
            <span class="muted compact-text">· 同出处的角色会自动合并到同名分组</span>
          </p>
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
            还没有分组，下方已为你预填 source_hint 作为新分组名
          </div>
        </div>

        <div class="fav-add-new">
          <input
            v-model="state.newGroupName"
            type="text"
            placeholder="新分组名（建议 = source_hint，例如 vocaloid / touhou）"
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
