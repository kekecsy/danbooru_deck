<script setup>
// Tag 浏览 / 收集ID 在线预览弹窗。
// 状态由父组件持有（state prop 传 ref，自动 unwrap），数据/网络逻辑也都在父组件里。
// 本组件纯呈现 + 事件分发；纯展示/格式化逻辑（缩略图 URL、rating 分桶、原帖链接）放在这里。
import { computed, ref, watch } from 'vue';
import SearchHistoryDropdown from '../SearchHistoryDropdown.vue';

const props = defineProps({
  state: { type: Object, required: true },
  filtered: { type: Array, required: true },
  selectedCount: { type: Number, required: true },
  safeMode: { type: Boolean, default: true },
  // 「全选当前」按钮在父组件按三态循环驱动：
  //   全选（除已下载）→ 全选所有 → 取消全选 → 全选（除已下载）…
  // 这里只接收最终文案，不在子组件里持有 phase，避免双向同步。
  selectAllLabel: { type: String, default: '全选（除已下载）' },
  // 已选中但不在当前页的数量（用于「查看已选」按钮的角标）
  crossPageCount: { type: Number, default: 0 },
  // 多页批量选择弹窗状态：父组件持有（open / from / to / loading / progress / error），
  // 子组件只是把表单控件 v-model 出去 + 把三个动作转发回去。
  multiPage: {
    type: Object,
    default: () => ({ open: false, from: 1, to: 1, loading: false, progress: '', error: '' }),
  },
  // tag 搜索历史（最多 10 条，最近优先）。子组件不直接改 state，仅显示。
  tagSearchHistory: { type: Array, default: () => [] },
  // 常用 tag 列表（最多 20 条）：用户保存的 tag 片段（e.g. official_art），点 chip 就拼到当前 query
  savedTags: { type: Array, default: () => [] },
});
const emit = defineEmits([
  'update:open',
  'load-collected',     // (date, page=1)
  'run-search',         // (page=1)
  'refresh',            // ()
  'go-page',            // (delta)
  'go-to-page',         // (page: number) 直接跳到指定页（Tag/Rank 浏览；collected 模式也支持，跳到该 ID 列表的对应切片页）
  'toggle-select',      // (post)
  'select-all-visible', // ()
  'clear-selection',    // ()
  'open-selection-list',// () 打开跨页已选清单
  'open-multi-page',    // () 打开多页批量弹窗
  'close-multi-page',   // () 关闭多页批量弹窗
  'multi-page-action',  // (mode: 'non_downloaded' | 'all' | 'clear_range')
  'update:multi-from',  // (number)
  'update:multi-to',    // (number)
  'download-selected',  // ()
  'pick-tag-history',   // (query: string) 点了某条历史 → 父组件覆写 browse.query + 触发搜索
  'remove-tag-history', // (query: string) 删除单条
  'clear-tag-history',  // () 清空全部
  'add-saved-tag',      // (tag: string) 把当前 query 保存为常用 tag
  'remove-saved-tag',   // (tag: string) 移除一条常用 tag
]);

const open = computed({
  get: () => props.state.open,
  set: (v) => emit('update:open', v),
});
function close() { open.value = false; }

// tag 搜索历史下拉的显示/隐藏
const showTagHistory = ref(false);
const tagHistoryRef = ref(null);
function onTagHistoryBlur() {
  // 延时关：让下拉里的 mousedown.prevent 抢先触发；点击条目时输入框失焦，blur 在 click 之前
  setTimeout(() => { showTagHistory.value = false; }, 150);
}

// 把一个常用 tag 片段追加到当前 query（直接改 state，不触发搜索 ——
// 用户通常会一次拼多个 tag，最后自己点「搜索」）。
function appendSavedTagToQuery(tag) {
  const t = String(tag || '').trim();
  if (!t) return;
  const cur = (props.state.query || '').replace(/\s+$/, '');
  props.state.query = cur ? `${cur} ${t}` : t;
}
// 「+ 保存当前」：把当前输入框整段保存为常用 tag；只在 query 非空时可点
function addCurrentAsSavedTag() {
  const cur = (props.state.query || '').trim();
  if (!cur) return;
  emit('add-saved-tag', cur);
}

