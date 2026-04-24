import sys
from curl_cffi import requests
from my_utils import get_proxies_for_url

COOKIES = "_danbooru2_session=dzlzD2gYvCdxOQfBzhHUpXyPWZdvd8kXWARK6n0KdX4VDPgzGj9sLOyHfTrMVFdFpaJLHAP3LfMiptyeQeiNGE1yNM8tY7IGQXtYV2u8aFKglH7khCVW8FVqurTZSNR25VdDBgoTBDzu9p/gTeTrazCCWzLRPlg7hglvs6F6Xmfd7VVN/Mb+HbA5y7HAykGYk9kXDOTbE5s/HTOvPZd3hT6t/WcVUL8VlEW0nv1aiJt2h0byWwJBgBDGIvPgTebOWaH+xlRuqaHPhU0BmTEP+MtffzowVs9EQBUaO6LCky5e+fYQmcxXl68ANSAF/DQmp1EqppEU/TDW86rPMwoLCJZmIfC+XaAe5Z+PnwLV+DeOrBVtWdYWa8klazul6KqGHX8W6Z7WYMOoB9LpDfCzVnyDXOqCA+w2wbM2GuAUI7uH3A3Nuc/Z73esVF/qhN3CyImze/KOleoApwSRSjMXeb6oSpks1MvFGvN2lADMAtibu3cEfpC0glc+0YieVxc2J8TTQFVAhhSb+PYzohNdDR533ubSH5fikI2D8hiZmR1WSl1gWTol2eDkohCDmi5S+tqGOE1em0ZzJ/lpdfBhoJcwMYMA7dWp--YLXy4wFzNb8Zce8g--/4vINxtQJS/LT/9J9QoqKA=="
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Cookie": COOKIES,
    "Referer": "https://danbooru.donmai.us/posts",
    "Accept": "application/json"
}

proxies = get_proxies_for_url("https://danbooru.donmai.us")

def test_popular_json():
    print("Testing popular json...")
    r = requests.get(
        "https://danbooru.donmai.us/explore/posts/popular.json?date=2026-04-23&scale=day",
        headers=HEADERS,
        proxies=proxies,
        impersonate="chrome120"
    )
    print(r.status_code)
    try:
        print(len(r.json()))
    except:
        print("not json")

def test_post_json():
    print("Testing post json...")
    r = requests.get(
        "https://danbooru.donmai.us/posts/7488824.json",
        headers=HEADERS,
        proxies=proxies,
        impersonate="chrome120"
    )
    print(r.status_code)
    try:
        print("keys:", r.json().keys())
    except:
        print("not json")

if __name__ == "__main__":
    test_popular_json()
    test_post_json()
