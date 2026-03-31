import requests
import warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

def test_tpex_history():
    # Attempting to fetch 2024-03-20 (113/03/20 in Minguo calendar)
    url = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d=113/03/20&s=0,asc"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        data = r.json()
        print(f"TPEX total items (113/03/20): {len(data.get('aaData', []))}")
        if data.get('aaData'):
            print(f"Sample: {data['aaData'][0][:5]}")
    except Exception as e:
        print(f"TPEX Error: {e}")

if __name__ == "__main__":
    test_tpex_history()
