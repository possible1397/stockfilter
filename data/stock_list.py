"""
台股上市/上櫃股票代號清單抓取模組
資料來源:
  - 上市 (TWSE): https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
  - 上櫃 (TPEX): https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap68_O

執行: python -m data.stock_list
"""
import requests
import json
import time
import os

import certifi

from utils.logger import get_logger

logger = get_logger(__name__)

_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'stock_list_cache.json')
_SSL_VERIFY = certifi.where()


def _get(url: str, timeout: int = 20):
    """統一 requests.get 包裝，優先使用 certifi；若 SSL 失敗則回退 verify=False 並警告。"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        return requests.get(url, headers=headers, timeout=timeout, verify=_SSL_VERIFY)
    except requests.exceptions.SSLError:
        logger.warning(f"SSL 驗證失敗，以不驗證模式重試: {url}")
        return requests.get(url, headers=headers, timeout=timeout, verify=False)


def fetch_twse_tickers() -> tuple:
    """
    從 TWSE 開放資料 API 取得上市股票代號清單
    回傳: (['0050.TW', ...], {'0050.TW': '元大台灣50', ...})
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        resp = _get(url)
        resp.raise_for_status()
        data = resp.json()
        tickers = []
        names = {}
        for item in data:
            code = item.get('Code', '')
            if 4 <= len(code) <= 5 and code.isdigit():
                ticker = f"{code}.TW"
                tickers.append(ticker)
                names[ticker] = item.get('Name', '')
        logger.info(f"  [TWSE] 取得上市股票: {len(tickers)} 檔")
        return tickers, names
    except Exception as e:
        logger.error(f"  [TWSE] 取得失敗: {e}")
        return [], {}


def fetch_tpex_tickers() -> tuple:
    """
    從 TPEX 開放資料 API 取得上櫃股票代號清單
    回傳: (['6176.TWO', ...], {'6176.TWO': '瑞儀', ...})
    """
    urls = [
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap68_O",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
    ]
    for url in urls:
        try:
            resp = _get(url)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                continue
            code_key = next(
                (k for k in data[0].keys() if 'code' in k.lower() or 'Code' in k),
                None
            )
            name_key = next(
                (k for k in data[0].keys() if 'name' in k.lower() or 'Name' in k),
                None
            )
            if not code_key:
                continue
            tickers = []
            names = {}
            for item in data:
                code = item.get(code_key, '')
                if code.isdigit() and 4 <= len(code) <= 5:
                    ticker = f"{code}.TWO"
                    tickers.append(ticker)
                    names[ticker] = item.get(name_key, '') if name_key else ''
            if tickers:
                logger.info(f"  [TPEX] 取得上櫃股票: {len(tickers)} 檔 (來源: {url})")
                return tickers, names
        except Exception as e:
            logger.warning(f"  [TPEX] {url} 失敗: {e}")
            continue
    logger.error("  [TPEX] 所有 URL 均失敗")
    return [], {}


def get_stock_name(ticker: str) -> str:
    """從快取查詢股票中文名稱，找不到回傳空字串"""
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            return cached.get('names', {}).get(ticker, '')
        except Exception:
            pass
    return ''


def get_all_tickers(use_cache: bool = True) -> list:
    """
    取得全台股 (上市 + 上櫃) 清單，支援本機快取
    use_cache: True 時，若快取存在且是今日所建立的，直接回傳快取
    """
    from datetime import datetime

    if use_cache and os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            cache_date = cached.get('date', '')
            today = datetime.today().strftime('%Y-%m-%d')
            if cache_date == today and cached.get('tickers'):
                logger.info(f"[StockList] 快取命中 ({today})，共 {len(cached['tickers'])} 檔")
                return cached['tickers']
        except Exception:
            pass

    logger.info("[StockList] 從 TWSE/TPEX 即時抓取全台股清單...")
    twse_tickers, twse_names = fetch_twse_tickers()
    time.sleep(1)
    tpex_tickers, tpex_names = fetch_tpex_tickers()

    all_tickers = twse_tickers + tpex_tickers
    all_names = {**twse_names, **tpex_names}

    # --- 優化: 排除 ETF 與非個股標的 ---
    # 台灣 ETF 代號通常以 00, 01 開頭，權證為 6 位數，或是包含英文字母的特別股
    # 我們只保留 4 位數純數字且不以 00, 01 開頭的個股
    filtered_tickers = []
    for t in all_tickers:
        code = t.split('.')[0]
        # 排除 00 (ETF), 01 (REITs)
        if code.startswith('00') or code.startswith('01'):
            continue
        # 排除權證 (6位數) 或非標準 4 位數標的
        if len(code) != 4 or not code.isdigit():
            continue
        filtered_tickers.append(t)
    
    logger.info(f"[StockList] 排除 ETF/非個股後剩下: {len(filtered_tickers)} 檔 (原 {len(all_tickers)} 檔)")
    all_tickers = filtered_tickers

    if not all_tickers:
        logger.warning("[StockList] 警告: 無法取得清單，請檢查網路連線")
        return []

    today = datetime.today().strftime('%Y-%m-%d')
    with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'date': today, 'tickers': all_tickers, 'names': all_names}, f, ensure_ascii=False)
    logger.info(f"[StockList] 合計 {len(all_tickers)} 檔，快取已存至: {_CACHE_FILE}")

    return all_tickers


if __name__ == "__main__":
    tickers = get_all_tickers(use_cache=False)
    print(f"\n前 10 檔上市: {tickers[:10]}")
    twse_count = sum(1 for t in tickers if t.endswith('.TW'))
    tpex_count  = sum(1 for t in tickers if t.endswith('.TWO'))
    print(f"上市: {twse_count} 檔 | 上櫃: {tpex_count} 檔 | 合計: {len(tickers)} 檔")
