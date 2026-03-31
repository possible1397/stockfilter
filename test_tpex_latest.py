import requests

def test_tpex_latest():
    url = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        data = r.json()
        print(f"TPEX Date: {data.get('reportDate')}")
        print(f"TPEX total items: {len(data.get('aaData', []))}")
        if data.get('aaData'):
            print(f"Sample: {data['aaData'][0][:5]}")
    except Exception as e:
        print(f"TPEX Error: {e}")

if __name__ == "__main__":
    test_tpex_latest()
