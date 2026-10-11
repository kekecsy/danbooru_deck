// 日历「有信息缺失的图片」角标的数据源。
//
// 什么算「没有信息的图片」= 代码里既有的「孤立文件（orphan）」：盘上有媒体文件，
// 但 deck.db.viewer_entries 里没有它的元数据（手动丢进日期目录、下载中断留下的文件…
// 画廊里那张卡片会显示「未知」）。需求：这类日期要在日历里打感叹号。
//
// ⚠ 只按「当前显示的月份」按需拉取（≤31 天），不做全量扫盘：三个库根全是 lazy_scan，
// 逐日期扫机械盘正是图库加载时刻意避开的（见 main.build_local_image_library）。
// 后端 /api/date_orphans 自带 60s 缓存，这里再叠一层前端 in-flight 去重。
//
// 为什么要去重：同一个后端响应会被 4 个日历实例（右侧画廊日历 + 左侧任务日历的
// 单日/起/止 3 个日期框）同时问到，不去重就是同一月份并发触发多次扫盘。

const API_BASE = 'http://127.0.0.1:18765';

// 'YYYY-MM' -> Promise<{ 'YYYY-MM-DD': 孤立文件数 }>
// 只保存「正在飞」的请求，落地后短暂保留一会儿让同一批并发调用共享结果，
// 之后清掉：重新打开面板会重新问后端（后端 60s 内直接回缓存，代价很低）。
const inflight = new Map();
const KEEP_MS = 5000;

/**
 * 拉取某个月份的「按日孤立文件数」。
 * @param {string} month 'YYYY-MM'
 * @returns {Promise<{[date: string]: number}>} 只含 > 0 的日期；参数非法或请求失败返回 {}
 */
export function fetchDateOrphans(month) {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month || '')) return Promise.resolve({});
  const cached = inflight.get(month);
  if (cached) return cached;

  const task = fetch(`${API_BASE}/api/date_orphans?month=${month}`)
    .then(res => res.json())
    .then(json => (json && json.ok && json.counts) ? json.counts : {})
    .catch(() => ({}))   // 后端没起来 / 网络抖动：静默降级为「无标记」，不打断日历
    .finally(() => { setTimeout(() => inflight.delete(month), KEEP_MS); });

  inflight.set(month, task);
  return task;
}