// 缩略图 URL：统一走后端 /api/proxy_thumb（落盘缓存 + 防盗链转发）。
// 走 large_file_url（Danbooru 720px）+ ?size=360，让后端用 Pillow 缩到长边 360px 落盘：
// 比 preview(150) 清晰，比直接用 large(720) 省缓存；展示 cell ~200px 浏览器再轻微 downscale。
// 没有 large 时退回 preview/file_url，透传不缩。
const _THUMB_SIZE = 360;
function thumbUrl(post) {
  let raw = post.large_file_url;
  let useSize = _THUMB_SIZE;
  if (!raw) {
    // 没有 large_file_url 时退回 preview/file_url，不缩放（避免 150→360 upscale 反而更糊）
    raw = post.preview_file_url || post.file_url || '';
    useSize = 0;
  }
  if (!raw) return '';
  const q = `url=${encodeURIComponent(raw)}${useSize ? `&size=${useSize}` : ''}`;
  return `http://127.0.0.1:8000/api/proxy_thumb?${q}`;
}

// rating 首字母：Danbooru 返回 g/s/q/e，g(general) 归入 s 档
function ratingBucket(post) {
  const r = (post.rating || '').toLowerCase();
  if (r === 'e') return 'e';
  if (r === 'q') return 'q';
  return 's';
}

async function openPost(post) {
  if (!post?.id) return;
  const host = props.safeMode ? 'safebooru.donmai.us' : 'danbooru.donmai.us';
  await window.desktopAPI.external.open(`https://${host}/posts/${post.id}`);
}

// 「跳到第 N 页」输入框：默认跟当前页同步；用户输入非法（≤0 / NaN）时不发事件。
// 每次弹窗重新打开或当前页变化时，刷新一次输入值，避免显示陈旧。
const jumpPage = ref(1);
watch(() => props.state.page, (p) => { jumpPage.value = p || 1; }, { immediate: true });
function submitJump() {
  let n = Number(jumpPage.value);
  if (!Number.isFinite(n) || n < 1) {
    jumpPage.value = props.state.page || 1;
    return;
  }
  n = Math.floor(n);
  if (n === props.state.page) {
    // 同一页：把输入框对齐到当前页，避免显示"看起来没生效"
    jumpPage.value = n;
    return;
  }
  emit('go-to-page', n);
}
</script>

