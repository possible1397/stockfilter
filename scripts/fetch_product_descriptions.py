"""
從鉅亨網批次爬取台股主要產品資訊，補進 supply_chain_data.json
用法: python scripts/fetch_product_descriptions.py
"""
import json
import sys
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path

# Windows 終端機強制 utf-8 輸出
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SUPPLY_CHAIN_PATH = Path('data/supply_chain_data.json')
OUTPUT_PATH = Path('data/product_descriptions.json')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  'Chrome/120.0.0.0 Safari/537.36'
}


def fetch_products(code: str) -> str:
    """從鉅亨網爬取股票主要產品描述，回傳中文字串"""
    # 去掉 .TW / .TWO 後綴
    raw_code = code.replace('.TW', '').replace('.TWO', '')
    url = f'https://www.cnyes.com/twstock/intro.aspx?code={raw_code}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.content, 'html.parser', from_encoding=r.apparent_encoding)
        tables = soup.find_all('table')

        if len(tables) > 2:
            rows = tables[2].find_all('tr')
            products = []
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cols) >= 4:
                    pct = cols[3].replace('.', '').replace('-', '')
                    if pct.isdigit() and cols[1] and cols[1] not in ('其他', '小計', '合計'):
                        try:
                            pct_float = float(cols[3])
                        except ValueError:
                            continue
                        if pct_float > 1.0:  # 排除佔比 < 1% 的雜項
                            products.append(f'{cols[1]}({cols[3]}%)')
            if products:
                return ' / '.join(products[:4])

        # fallback: 業務簡介文字
        if len(tables) > 0:
            text = tables[0].get_text(separator='|')
            idx = text.find('主要業務')
            if idx >= 0:
                snippet = text[idx+5:idx+80].split('|')[0].strip()
                if snippet:
                    return snippet
    except Exception as e:
        print(f'  [{code}] 爬取失敗: {e}')
    return ''


def get_all_tickers(data: dict) -> set:
    """從 supply_chain_data.json 取出所有股票代號"""
    tickers = set()
    for chain in data.values():
        for key in ('上游', '中游', '下游', '全產業'):
            val = chain.get(key, [])
            if isinstance(val, list):
                tickers.update(val)
            elif isinstance(val, dict):
                for v in val.values():
                    if isinstance(v, list):
                        tickers.update(v)
    return tickers


def main():
    with open(SUPPLY_CHAIN_PATH, encoding='utf-8') as f:
        chain_data = json.load(f)

    # 載入已有的描述（避免重複爬）
    if OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding='utf-8') as f:
            descriptions = json.load(f)
    else:
        descriptions = {}

    tickers = sorted(get_all_tickers(chain_data))
    total = len(tickers)
    print(f'共 {total} 支股票需要爬取產品描述')
    print(f'已有 {len(descriptions)} 筆，需爬 {total - len(descriptions)} 筆\n')

    updated = 0
    for i, ticker in enumerate(tickers, 1):
        if ticker in descriptions:
            continue  # 已有資料跳過

        desc = fetch_products(ticker)
        descriptions[ticker] = desc
        status = desc if desc else '(無資料)'
        print(f'[{i:4}/{total}] {ticker:<12} {status}')
        updated += 1

        # 每 10 筆存一次，避免中途失敗全部消失
        if updated % 10 == 0:
            with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
                json.dump(descriptions, f, ensure_ascii=False, indent=2)

        time.sleep(0.5)  # 避免被封

    # 最後存檔
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(descriptions, f, ensure_ascii=False, indent=2)

    filled = sum(1 for v in descriptions.values() if v)
    print(f'\n完成！共 {len(descriptions)} 筆，有資料 {filled} 筆，無資料 {len(descriptions)-filled} 筆')
    print(f'已存至: {OUTPUT_PATH}')


if __name__ == '__main__':
    main()
