import requests
import pandas as pd
import io
import urllib3
from datetime import datetime, timedelta
from typing import Optional

# 關閉 SSL 警告 (TDCC 有時會有證書問題)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from data.database import save_chip_data, get_connection
from utils.logger import get_logger

logger = get_logger(__name__)

TDCC_URL = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"

class TDCCCrawler:
    """
    集保戶股權分散表爬蟲 (每週更新一次)
    官方 CSV URL: https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5
    """

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def fetch_latest_csv(self) -> Optional[pd.DataFrame]:
        """
        下載最新的股權分散表 CSV
        """
        try:
            logger.info(f"[TDCC] 正在從 {TDCC_URL} 下載資料...")
            # 使用 verify=False 規避證書問題
            response = requests.get(TDCC_URL, headers=self.headers, timeout=30, verify=False)
            response.raise_for_status()
            
            # 使用 io.StringIO 讀取 CSV
            df = pd.read_csv(io.StringIO(response.text))
            logger.info(f"[TDCC] 下載成功，共 {len(df)} 筆原始紀錄")
            return df
        except Exception as e:
            logger.error(f"[TDCC] 下載 CSV 失敗: {e}")
            return None

    def process_data(self, df: pd.DataFrame):
        """
        解析 CSV 並聚合 400張/1000張 持股比例
        CSV 欄位: 資料日期, 證券代號, 持股分級, 人數, 股數, 占集保庫存數比例%
        """
        if df is None or df.empty:
            return

        # 欄位重新命名以利處理
        df.columns = ['date', 'ticker', 'class', 'people', 'shares', 'percent']
        
        # 轉化日期格式 20260320 -> 2026-03-20
        df['date'] = df['date'].astype(str).apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:8]}")
        
        # 處理代號
        # 1. 移除空白 (如 "2330  ")
        # 2. 加上 .TW 或 .TWO (這部分較難自動判定，通常建議僅存純數字，或配合現有的 ticker 定義)
        df['ticker'] = df['ticker'].astype(str).str.strip()
        
        # 聚合分級數據
        # 400張以上: 持股分級 12~17 (或依據實際 CSV 最大值)
        # 1000張以上: 持股分級 15~17
        
        logger.info("[TDCC] 正在聚合大戶比例...")
        
        # 建立 pivot table
        # 我們只關心 percent
        pivot = df.pivot_table(index=['ticker', 'date'], columns='class', values='percent', aggfunc='sum').fillna(0)
        
        results = []
        for (ticker, date), row in pivot.iterrows():
            # 輔助判定：如果是台灣市場，大戶定義通常是：
            # 400張以上 = class 12, 13, 14, 15, 16, 17
            # 1000張以上 = class 15, 16, 17
            
            # 注意：這裡假設 CSV 包含所有 classes 1~17。
            # 如果某些股票沒有高分級，row 中可能缺少對應欄位，可以使用 .get(col, 0)
            
            p400 = sum([row.get(c, 0) for c in [12, 13, 14, 15, 16, 17]])
            p1000 = sum([row.get(c, 0) for c in [15, 16, 17]])
            
            # 限制個股（4位數純數字）
            if len(ticker) == 4 and ticker.isdigit():
                # 為了配合現有資料庫 (ticker.TW / ticker.TWO)，這裡需要稍微處理
                # 目前先存入純數字，database 寫入時再處理或在 analyze 時處理
                # 或者我們 fetch 現有 stock_list 來比對
                results.append({
                    'ticker': ticker,
                    'date': date,
                    'big_player_400': round(p400, 2),
                    'big_player_1000': round(p1000, 2)
                })

        return pd.DataFrame(results)

    def run_update(self):
        """
        執行完整更新流程
        """
        df_raw = self.fetch_latest_csv()
        if df_raw is None:
            return
            
        df_processed = self.process_data(df_raw)
        if df_processed is None or df_processed.empty:
            logger.warning("[TDCC] 解析後無效資料")
            return
            
        logger.info(f"[TDCC] 開始寫入資料庫, 共 {len(df_processed)} 檔股票...")
        
        # 分批寫入以避免連線過久
        # 注意：目前的 database.save_chip_data 預期 ticker 是完整的 (例如 2330.TW)
        # 我們需要根據 database 中的現有 ticker 來補齊字尾
        
        # 取得資料庫中現有的 ticker 對照表
        with get_connection() as conn:
            query = "SELECT DISTINCT ticker FROM daily_prices"
            existing_tickers = pd.read_sql(query, conn)['ticker'].tolist()
            ticker_map = {t.split('.')[0]: t for t in existing_tickers if '.' in t}
            
        # 建立完整 Ticker 的 DataFrame
        df_processed['full_ticker'] = df_processed['ticker'].map(ticker_map)
        df_final = df_processed.dropna(subset=['full_ticker']).copy()
        df_final['ticker'] = df_final['full_ticker']
        
        if df_final.empty:
            logger.warning("[TDCC] 沒有可更新的個股資料 (請確認庫存資料是否已初始化)")
            return

        # 批次寫入
        from data.database import save_bulk_chip_data
        save_bulk_chip_data(df_final[['ticker', 'date', 'big_player_400', 'big_player_1000']])
                
        logger.info(f"[TDCC] 更新完成，成功寫入 {len(df_final)} 檔個股大戶資料")

if __name__ == "__main__":
    crawler = TDCCCrawler()
    crawler.run_update()