<template>
  <div v-if="state.open" class="viewer-overlay browse-overlay" @click.self="close">
    <div class="browse-card">
      <div class="browse-head">
        <div class="browse-head-title">
          <h3>{{ state.source === 'collected' ? '查看收集ID' : (state.source === 'rank' ? 'Rank 浏览' : 'Tag 浏览') }}</h3>
          <span class="muted compact-text">
            {{ state.source === 'collected'
              ? `${state.collectedDate} · 收集 ${state.collectedIds.length} 个 ID`
              : (state.source === 'rank'
                  ? 'Danbooru 全时排行榜 · order:rank'
                  : '缩略图经后端缓存转发') }} · {{ safeMode ? 'SFW' : '全部内容' }}
          </span>
        </div>
        <button class="ghost" @click="close" style="color: var(--muted);">×</button>
      </div>

      <div class="browse-searchbar">
        <template v-if="state.source === 'collected'">
          <span class="muted compact-text" style="align-self: center;">查看日期</span>
          <TaskDatePicker
            :model-value="state.collectedDate"
            @update:model-value="d => emit('load-collected', d, 1)"
            placeholder="默认今天"
          />
          <button class="secondary" :disabled="state.loading" @click="emit('load-collected', state.collectedDate, 1)">
            {{ state.loading ? '加载中…' : '刷新' }}
          </button>
        </template>
        <template v-else-if="state.source === 'rank'">
          <span class="muted compact-text" style="flex: 1; align-self: center;">
            Danbooru 排行榜 · order:rank（按 score 降序，每页 40 条）
          </span>
          <button class="secondary" :disabled="state.loading" @click="emit('refresh')">
            {{ state.loading ? '加载中…' : '刷新当前页' }}
          </button>
        </template>
        <template v-else>
          <div class="browse-query-wrap">
            <input
              v-model="state.query"
              class="search-input"
              type="text"
              placeholder="tag 查询串，例如：hatsune_miku rating:safe -comic"
              @focus="showTagHistory = true"
              @blur="onTagHistoryBlur"
              @keyup.enter="emit('run-search', 1)"
              @keydown="tagHistoryRef?.handleKeydown?.($event)"
            />
            <SearchHistoryDropdown
              ref="tagHistoryRef"
              :items="tagSearchHistory"
              :open="showTagHistory"
              header-label="最近 tag 搜索"
              @pick="entry => { emit('pick-tag-history', entry); showTagHistory = false; }"
              @remove="emit('remove-tag-history', $event)"
              @clear="emit('clear-tag-history')"
              @close="showTagHistory = false"
            />
          </div>
          <button class="secondary" :disabled="state.loading" @click="emit('run-search', 1)">
            {{ state.loading ? '搜索中…' : '搜索' }}
          </button>
        </template>
      </div>

      <div v-if="state.source === 'tags'" class="browse-saved-tags">
        <button
          type="button"
          class="saved-tag-add"
          :disabled="!(state.query || '').trim()"
          :title="(state.query || '').trim() ? '把当前查询串保存为常用 tag' : '先在搜索框里输入要保存的 tag'"
          @click="addCurrentAsSavedTag"
        >+ 保存当前</button>
        <span v-if="savedTags.length" class="saved-tags-sep">常用：</span>
        <span
          v-for="tag in savedTags"
          :key="tag"
          class="saved-tag-chip"
          :title="`点击把 “${tag}” 追加到当前查询`"
          @click="appendSavedTagToQuery(tag)"
        >
          <span class="saved-tag-text">{{ tag }}</span>
          <button
            type="button"
            class="saved-tag-remove"
            title="删除这个常用 tag"
            @click.stop="emit('remove-saved-tag', tag)"
          >×</button>
        </span>
      </div>

      <div class="browse-filters">
        <label class="browse-filter-item">
          最低分
          <input v-model.number="state.minScore" type="number" min="0" class="browse-score-input" />
        </label>
        <label class="browse-filter-item">
          排序
          <select v-model="state.sortBy" class="browse-score-input" style="width: auto;">
            <option value="default">默认顺序</option>
            <option value="score">按 score</option>
          </select>
        </label>
        <button class="ghost" :disabled="state.loading || !filtered.length" @click="emit('refresh')" title="重新拉取当前页，更新 score（保留已勾选）">刷新分数</button>
        <span class="browse-filter-spacer"></span>
        <button class="ghost" @click="emit('select-all-visible')" :title="selectAllLabel + '（连续按下依次切换为：全选所有 → 取消全选）'">{{ selectAllLabel }}</button>
        <button
          class="ghost"
          :class="{ 'browse-multi-page-on': multiPage.open }"
          :disabled="state.loading"
          title="按页范围批量处理：选择/清空多页（一次性，不参与三态）"
          @click="emit(multiPage.open ? 'close-multi-page' : 'open-multi-page')"
        >
          多页全选 {{ multiPage.open ? '▴' : '▾' }}
        </button>
        <button class="ghost" @click="emit('clear-selection')">清空选择</button>
        <button
          class="ghost browse-view-selected"
          :disabled="!selectedCount"
          :title="crossPageCount
            ? `已选 ${selectedCount} 个，其中 ${crossPageCount} 个不在当前页`
            : '查看已选 ID 列表'"
          @click="emit('open-selection-list')"
        >
          查看已选
          <span v-if="selectedCount" class="browse-view-selected-count">({{ selectedCount }})</span>
          <span v-if="crossPageCount" class="browse-view-selected-cross" :title="`${crossPageCount} 个不在当前页`">·{{ crossPageCount }} 跨页</span>
        </button>
      </div>

      <!-- 多页批量弹窗（直接内联在筛选栏下面，popover 形态）。
           范围输入 + 三个一次性动作；执行中禁用控件、显示进度。 -->
      <div v-if="multiPage.open" class="browse-multipage" @click.stop>
        <div class="browse-multipage-head">
          <span class="browse-multipage-title">多页批量（一次性）</span>
          <span class="muted compact-text">
            当前第 {{ state.page }} 页 · 范围上限 {{ state.page }}
          </span>
          <button class="ghost" :disabled="multiPage.loading" @click="emit('close-multi-page')" title="关闭">×</button>
        </div>
        <div class="browse-multipage-body">
          <label class="browse-multipage-field">
            从
            <input
              type="number"
              min="1"
              :max="state.page"
              :value="multiPage.from"
              :disabled="multiPage.loading"
              class="browse-multipage-input"
              @input="e => emit('update:multi-from', Math.max(1, Math.min(state.page, Number(e.target.value) || 1)))"
            />
            页
          </label>
          <label class="browse-multipage-field">
            到
            <input
              type="number"
              min="1"
              :max="state.page"
              :value="multiPage.to"
              :disabled="multiPage.loading"
              class="browse-multipage-input"
              @input="e => emit('update:multi-to', Math.max(1, Math.min(state.page, Number(e.target.value) || 1)))"
            />
            页
          </label>
          <div class="browse-multipage-actions">
            <button
              class="secondary"
              :disabled="multiPage.loading"
              :title="`把第 ${multiPage.from}-${multiPage.to} 页内 post.downloaded === false 的全部勾上`"
              @click="emit('multi-page-action', 'non_downloaded')"
            >
              全选(除已下载)
            </button>
            <button
              class="secondary"
              :disabled="multiPage.loading"
              :title="`把第 ${multiPage.from}-${multiPage.to} 页内所有图片（含已下载）一起勾上`"
              @click="emit('multi-page-action', 'all')"
            >
              全选所有
            </button>
            <button
              class="ghost"
              :disabled="multiPage.loading"
              :title="`把当前已选中、且出现在第 ${multiPage.from}-${multiPage.to} 页的 ID 从已选里移除`"
              @click="emit('multi-page-action', 'clear_range')"
            >
              清空范围内
            </button>
          </div>
        </div>
        <div v-if="multiPage.progress || multiPage.error" class="browse-multipage-status">
          <span v-if="multiPage.progress" class="muted compact-text">{{ multiPage.progress }}…</span>
          <span v-if="multiPage.error" class="browse-multipage-error">{{ multiPage.error }}</span>
        </div>
      </div>

      <div class="browse-grid-wrap">
        <div v-if="state.loading" class="gallery-empty" style="min-height: 200px;">正在获取…</div>
        <div v-else-if="state.error" class="gallery-empty" style="min-height: 200px;">{{ state.error }}</div>
        <div v-else-if="!filtered.length" class="gallery-empty" style="min-height: 200px;">
          没有符合筛选条件的结果
        </div>
        <div v-else class="browse-grid">
          <div
            v-for="post in filtered"
            :key="post.id"
            class="browse-cell"
            :class="{ selected: state.selected.has(post.id), downloaded: post.downloaded }"
            @click="emit('toggle-select', post)"
          >
            <img
              :src="thumbUrl(post)"
              class="browse-thumb"
              loading="lazy"
              referrerpolicy="no-referrer"
              :alt="post.id"
            />
            <div class="browse-cell-check" :class="{ on: state.selected.has(post.id) }">✓</div>
            <button class="browse-open-post" @click.stop="openPost(post)" title="在浏览器打开 Danbooru 原帖">↗</button>
            <!-- 已下载价签：左侧竖向绿色吊牌，告诉用户这张已经在库里了，不需要再勾选下载 -->
            <div v-if="post.downloaded" class="browse-downloaded-ribbon" title="已在库（log.json）">已下载</div>
            <div class="browse-cell-meta">
              <span class="browse-badge" :class="`rating-${ratingBucket(post)}`">{{ (post.rating || '?').toUpperCase() }}</span>
              <span class="browse-badge">▲{{ post.score }}</span>
              <span class="browse-badge" v-if="post.image_width">{{ post.image_width }}×{{ post.image_height }}</span>
              <span class="browse-badge browse-ext">{{ (post.file_ext || '').toUpperCase() }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="browse-foot">
        <div class="browse-foot-left">
          <button class="ghost" :disabled="state.page <= 1 || state.loading" @click="emit('go-page', -1)">‹ 上一页</button>
          <span class="browse-page-label">第 {{ state.page }} 页</span>
          <button class="ghost" :disabled="!state.hasMore || state.loading" @click="emit('go-page', 1)">下一页 ›</button>
          <span class="browse-jump-wrap" :class="{ disabled: state.loading }" :title="state.hasMore ? '输入页码后按 Enter 或点“跳”' : '已达末页（hasMore=false），跳到更高页可能为空'">
            <span class="browse-jump-prefix">跳到</span>
            <input
              v-model.number="jumpPage"
              type="number"
              min="1"
              step="1"
              class="browse-jump-input"
              :disabled="state.loading"
              @keyup.enter="submitJump"
              @blur="submitJump"
            />
            <span class="browse-jump-suffix">页</span>
            <button
              class="ghost browse-jump-btn"
              :disabled="state.loading"
              @click="submitJump"
            >跳</button>
          </span>
        </div>
        <div class="browse-foot-right">
          <span class="muted compact-text">下载到</span>
          <TaskDatePicker v-model="state.targetDate" placeholder="默认今天" />
          <button
            class="browse-download-btn"
            :disabled="!selectedCount || state.downloading"
            @click="emit('download-selected')"
          >
            {{ state.downloading ? '提交中…' : `下载选中 (${selectedCount})` }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.browse-overlay {
  align-items: stretch;
}
.browse-card {
  width: min(1200px, 96vw);
  height: min(860px, 92vh);
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin: auto;
}
.browse-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--line);
}
.browse-head-title { display: flex; align-items: baseline; gap: 12px; }
.browse-head-title h3 { margin: 0; color: var(--accent-deep); font-size: 18px; }
.browse-searchbar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 18px;
  border-bottom: 1px solid var(--line);
}
.browse-searchbar .search-input { flex: 1; }
.browse-query-wrap { position: relative; flex: 1; display: flex; }

