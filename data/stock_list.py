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


def fetch_twse_tickers() -> list:
    """
    從 TWSE 開放資料 API 取得上市股票代號清單
    回傳: ['0050.TW', '0051.TW', ..., '2330.TW', ...]
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        resp = _get(url)
        resp.raise_for_status()
        data = resp.json()
        tickers = [
            f"{item['Code']}.TW" for item in data
            if 4 <= len(item.get('Code', '')) <= 5 and item.get('Code', '').isdigit()
        ]
        logger.info(f"  [TWSE] 取得上市股票: {len(tickers)} 檔")
        return tickers
    except Exception as e:
        logger.error(f"  [TWSE] 取得失敗: {e}")
        return []


def fetch_tpex_tickers() -> list:
    """
    從 TPEX 開放資料 API 取得上櫃股票代號清單
    回傳: ['6176.TWO', '6183.TWO', ...]
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
            if not code_key:
                continue
            tickers = [
                f"{item[code_key]}.TWO" for item in data
                if item.get(code_key, '').isdigit() and
                   4 <= len(item.get(code_key, '')) <= 5
            ]
            if tickers:
                logger.info(f"  [TPEX] 取得上櫃股票: {len(tickers)} 檔 (來源: {url})")
                return tickers
        except Exception as e:
            logger.warning(f"  [TPEX] {url} 失敗: {e}")
            continue
    logger.error("  [TPEX] 所有 URL 均失敗")
    return []


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
    twse = fetch_twse_tickers()
    time.sleep(1)
    tpex = fetch_tpex_tickers()

    all_tickers = twse + tpex

    if not all_tickers:
        logger.warning("[StockList] 警告: 無法取得清單，請檢查網路連線")
        return []

    today = datetime.today().strftime('%Y-%m-%d')
    with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'date': today, 'tickers': all_tickers}, f, ensure_ascii=False)
    logger.info(f"[StockList] 合計 {len(all_tickers)} 檔，快取已存至: {_CACHE_FILE}")

    return all_tickers


if __name__ == "__main__":
    tickers = get_all_tickers(use_cache=False)
    print(f"\n前 10 檔上市: {tickers[:10]}")
    twse_count = sum(1 for t in tickers if t.endswith('.TW'))
    tpex_count  = sum(1 for t in tickers if t.endswith('.TWO'))
    print(f"上市: {twse_count} 檔 | 上櫃: {tpex_count} 檔 | 合計: {len(tickers)} 檔")
