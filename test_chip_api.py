import requests
import json
import warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

def test_twse_chip():
    # Use 20260320 as a recent weekday
    url = "https://www.twse.com.tw/fund/T86?response=json&date=20260320&selectType=ALL"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        data = r.json()
        print(f"TWSE total items: {len(data.get('data', []))}")
        if data.get('data'):
            fields = data.get('fields', [])
            row = data['data'][0]
            print(f"TWSE Fields: {fields}")
            print(f"Sample TWSE: {row}")
    except Exception as e:
        print(f"TWSE Error: {e}")

def test_tpex_chip():
    # TPEX uses ROC year (2026 - 1911 = 115)
    url = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d=115/03/20&s=0,asc"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        data = r.json()
        print(f"\nTPEX total items: {len(data.get('aaData', []))}")
        if data.get('aaData'):
            row = data['aaData'][0]
            print(f"Sample TPEX: {row[:5]}") # Print first few columns
    except Exception as e:
        print(f"TPEX Error: {e}")

if __name__ == "__main__":
    test_twse_chip()
    test_tpex_chip()
