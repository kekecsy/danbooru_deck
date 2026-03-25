from requests.utils import get_environ_proxies


def get_proxies_for_url(url):
    proxies = get_environ_proxies(url)
    if 'https' in proxies and proxies['https'].startswith('https://'):
        proxies['https'] = proxies['https'].replace('https://', 'http://', 1)
    return proxies


if __name__ == "__main__":
    url = "https://danbooru.donmai.us"
    # print(get_proxies_for_url(url))
    print(get_environ_proxies(url))