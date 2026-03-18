import yfinance as yf
import pandas as pd
from datetime import datetime
import concurrent.futures
import time
import random

import data.database as db_module

class StockFetcher:
    def __init__(self, db_path: str = 'stock_data.db'):
        self.db_path = db_path
        # 確保資料庫與資料表已建立
        db_module.init_db(self.db_path)

    def fetch_stock_data(self, ticker: str) -> pd.DataFrame:
        """
        抓取單檔股票的日 K 資料 (含 SQLite 快取)
        - 若資料庫已有今日最新資料 -> 直接從快取讀取
        - 若無 -> 從 yfinance 下載後存入 SQLite，再回傳
        """
        today_str = datetime.today().strftime('%Y-%m-%d')
        latest = db_module.get_latest_date(ticker, self.db_path)

        if latest and latest >= today_str:
            # 快取命中
            print(f"  [{ticker}] 快取命中 (最新: {latest})，直接從 SQLite 讀取")
            return db_module.load_dataframe(ticker, self.db_path)

        # 需從網路下載
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period="1y")

            if df is None or df.empty:
                print(f"  [{ticker}] 查無資料 (可能已下市或代號不正確)")
                return pd.DataFrame()

            # 存入 SQLite (每個執行緒獨立 connection)
            db_module.save_dataframe(ticker, df.copy(), self.db_path)

            # 防 Rate Limiting: 随機延遲 0.2 ~ 0.6 秒
            time.sleep(random.uniform(0.2, 0.6))

            # 回傳標準化格式
            return db_module.load_dataframe(ticker, self.db_path)

        except Exception as e:
            print(f"  [{ticker}] 下載失敗: {e}")
            return pd.DataFrame()

    def fetch_batch_data(self, tickers: list, max_workers: int = 5) -> dict:
        """
        以 ThreadPoolExecutor 多執行緒批次下載所有股票
        回傳: {ticker: DataFrame, ...}
        """
        results = {}
        total = len(tickers)
        done  = 0
        print(f"[Fetcher] 使用 {max_workers} 執行緒下載 {total} 檔資料...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(self.fetch_stock_data, t): t for t in tickers}
            for future in concurrent.futures.as_completed(future_map):
                ticker = future_map[future]
                done += 1
                try:
                    results[ticker] = future.result()
                except Exception as exc:
                    print(f"  [{ticker}] 執行緒例外: {exc}")
                    results[ticker] = pd.DataFrame()
                # 每 100 檔顯示進度
                if done % 100 == 0 or done == total:
                    pct = done / total * 100
                    print(f"[Fetcher] 進度: {done}/{total} ({pct:.1f}%)")
        return results

    def close(self):
        """相容介面：無狀態架構下不需要實際關閉連線"""
        pass


if __name__ == "__main__":
    fetcher = StockFetcher()
    print("=== 單檔測試: 2330.TW ===")
    df = fetcher.fetch_stock_data("2330.TW")
    if not df.empty:
        print(f"筆數: {len(df)}")
        print(df.tail())
    else:
        print("資料為空")
