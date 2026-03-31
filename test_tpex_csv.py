import requests
import warnings
import pandas as pd
import io
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

def test_tpex_csv():
    # Attempting to fetch CSV for 2024-03-20 (113/03/20)
    url = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=csv&se=EW&t=D&d=113/03/20"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        print(f"TPEX CSV length: {len(r.text)}")
        print(f"Sample:\n{r.text[:300]}")
    except Exception as e:
        print(f"TPEX Error: {e}")

if __name__ == "__main__":
    test_tpex_csv()
