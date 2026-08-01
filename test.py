from curl_cffi import requests  # 确保使用的是 curl_cffi

# 目标 API 地址
url = "https://danbooru.donmai.us/posts.json"

# 根据你提供的链接：https://danbooru.donmai.us/posts?d=1&tags=order:rank
params = {
    "tags": "order:rank",
    "d": "1",      # 把 d=1 加入参数
    "limit": 20,
    "page": 1
}

# 建议手动写死代理字典，确保格式正确
proxies = {
    "http": "http://127.0.0.1:7897",
    "https": "http://127.0.0.1:7897"
}

headers = {
    # 务必保证这个 UA 和你复制 COOKIES 时的浏览器 UA 一致
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Referer": "https://danbooru.donmai.us/posts",
    "Accept": "application/json" # 明确告诉服务器你要 JSON
}

try:
    r = requests.get(
        url,
        params=params,
        headers=headers,
        proxies=proxies,
        impersonate="chrome120", # curl_cffi 的核心功能，模拟指纹
        timeout=30
    )

    print(f"状态码: {r.status_code}")

    if r.status_code == 200:
        data = r.json()
        print(f"成功获取 {len(data)} 条数据！")
        # 打印第一条数据的 ID 验证一下
        if data:
            print(f"第一条数据的 ID: {data[0].get('id')}")
    else:
        print("访问失败，返回内容：")
        print(r.text[:500]) # 打印前 500 字符看错误原因

except Exception as e:
    print(f"代码执行报错: {e}")