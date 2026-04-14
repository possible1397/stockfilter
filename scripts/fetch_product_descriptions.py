"""
批次爬取台股主要產品資訊。
資料來源優先順序：
  1. 鉅亨網 cnyes.com（有百分比的中文產品明細）
  2. yfinance info（英文 industry / longBusinessSummary）
用法:
  python scripts/fetch_product_descriptions.py          # 全量（供應鏈 + 掃描清單）
  python scripts/fetch_product_descriptions.py --force  # 重新爬所有（含已有資料）
"""
import json
import sys
import time
import requests
import yfinance as yf
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

        if not tables:
            return ''

        # 策略 1: 尋找包含「主要經營業務」關鍵字的表格
        for table in tables:
            text = table.get_text()
            if '主要經營業務' in text or '主要產品' in text:
                rows = table.find_all('tr')
                # 取得該關鍵字所在行之後的文字，或同一單元格的內容
                for row in rows:
                    cells = [td.get_text(strip=True) for td in row.find_all('td')]
                    if not cells: continue
                    # 若第一格就是關鍵字，回傳第二格或整行內容
                    for i, cell in enumerate(cells):
                        if '主要經營業務' in cell or '主要產品' in cell:
                            desc = ""
                            if i + 1 < len(cells):
                                desc = cells[i+1]
                            else:
                                desc = cells[i].replace('主要經營業務', '').replace('主要產品', '').strip(':').strip('：').strip()
                            
                            if desc and '頁面自動彙整' not in desc:
                                return desc[:200] # 限制長度

        # 策略 2: 原本的營收佔比表格 (通常是最後一個 table 或 tables[2])
        # 遍歷所有表格尋找有百分比符號的
        for table in reversed(tables):
            rows = table.find_all('tr')
            products = []
            for row in rows:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cols) >= 3:
                    # 檢查最後一格是否有 %
                    last_col = cols[-1]
                    if '%' in last_col:
                        try:
                            pct_str = last_col.replace('%', '').strip()
                            if pct_str and float(pct_str) > 1.0:
                                name = cols[1] if len(cols) > 2 else cols[0]
                                if name and name not in ('其他', '小計', '合計', '項目'):
                                    products.append(f'{name}({pct_str}%)')
                        except ValueError:
                            continue
            if products:
                return ' / '.join(products[:4])

    except Exception as e:
        print(f'  [{code}] 爬取失敗: {e}')
    return ''


def fetch_products_yfinance(ticker: str) -> str:
    """yfinance fallback — 當鉅亨網無資料時使用（壓制 yfinance 的 404 stderr 噪音）"""
    import os, io, contextlib
    try:
        # 壓制 yfinance 印到 stderr 的 HTTP 404 錯誤
        with open(os.devnull, 'w') as devnull, contextlib.redirect_stderr(devnull):
            info = yf.Ticker(ticker).info
        industry = info.get('industry', '')
        summary = info.get('longBusinessSummary', '')
        if industry:
            snippet = summary[:60].rstrip() + '...' if len(summary) > 60 else summary
            return f'[{industry}] {snippet}'.strip() if snippet else industry
    except Exception:
        pass
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


def get_scan_tickers() -> set:
    """從 stock_list_cache.json 取出全市場掃描清單（含上市+上櫃）"""
    cache = Path('stock_list_cache.json')
    if not cache.exists():
        return set()
    with open(cache, encoding='utf-8') as f:
        data = json.load(f)
    return set(data.get('tickers', []))


def main():
    with open(SUPPLY_CHAIN_PATH, encoding='utf-8') as f:
        chain_data = json.load(f)

    # 載入已有的描述（避免重複爬）
    if OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding='utf-8') as f:
            descriptions = json.load(f)
    else:
        descriptions = {}

    force = '--force' in sys.argv

    # 合併供應鏈清單 + 全市場掃描清單
    chain_tickers = get_all_tickers(chain_data)
    scan_tickers = get_scan_tickers()
    all_tickers = chain_tickers | scan_tickers
    tickers = sorted(all_tickers)
    total = len(tickers)
    print(f'供應鏈清單: {len(chain_tickers)} 支 | 全市場清單: {len(scan_tickers)} 支 | 合計去重: {total} 支')
    missing = sum(1 for t in tickers if t not in descriptions or not descriptions[t])
    print(f'已有資料: {len(descriptions)} 筆 | 待爬（含空值）: {missing} 筆\n')

    updated = 0
    for i, ticker in enumerate(tickers, 1):
        if not force and ticker in descriptions and descriptions[ticker]:
            continue  # 已有非空資料跳過

        # 1. 鉅亨網
        desc = fetch_products(ticker)

        # 2. yfinance fallback（鉅亨網無資料才用）
        if not desc:
            desc = fetch_products_yfinance(ticker)
            if desc:
                print(f'[{i:4}/{total}] {ticker:<12} [yf] {desc[:60]}')
            else:
                print(f'[{i:4}/{total}] {ticker:<12} (無資料)')
        else:
            print(f'[{i:4}/{total}] {ticker:<12} {desc[:60]}')

        descriptions[ticker] = desc
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
