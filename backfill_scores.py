"""一次性回填脚本：为历史 viewer_data.json 条目补上 score / fav_count。

用法：
    python backfill_scores.py                          # 回填 hot_pic/ 下所有日期
    python backfill_scores.py --date 2026-05-12        # 只回填指定日期
    python backfill_scores.py --sleep 2.0              # 调整请求间隔（秒）
    python backfill_scores.py --force                  # 已有字段也重新拉取

幂等：默认跳过已有 score 字段的条目。每处理 20 条落盘一次，中断后重跑继续。
"""
import argparse
import os
import time
from danbooru_data import DanbooruData
import danbooru_api

BASE_DIR = './hot_pic'


def extract_post_id(post_url: str) -> int | None:
    if not post_url:
        return None
    tail = post_url.rstrip('/').rsplit('/', 1)[-1]
    return int(tail) if tail.isdigit() else None


def backfill_one_day(date_str: str, sleep_s: float, force: bool) -> tuple[int, int]:
    dd = DanbooruData(target_date=date_str)
    data = dd.load_viewer_data()
    if not data:
        return 0, 0

    changed = 0
    skipped = 0
    for i, item in enumerate(data):
        if not force and 'score' in item:
            skipped += 1
            continue
        post_id = extract_post_id(item.get('post_url', ''))
        if post_id is None:
            skipped += 1
            continue

        post = danbooru_api.fetch_data_with_retry(post_id)
        if post is None:
            print(f"  [{date_str}] post {post_id} 拉取失败，跳过")
            continue

        item['score'] = post.get('score', 0) or 0
        item['fav_count'] = post.get('fav_count', 0) or 0
        changed += 1

        if changed % 20 == 0:
            dd.save_viewer_data(data)
            print(f"  [{date_str}] 已回填 {changed} 条（落盘）")

        time.sleep(sleep_s)

    dd.save_viewer_data(data)
    return changed, skipped


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
    p.add_argument('--date', help='只回填指定日期 (YYYY-MM-DD)')
    p.add_argument('--sleep', type=float, default=1.5, help='请求间隔秒数 (默认 1.5)')
    p.add_argument('--force', action='store_true', help='已有 score 字段的条目也重新拉取')
    args = p.parse_args()

    dates = [args.date] if args.date else list_dates()
    if not dates:
        print('没有找到任何 viewer_data.json')
        return

    total_changed = 0
    total_skipped = 0
    for d in dates:
        print(f'==> 处理 {d}')
        c, s = backfill_one_day(d, args.sleep, args.force)
        print(f'    {d}: 回填 {c} 条，跳过 {s} 条')
        total_changed += c
        total_skipped += s

    print(f'\n完成。总计回填 {total_changed} 条，跳过 {total_skipped} 条。')


if __name__ == '__main__':
    main()
