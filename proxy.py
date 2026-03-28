from my_utils import get_proxies_for_url


def main() -> None:
    """
    简单的代理调试入口:
    通过 shared 的 `get_proxies_for_url` 读取并转换代理设置后输出。
    """
    url = "https://danbooru.donmai.us"
    proxies = get_proxies_for_url(url)
    print(proxies)


if __name__ == "__main__":
    main()