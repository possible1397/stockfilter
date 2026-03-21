import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
import concurrent.futures
import time
import random

import data.database as db_module
from utils.logger import get_logger

logger = get_logger(__name__)


class StockFetcher:
    def __init__(self, db_path: str = 'stock_data.db'):
        self.db_path = db_path
        db_module.init_db(self.db_path)

    @staticmethod
    def _is_trading_day(dt: datetime) -> bool:
        """判斷是否為交易日 (排除週末，國定假日不在此處理)"""
        return dt.weekday() < 5

    def fetch_stock_data(self, ticker: str) -> pd.DataFrame:
        """
        抓取單檔股票的日 K 資料 (含 SQLite 快取 + 增量下載)
        - 若資料庫已有今日最新資料 → 直接從快取讀取
        - 若為非交易日且有近期快取 → 直接使用快取
        - 若快取有部分資料 → 只下載缺少的增量部分
        - 若無任何快取 → 下載完整 1 年
        """
        today = datetime.today()
        today_date = today.date()
        latest_str = db_module.get_latest_date(ticker, self.db_path)

        if latest_str:
            try:
                latest_date = datetime.strptime(latest_str, '%Y-%m-%d').date()
            except ValueError:
                latest_date = None

            if latest_date:
                # 快取命中: 已有今日資料
                if latest_date >= today_date:
                    return db_module.load_dataframe(ticker, self.db_path)

                # 非交易日且快取在 3 天內 → 直接使用
                if not self._is_trading_day(today):
                    if (today_date - latest_date).days <= 3:
                        return db_module.load_dataframe(ticker, self.db_path)

                # 增量下載：只補缺少的資料
                start_date = (latest_date + timedelta(days=1)).strftime('%Y-%m-%d')
                return self._download_and_save(ticker, start=start_date)

        # 無任何快取：下載完整 1 年
        return self._download_and_save(ticker, period="1y")

    def _download_and_save(self, ticker: str, period: Optional[str] = None, start: Optional[str] = None) -> pd.DataFrame:
        """執行 yfinance 下載並寫入 SQLite，回傳完整 DataFrame"""
        try:
            stock = yf.Ticker(ticker)
            if start:
                df = stock.history(start=start)
            else:
                df = stock.history(period=period or "1y")

            if df is None or df.empty:
                if not start:  # 首次下載才印警告，增量下載無新資料是正常的
                    logger.warning(f"  [{ticker}] 查無資料 (可能已下市或代號不正確)")
                return db_module.load_dataframe(ticker, self.db_path)

            db_module.save_dataframe(ticker, df.copy(), self.db_path)
            time.sleep(random.uniform(0.2, 0.6))
            return db_module.load_dataframe(ticker, self.db_path)

        except Exception as e:
            logger.error(f"  [{ticker}] 下載失敗: {e}")
            # 若有快取資料則回傳快取，降低失敗影響
            cached = db_module.load_dataframe(ticker, self.db_path)
            return cached if not cached.empty else pd.DataFrame()

    def fetch_batch_data(self, tickers: list, max_workers: int = 5) -> dict:
        """
        以 ThreadPoolExecutor 多執行緒批次下載所有股票
        回傳: {ticker: DataFrame, ...}
        """
        results = {}
        total = len(tickers)
        done  = 0
        logger.info(f"[Fetcher] 使用 {max_workers} 執行緒下載 {total} 檔資料...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(self.fetch_stock_data, t): t for t in tickers}
            for future in concurrent.futures.as_completed(future_map):
                ticker = future_map[future]
                done += 1
                try:
                    results[ticker] = future.result()
                except Exception as exc:
                    logger.error(f"  [{ticker}] 執行緒例外: {exc}")
                    results[ticker] = pd.DataFrame()
                if done % 100 == 0 or done == total:
                    pct = done / total * 100
                    logger.info(f"[Fetcher] 進度: {done}/{total} ({pct:.1f}%)")
        return results

    def close(self):
        """相容介面：無狀態架構下不需要實際關閉連線"""
        pass
