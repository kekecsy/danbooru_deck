import re
import json
import requests
from my_utils import get_proxies_for_url  # 使用你自己的代理工具函数

# Yande.re API 基础地址
BASE_URL = "https://yande.re"

def get_post_from_id(post_id):
    """通过帖子 ID 获取帖子信息"""
    url = f"{BASE_URL}/post.json"
    params = {
        "tags": f"id:{post_id}",
        "limit": 1
    }
    proxies = get_proxies_for_url(url)      # 根据 URL 自动获取代理
    resp = requests.get(url, params=params, proxies=proxies)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        print(f"未找到 ID 为 {post_id} 的帖子。")
        return None
    return data[0]

def get_post_from_md5(md5):
    """通过图片 MD5 获取帖子信息"""
    url = f"{BASE_URL}/post.json"
    params = {
        "tags": f"md5:{md5}",
        "limit": 1
    }
    proxies = get_proxies_for_url(url)
    resp = requests.get(url, params=params, proxies=proxies)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        print(f"未找到 MD5 为 {md5} 的帖子。")
        return None
    return data[0]

def parse_input(url_str):
    """
    解析用户输入的 Yande.re 网址，返回 (类型, 值)
    类型: 'id' 或 'md5'
    """
    # 匹配帖子页面：/post/show/数字
    post_match = re.search(r'/post/show/(\d+)', url_str)
    if post_match:
        return 'id', post_match.group(1)

    # 匹配图片直链：/image/ 后面一串 MD5 哈希值
    image_match = re.search(r'/image/([a-fA-F0-9]{32})', url_str)
    if image_match:
        return 'md5', image_match.group(1).lower()

    # 如果用户直接给纯数字 ID
    if url_str.strip().isdigit():
        return 'id', url_str.strip()

    # 如果用户给 32 位十六进制字符串（可能是 MD5）
    if re.fullmatch(r'[a-fA-F0-9]{32}', url_str.strip()):
        return 'md5', url_str.strip().lower()

    return None, None

def print_post_info(post):
    """格式化打印帖子详细信息"""
    print("\n========== 帖子信息 ==========")
    print(f"ID:          {post.get('id')}")
    print(f"上传者:      {post.get('author')}")
    print(f"创建时间:    {post.get('created_at')}")
    print(f"评分:        {post.get('rating')}  (分数: {post.get('score')})")
    print(f"来源:        {post.get('source', '无')}")
    print(f"文件 URL:    {post.get('file_url')}")
    print(f"样本 URL:    {post.get('sample_url')}")
    print(f"预览 URL:    {post.get('preview_url')}")
    print(f"标签 (空格分隔): {post.get('tags')}")

def main():
    input_url = input("请输入 Yande.re 图片网址（帖子链接或图片直链）: ").strip()
    if not input_url:
        print("未输入任何内容，退出。")
        return

    typ, value = parse_input(input_url)
    if typ is None:
        print("无法识别的 URL 格式。请提供类似下列格式的链接：")
        print("  - 帖子页: https://yande.re/post/show/12345")
        print("  - 图片直链: https://yande.re/image/abc123.../image.png")
        print("  - 纯数字帖子 ID 或纯 32 位 MD5 值")
        return

    try:
        if typ == 'id':
            post = get_post_from_id(value)
        else:
            post = get_post_from_md5(value)

        if post:
            print_post_info(post)
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
    except json.JSONDecodeError:
        print("API 返回了非 JSON 数据，可能是服务器错误或限制。")

if __name__ == "__main__":
    main()