// Danbooru ID 编解码工具。
// 用于：
//   - 加密（压缩）：把一长串 ID 列表压成 "dbids:..." 短字符串，方便分享
//   - 解密（解压）：从 "dbids:..." 还原成 ID 列表
//   - parsePastedIds：智能解析用户粘贴的任意文本（自动识别压缩格式或明文数字）

/**
 * 压缩 ID 列表为 dbids:xxx 格式。
 * @param {Iterable<string|number>} idStrings
 * @returns {string} 压缩后的字符串；空列表返回 ''
 */
export function compressIds(idStrings) {
  const nums = Array.from(idStrings).map(s => Number(s)).filter(n => Number.isFinite(n) && n > 0);
  if (!nums.length) return '';
  nums.sort((a, b) => a - b);
  const dedup = [];
  let last = -1;
  for (const n of nums) {
    if (n !== last) { dedup.push(n); last = n; }
  }
  const parts = [dedup[0].toString(36)];
  for (let i = 1; i < dedup.length; i++) {
    parts.push((dedup[i] - dedup[i - 1]).toString(36));
  }
  return 'dbids:' + parts.join('.');
}

/**
 * 解压 dbids:xxx 格式回 ID 列表。
 * @param {string} text
 * @returns {string[]|null} ID 字符串数组；不是压缩格式时返回 null
 */
export function decompressIds(text) {
  const m = String(text || '').match(/dbids:([0-9a-z.]+)/i);
  if (!m) return null;
  const parts = m[1].split('.').filter(Boolean);
  if (!parts.length) return null;
  const result = [];
  let cur = 0;
  for (let i = 0; i < parts.length; i++) {
    const v = parseInt(parts[i], 36);
    if (!Number.isFinite(v) || v < 0) return null;
    cur = i === 0 ? v : cur + v;
    if (cur <= 0) return null;
    result.push(String(cur));
  }
  return result;
}

/**
 * 智能解析用户粘贴的任意文本，自动识别压缩格式或明文数字。
 * @param {string} text
 * @returns {string[]} 解析出的 ID 字符串数组
 */
export function parsePastedIds(text) {
  if (!text) return [];
  // 优先识别压缩格式
  const decompressed = decompressIds(text);
  if (decompressed && decompressed.length) return decompressed;
  // 回退：从任意文本中抠出 3 位以上数字（兼容旧的逗号/空格/换行/URL 混合）
  const matches = String(text).match(/\d{3,}/g) || [];
  return Array.from(new Set(matches));
}
