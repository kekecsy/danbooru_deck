"""修复脚本：扫描 viewer_data.json 中 local_path 缺失的图片并重新下载。

用法：
    python redownload_missing.py                       # 扫描 hot_pic/ 下所有日期
    python redownload_missing.py --date 2026-05-12     # 只修复指定日期
    python redownload_missing.py --dry-run             # 只列出缺失文件，不下载
    python redownload_missing.py --sleep 2.0           # 调整下载间隔（秒）
    python redownload_missing.py --no-api              # 仅用 log.json 缓存的 URL，不回退 API

机制：
  - 对每条 viewer_data.json 条目检查 local_path 是否在磁盘上
  - 缺失则查 log.json[post_id] 拿原 CDN URL（命中率高，无需打 API）
  - 未命中且 --no-api 未开启时，调 danbooru_api.fetch_data_with_retry(post_id) 拿新 URL
  - 下载后若 log.json 没有该 post_id，自动补登记
"""
import argparse
import os
import time
from danbooru_data import DanbooruData
import danbooru_api

BASE_DIR = './hot_pic'


def extract_post_id(post_url: str) -> str | None:
    if not post_url:
        return None
    tail = post_url.rstrip('/').rsplit('/', 1)[-1]
    return tail if tail.isdigit() else None


def repair_one_day(date_str: str, sleep_s: float, dry_run: bool, use_api: bool) -> tuple[int, int, int]:
    dd = DanbooruData(target_date=date_str)
    data = dd.load_viewer_data()
    if not data:
        return 0, 0, 0

    missing = 0
    repaired = 0
    failed = 0
    log_dirty = False

    for item in data:
        filename = item.get('filename', '')
        local_path = item.get('local_path', '') or (
            os.path.join(dd.save_dir, filename) if filename else ''
        )
        if not local_path or os.path.exists(local_path):
            continue

        missing += 1
        post_id = extract_post_id(item.get('post_url', ''))
        if not post_id:
            print(f"  [{date_str}] {filename or '?'}: 无 post_url，跳过")
            failed += 1
            continue

        image_url = dd.log_data.get(post_id)
        source = 'log.json'

        if not image_url and use_api:
            post = danbooru_api.fetch_data_with_retry(int(post_id))
            if post:
                image_url = post.get('file_url') or post.get('large_file_url')
                source = 'API'

        if not image_url:
            print(f"  [{date_str}] post {post_id}: 无法获取下载URL")
            failed += 1
            continue

        if dry_run:
            print(f"  [{date_str}] 缺失: {filename}  ←  {source}")
            continue

        print(f"  [{date_str}] 重新下载 post {post_id} ({source})...")
        saved = danbooru_api.download_image(image_url, dd.save_dir)
        if saved:
            repaired += 1
            if post_id not in dd.log_data:
                dd.log_data[post_id] = image_url
                log_dirty = True
        else:
            print(f"    下载失败: {image_url}")
            failed += 1

        time.sleep(sleep_s)

    if log_dirty and not dry_run:
        dd.save_global_data()

    return missing, repaired, failed


def list_dates() -> list[str]:
    if not os.path.isdir(BASE_DIR):
        return []
    out = []
    for name in sorted(os.listdir(BASE_DIR)):
        full = os.path.join(BASE_DIR, name, 'viewer_data.json')
        if os.path.isfile(full):
            out.append(name)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', help='只修复指定日期 (YYYY-MM-DD)')
    p.add_argument('--sleep', type=float, default=1.5, help='下载间隔秒数 (默认 1.5)')
    p.add_argument('--dry-run', action='store_true', help='只列出缺失文件，不下载')
    p.add_argument('--no-api', action='store_true', help='仅用 log.json 缓存的 URL，不回退 API')
    args = p.parse_args()

    dates = [args.date] if args.date else list_dates()
    if not dates:
        print('没有找到任何 viewer_data.json')
        return

    total_missing = 0
    total_repaired = 0
    total_failed = 0
    for d in dates:
        print(f'==> 扫描 {d}')
        m, r, f = repair_one_day(d, args.sleep, args.dry_run, use_api=not args.no_api)
        if m == 0:
            print(f'    {d}: 全部完好')
        else:
            verb = '将重下' if args.dry_run else '已重下'
            print(f'    {d}: 缺失 {m} 张，{verb} {r} 张，失败 {f} 张')
        total_missing += m
        total_repaired += r
        total_failed += f

    print(f'\n完成。总计缺失 {total_missing} 张，'
          f'{"将重下" if args.dry_run else "已重下"} {total_repaired} 张，失败 {total_failed} 张。')


if __name__ == '__main__':
    main()
