// Tag 浏览 / 排行榜浏览 / 已选清单共用的缩略图 URL 构造。
// 统一走后端 /api/proxy_thumb（落盘缓存 + 防盗链转发）；这三处的网格规则完全一致，
// 所以收敛到一个函数，避免"改了一处忘了另一处"。

const API_BASE = 'http://127.0.0.1:18765';
const THUMB_SIZE = 360;

// ⚠ large_file_url 不保证是图片。Danbooru 对没有 large 变体的帖子会把它退回 file_url
// 本体（视频 → .mp4，ugoira → .zip）。把这种 URL 喂给 <img> 渲染不出来，后端还会把
// 整个本体当"缩略图"拉下来，所以用它之前必须先确认扩展名确实是图片。
const IMAGE_URL_RE = /\.(jpe?g|png|webp|gif|bmp|avif)(?:[?#]|$)/i;

/**
 * 算出一个 post 在浏览网格里该用的缩略图 URL。
 * @param {object} post 后端 /api/browse_tags|browse_rank 返回的 slim post
 * @returns {string} 可直接塞进 <img src> 的 URL；拿不到任何可用图源时返回 ''
 */
export function browseThumbUrl(post) {
  if (!post) return '';

  // 1) 视频：用后端从 media_asset.variants 挑好的静态封面帧（cover_file_url，360x360 jpg）。
  //    已是目标尺寸，不再让后端缩放。
  if (post.cover_file_url) {
    return `${API_BASE}/api/proxy_thumb?url=${encodeURIComponent(post.cover_file_url)}`;
  }

  // 2) 图片：走 large_file_url（~720px）+ ?size=360，让后端用 Pillow 缩到长边 360px 落盘：
  //    比 preview(150) 清晰，比直接用 large(720) 省缓存；cell ~200px 浏览器再轻微 downscale。
  const large = post.large_file_url;
  if (large && IMAGE_URL_RE.test(large)) {
    return `${API_BASE}/api/proxy_thumb?url=${encodeURIComponent(large)}&size=${THUMB_SIZE}`;
  }

  // 3) 其余（没有 large / large 不是图片）：退回 preview/file_url，透传不缩
  //    （避免 150→360 upscale 反而更糊）。都没有则返回空串，由卡片显示占位。
  const raw = post.preview_file_url || post.file_url || '';
  if (!raw) return '';
  return `${API_BASE}/api/proxy_thumb?url=${encodeURIComponent(raw)}`;
}
