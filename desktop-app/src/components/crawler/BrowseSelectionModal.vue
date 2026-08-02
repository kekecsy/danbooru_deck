<script setup>
// Tag 浏览的「跨页已选清单」弹窗。
//
// 跟普通画廊的 SelectionListModal 不同：browse 一次只把当前页的 post 拉到内存里，
// 切页之后旧页的 post 就没了；为了跨页展示缩略图，父组件会同时把 post 对象存到
// browse.selectedItems (Map<id, post>)，这里直接拿这个 Map 渲染就行。
//
// 父组件（CrawlerPage）通过 selectedEntries 传 [{ id, post, onPage }]：
//   - onPage = true  表示这条勾选正好落在当前页（用户能在主网格里看到）
//   - onPage = false 表示这条勾选来自其他页/其他搜索（主网格看不到，全靠这里管理）
import { computed } from 'vue';

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  // 父组件传过来的已选列表（建议按 id 升序，方便看）
  selectedEntries: { type: Array, required: true },
  // 总数 / 当前页 / 跨页 三个数字
  totalCount: { type: Number, required: true },
  onPageCount: { type: Number, required: true },
  crossPageCount: { type: Number, required: true },
});
const emit = defineEmits(['update:modelValue', 'remove']);

const open = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
});
function close() { open.value = false; }

// 缩略图：和 BrowseOverlay 用同样的后端 /api/proxy_thumb 缓存策略。
// 没有 large_file_url 时退回 preview/file_url，不缩放（避免 upscale 反而更糊）。
const _THUMB_SIZE = 360;
function thumbUrl(post) {
  if (!post) return '';
  let raw = post.large_file_url;
  let useSize = _THUMB_SIZE;
  if (!raw) {
    raw = post.preview_file_url || post.file_url || '';
    useSize = 0;
  }
  if (!raw) return '';
  const q = `url=${encodeURIComponent(raw)}${useSize ? `&size=${useSize}` : ''}`;
  return `http://127.0.0.1:8000/api/proxy_thumb?${q}`;
}
function ratingBucket(post) {
  const r = (post?.rating || '').toLowerCase();
  if (r === 'e') return 'e';
  if (r === 'q') return 'q';
  return 's';
}
</script>

<template>
  <div
    v-if="open"
    class="viewer-overlay"
    @click.self="close"
    style="z-index: 10000; display: flex; justify-content: center; align-items: center; padding: 24px;"
  >
    <div class="bsl-card">
      <div class="bsl-head">
        <h3 style="margin: 0; color: var(--accent-deep); font-size: 18px;">
          已选清单 · {{ totalCount }} 个
          <span class="muted compact-text" style="font-weight: 400; margin-left: 8px;">
            本页 {{ onPageCount }} · 跨页 {{ crossPageCount }}
          </span>
        </h3>
        <button class="ghost" @click="close" style="color: var(--muted);">×</button>
      </div>
      <div v-if="!totalCount" class="gallery-empty" style="min-height: 120px;">还没有选择任何图片</div>
      <div v-else class="bsl-body">
        <!-- 跨页部分先展示：用户打开弹窗通常就是为了处理「看不到的那些」 -->
        <div v-if="crossPageCount" class="bsl-section">
          <div class="bsl-section-title">
            跨页 / 跨搜索 · {{ crossPageCount }} 个
            <span class="muted compact-text" style="font-weight: 400;">
              （不在当前页主网格里显示）
            </span>
          </div>
          <div class="bsl-grid">
            <div
              v-for="entry in selectedEntries.filter(e => !e.onPage)"
              :key="`cross-${entry.id}`"
              class="bsl-item cross"
            >
              <img
                class="bsl-thumb"
                :src="thumbUrl(entry.post)"
                :alt="entry.id"
                loading="lazy"
                referrerpolicy="no-referrer"
              />
              <div class="bsl-item-info">
                <span class="bsl-id">#{{ entry.id }}</span>
                <div class="bsl-meta">
                  <span class="bsl-badge" :class="`rating-${ratingBucket(entry.post)}`">
                    {{ (entry.post.rating || '?').toUpperCase() }}
                  </span>
                  <span class="bsl-badge">▲{{ entry.post.score }}</span>
                  <span v-if="entry.post.downloaded" class="bsl-badge downloaded" title="已在库">已下载</span>
                </div>
              </div>
              <button class="ghost" @click="emit('remove', entry.id)" title="从选择中移除">移除</button>
            </div>
          </div>
        </div>
        <div v-if="onPageCount" class="bsl-section">
          <div class="bsl-section-title">
            当前页 · {{ onPageCount }} 个
            <span class="muted compact-text" style="font-weight: 400;">
              （主网格里也能看到）
            </span>
          </div>
          <div class="bsl-grid">
            <div
              v-for="entry in selectedEntries.filter(e => e.onPage)"
              :key="`cur-${entry.id}`"
              class="bsl-item on-page"
            >
              <img
                class="bsl-thumb"
                :src="thumbUrl(entry.post)"
                :alt="entry.id"
                loading="lazy"
                referrerpolicy="no-referrer"
              />
              <div class="bsl-item-info">
                <span class="bsl-id">#{{ entry.id }}</span>
                <div class="bsl-meta">
                  <span class="bsl-badge" :class="`rating-${ratingBucket(entry.post)}`">
                    {{ (entry.post.rating || '?').toUpperCase() }}
                  </span>
                  <span class="bsl-badge">▲{{ entry.post.score }}</span>
                  <span v-if="entry.post.downloaded" class="bsl-badge downloaded" title="已在库">已下载</span>
                </div>
              </div>
              <button class="ghost" @click="emit('remove', entry.id)" title="从选择中移除">移除</button>
            </div>
          </div>
        </div>
      </div>
      <div class="bsl-foot">
        <span class="muted compact-text">移除后若想继续跨页操作，重新打开浏览弹窗即可</span>
        <button class="ghost" @click="close">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bsl-card {
  width: 760px;
  max-width: 96vw;
  max-height: 86vh;
  background: rgba(255, 255, 255, 0.97);
  border-radius: 14px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.bsl-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.bsl-body {
  flex: 1 1 auto;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-right: 4px;
}
.bsl-section { display: flex; flex-direction: column; gap: 8px; }
.bsl-section-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-deep);
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.bsl-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}
.bsl-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border: 1px solid rgba(79, 118, 224, 0.14);
  border-radius: 10px;
  background: rgba(99, 102, 241, 0.06);
}
/* 跨页的条目用更醒目的边框，提醒用户「这条在主网格看不到，小心误删」 */
.bsl-item.cross { border-color: rgba(245, 158, 11, 0.5); background: rgba(245, 158, 11, 0.06); }
.bsl-item.on-page { border-color: rgba(99, 102, 241, 0.18); }
.bsl-thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
  background: #eee;
  flex: 0 0 auto;
}
.bsl-item-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1 1 auto;
  min-width: 0;
}
.bsl-id {
  font-family: Consolas, monospace;
  font-size: 13px;
  color: var(--ink);
  font-weight: 600;
}
.bsl-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.bsl-badge {
  font-size: 10.5px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
}
.bsl-badge.rating-e { background: rgba(220, 50, 50, 0.85); }
.bsl-badge.rating-q { background: rgba(220, 150, 40, 0.85); }
.bsl-badge.rating-s { background: rgba(50, 160, 90, 0.85); }
.bsl-badge.downloaded { background: rgba(16, 185, 129, 0.85); }
.bsl-item button {
  flex: 0 0 auto;
  font-size: 11px;
  padding: 4px 8px;
}
.bsl-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}
</style>
