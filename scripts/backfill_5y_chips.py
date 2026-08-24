import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import time
import random
from datetime import datetime, timedelta
from typing import List

from data.fetcher import StockFetcher
from data.database import save_bulk_chip_data, get_connection
from utils.logger import get_logger

logger = get_logger(__name__)

class HistoryBackfiller:
    """
    歷史法人資料補齊器
    對接 TWSE/TPEX 官方 API 進行逐日抓取
    """
    def __init__(self):
        self.fetcher = StockFetcher()

    def _is_holiday(self, date_dt: datetime) -> bool:
        # TODO: 這裡目前的邏輯僅排除週末，國定假日需對接 API 或維護清單
        # 但官方 API 若為假日會回傳空資料，所以影響不大
        return date_dt.weekday() >= 5

    def run_range(self, start_date_str: str, end_date_str: str):
        """
        抓取特定區間的全市場法人資料
        """
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        
        current_date = start_date
        total_days = (end_date - start_date).days + 1
        processed_days = 0
        
        logger.info(f"[History] 開始補齊歷史資料: {start_date_str} -> {end_date_str}")
        
        while current_date <= end_date:
            processed_days += 1
            if not self._is_holiday(current_date):
                date_str_twse = current_date.strftime("%Y%m%d")
                date_str_iso = current_date.strftime("%Y-%m-%d")
                
                # 檢查資料庫是否已有資料，避免重複抓取 (節省流量與時間)
                if self._check_data_exists(date_str_iso):
                    logger.debug(f"  [Skip] {date_str_iso} 已有資料")
                    current_date += timedelta(days=1)
                    continue

                logger.info(f"  [{processed_days}/{total_days}] 正在抓取 {date_str_iso}...")
                
                # 使用 fetcher 內部的抓取邏輯
                df_twse = self.fetcher._fetch_twse_chip_day(date_str_twse)
                df_tpex = self.fetcher._fetch_tpex_chip_day(current_date)
                
                if not df_twse.empty or not df_tpex.empty:
                    df_all = pd.concat([df_twse, df_tpex], ignore_index=True)
                    if not df_all.empty:
                        save_bulk_chip_data(df_all)
                        logger.info(f"    -> 成功! 寫入 {len(df_all)} 檔股票")
                    else:
                        logger.warning(f"    -> {date_str_iso} 無有效資料")
                else:
                    logger.debug(f"    -> {date_str_iso} 查無資料 (非交易日或 API 異常)")
                
                # 嚴格限速，避免被鎖
                time.sleep(random.uniform(2.5, 4.0))
            
            current_date += timedelta(days=1)

    def _check_data_exists(self, date_str: str) -> bool:
        """檢查資料庫某日是否已有數據"""
        try:
            with get_connection() as conn:
                # 檢查是否有足夠多的資料 (例如 > 500 檔)，代表下載完整
                cursor = conn.execute("SELECT COUNT(*) FROM chip_data WHERE date = ?", (date_str,))
                res = cursor.fetchone()
                return res[0] > 500 if res else False
        except Exception:
            return False

if __name__ == "__main__":
    backfiller = HistoryBackfiller()
    
    # 執行 5 年完整補齊 (2020 - 2024)
    # 註: 已完成的 2025 年資料會被自動跳過
    backfiller.run_range("2020-01-01", "2024-12-31")