/* 常用 tag chip 行：仅在 tag 搜索模式展示。点 chip 把 tag 拼到当前 query，点 + 保存当前把 query 存为常用。 */
.browse-saved-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 6px 18px;
  border-bottom: 1px solid var(--line);
  background: rgba(0, 0, 0, 0.015);
  font-size: 12px;
  min-height: 30px;
}
.saved-tag-add {
  background: transparent;
  border: 1px dashed var(--line, rgba(0, 0, 0, 0.25));
  border-radius: 14px;
  padding: 3px 10px;
  font-size: 12px;
  color: var(--muted, #6b5a3c);
  cursor: pointer;
  box-shadow: none;
  transform: none;
  transition: border-color 0.14s ease, color 0.14s ease, background-color 0.14s ease;
}
.saved-tag-add:hover:not(:disabled) {
  border-color: var(--accent-deep, #7c5cff);
  color: var(--accent-deep, #7c5cff);
  background: rgba(196, 130, 60, 0.06);
  filter: none;
  box-shadow: none;
  transform: none;
}
.saved-tag-add:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.saved-tags-sep {
  color: var(--muted, #6b5a3c);
  font-size: 12px;
  margin-left: 2px;
}
.saved-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  background: var(--surface, rgba(255, 255, 255, 0.55));
  border: 1px solid var(--line, rgba(0, 0, 0, 0.15));
  border-radius: 14px;
  padding: 2px 2px 2px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: var(--ink, #2a1f10);
  cursor: pointer;
  user-select: none;
  transition: border-color 0.14s ease, color 0.14s ease, background-color 0.14s ease;
}
.saved-tag-chip:hover {
  border-color: var(--accent-deep, #7c5cff);
  color: var(--accent-deep, #7c5cff);
  background: rgba(196, 130, 60, 0.10);
}
.saved-tag-text {
  /* 留出右边给 × 按钮：不要让 tag 文本挤到 chip 边缘 */
  padding-right: 4px;
}
.saved-tag-remove {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--muted, #6b5a3c);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease, background-color 0.12s ease, color 0.12s ease;
  box-shadow: none;
  transform: none;
}
.saved-tag-chip:hover .saved-tag-remove {
  opacity: 1;
}
.saved-tag-remove:hover {
  background: rgba(157, 44, 44, 0.18);
  color: #9d2c2c;
  filter: none;
  box-shadow: none;
  transform: none;
}
.browse-filters {
  display: flex;
  gap: 14px;
  align-items: center;
  padding: 8px 18px;
  border-bottom: 1px solid var(--line);
  background: rgba(0, 0, 0, 0.02);
  flex-wrap: wrap;
}
.browse-filter-item { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.browse-score-input { width: 64px; }
.browse-filter-ratings { display: flex; gap: 12px; font-size: 13px; }
.browse-filter-ratings label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.browse-filter-spacer { flex: 1; }
.browse-grid-wrap { flex: 1; overflow-y: auto; padding: 14px 18px; }
.browse-grid {
  display: grid;
  /* 缩略图升级到 large_file_url（~720px）后，单元稍微放宽一档，
     否则高分辨率在 160px 格子里显示不出来；同时上限也按相应代价调到 300。 */
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
}
.browse-cell {
  position: relative;
  aspect-ratio: 1 / 1;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  border: 2px solid transparent;
  /* 跟画廊 .image-card 一致：不放大不位移，仅用亮度 + 阴影 + 边框描边过渡 */
  transition: filter 0.14s ease, border-color 0.14s ease, box-shadow 0.14s ease;
  background: rgba(0, 0, 0, 0.04);
}
/* 悬停效果对齐画廊 image-card / 按钮：不做上浮位移，只用亮度 + 阴影 + 边框的轻微变化 */
.browse-cell:hover {
  transform: none;
  filter: brightness(0.97);
  border-color: rgba(var(--accent-rgb), 0.28);
  box-shadow: 0 6px 16px rgba(30, 41, 82, 0.10);
}
.browse-cell:active { filter: brightness(0.94); }
.browse-cell.selected { border-color: var(--accent-deep, #7c5cff); }
/* 已在库（log.json 命中）的外框样式：绿色实线 + 略带绿色阴影，跟 selected（紫）区分开 */
.browse-cell.downloaded { border-color: #10b981; box-shadow: inset 0 0 0 1px rgba(16, 185, 129, 0.4); }
.browse-cell.downloaded.selected { border-color: #10b981; box-shadow: inset 0 0 0 1px rgba(16, 185, 129, 0.6), 0 0 0 1px var(--accent-deep, #7c5cff); }
.browse-thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
.browse-cell-check {
  position: absolute;
  top: 6px;
  left: 6px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 800;
  opacity: 0;
  transition: opacity 0.12s ease, background 0.12s ease;
}
.browse-cell.selected .browse-cell-check,
.browse-cell:hover .browse-cell-check { opacity: 1; }
.browse-cell-check.on { background: var(--accent-deep, #7c5cff); color: #fff; }
.browse-open-post {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 13px;
  line-height: 1;
  /* 跟 .browse-cell-check 一样用 flex 居中，避免 ↗ 落在按钮左上角的默认文字基线位置，
     看起来偏移圆圈。padding: 0 是为了不挤压 flex 居中空间。 */
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease, background 0.12s ease;
}
.browse-cell:hover .browse-open-post { opacity: 1; }
.browse-open-post:hover { background: var(--accent-deep, #7c5cff); }
.browse-cell-meta {
  position: absolute;
  left: 6px;
  right: 6px;
  bottom: 6px;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.browse-badge {
  font-size: 10.5px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
}
.browse-badge.rating-e { background: rgba(220, 50, 50, 0.85); }
.browse-badge.rating-q { background: rgba(220, 150, 40, 0.85); }
.browse-badge.rating-s { background: rgba(50, 160, 90, 0.85); }
.browse-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 18px;
  border-top: 1px solid var(--line);
  gap: 10px;
  flex-wrap: wrap;
}
.browse-foot-left, .browse-foot-right { display: flex; align-items: center; gap: 10px; }
.browse-page-label { font-size: 13px; color: var(--muted); min-width: 60px; text-align: center; }
.browse-jump-wrap { display: inline-flex; align-items: center; gap: 4px; margin-left: 8px; padding: 2px 6px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-muted); font-size: 12px; color: var(--muted); transition: opacity 0.14s ease, border-color 0.14s ease; }
.browse-jump-wrap.disabled { opacity: 0.55; }
.browse-jump-wrap:focus-within { border-color: rgba(var(--accent-rgb), 0.55); }
.browse-jump-prefix, .browse-jump-suffix { white-space: nowrap; }
.browse-jump-input { width: 56px; height: 26px; padding: 0 4px; text-align: center; font-size: 12.5px; font-weight: 600; color: var(--ink); background: rgba(255, 255, 255, 0.7); border: 1px solid var(--line); border-radius: 6px; font-family: inherit; }
.browse-jump-input::-webkit-outer-spin-button, .browse-jump-input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.browse-jump-input[type="number"] { -moz-appearance: textfield; }
.browse-jump-input:disabled { cursor: not-allowed; }
.browse-jump-btn { height: 26px; padding: 0 10px; font-size: 12px; border-radius: 6px; }
.browse-download-btn {
  background: linear-gradient(135deg, var(--accent), var(--accent-deep));
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 6px 16px;
  font-weight: 600;
  cursor: pointer;
  font-size: 13px;
}
.browse-download-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* 「查看已选」按钮：默认 ghost 风格，括号里放总数；
   有跨页条目时附一个橙色小角标提示「主网格看不到的部分」 */
.browse-view-selected {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.browse-view-selected-count {
  color: var(--accent-deep);
  font-weight: 600;
}
.browse-view-selected-cross {
  color: #d97706;
  font-weight: 600;
  font-size: 11px;
  background: rgba(245, 158, 11, 0.12);
  padding: 1px 6px;
  border-radius: 999px;
  margin-left: 2px;
}

/* 「多页全选」按钮：开 / 关两种状态用箭头提示，再加一个开关高亮 */
.browse-multi-page-on {
  background: rgba(var(--accent-rgb), 0.16);
  border-color: rgba(var(--accent-rgb), 0.45);
  color: var(--accent-deep);
}

/* 多页批量 popover：内联在筛选栏下面，浅色卡片 + 边框 + 内边距。
   用 flex 让范围输入和三个动作按钮在窄屏自动换行。 */
.browse-multipage {
  margin: 0 18px;
  border: 1px solid rgba(var(--accent-rgb), 0.25);
  background: rgba(var(--accent-rgb), 0.05);
  border-radius: 10px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.browse-multipage-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.browse-multipage-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-deep);
}
.browse-multipage-head .ghost {
  margin-left: auto;
  padding: 0 8px;
}
.browse-multipage-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 14px;
}
.browse-multipage-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.browse-multipage-input {
  width: 64px;
  padding: 4px 6px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  font-size: 13px;
}
.browse-multipage-input:disabled { opacity: 0.5; }
.browse-multipage-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-wrap: wrap;
}
.browse-multipage-status {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  min-height: 18px;
}
.browse-multipage-error {
  color: #b91c1c;
  font-weight: 600;
}

/* 「已下载」价签：左缘竖向绿色长条 + 小圆点孔洞，模仿吊牌。
   让出 cell 圆角 + 上方 check / 打开原帖 按钮的空间，竖向文字不挡缩略图主体。 */
.browse-downloaded-ribbon {
  position: absolute;
  left: 4px;
  top: 34px;  /* 让出顶部 28px 给 check (top:6) + 一点呼吸距离 */
  z-index: 2;
  background: linear-gradient(180deg, #10b981, #059669);
  color: #fff;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1px;
  padding: 5px 5px 5px 8px;
  border-radius: 0 4px 4px 0;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.25);
  writing-mode: vertical-rl;
  text-orientation: upright;
  line-height: 1;
  pointer-events: none;
  user-select: none;
}
.browse-downloaded-ribbon::before {
  /* 价签上的「孔洞」小圆点，纯装饰 */
  content: '';
  position: absolute;
  left: 3px;
  top: 50%;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.7);
  transform: translateY(-50%);
}
</style>
