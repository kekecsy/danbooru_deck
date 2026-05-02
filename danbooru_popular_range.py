#!/usr/bin/env python3
# download_range.py - 下载指定日期范围的 Danbooru 热门图片

import argparse
from datetime import datetime, timedelta
import danbooru_popular

def date_range(start_date, end_date):
    """生成从 start_date 到 end_date 的每一天（包含两端）"""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    delta = timedelta(days=1)
    current = start
    while current <= end:
        yield current.strftime("%Y-%m-%d")
        current += delta

def main():
    parser = argparse.ArgumentParser(
        description="批量下载 Danbooru 指定日期范围内的热门图片"
    )
    parser.add_argument("--start_date", required=True,
                        help="起始日期，格式 YYYY-MM-DD，例如 2026-05-01")
    parser.add_argument("--end_date", required=True,
                        help="结束日期，格式 YYYY-MM-DD，例如 2026-05-10")
    parser.add_argument("--page_start", type=int, default=1,
                        help="起始页码（默认 1）")
    parser.add_argument("--page_end", type=int, default=35,
                        help="结束页码（默认 35）")
    args = parser.parse_args()

    for date_str in date_range(args.start_date, args.end_date):
        print(f"\n========== 正在处理日期：{date_str} ==========")
        # 修改原脚本中的全局日期变量，使其处理目标日期
        danbooru_popular.TODAY_STR = date_str
        try:
            danbooru_popular.run(page_start=args.page_start,
                                 page_end=args.page_end)
        except Exception as e:
            print(f"处理日期 {date_str} 时出错：{e}")
            continue

if __name__ == "__main__":
    main()