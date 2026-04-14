import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
import concurrent.futures
import time
import random

import requests
import io
import urllib3
import data.database as db_module
from utils.logger import get_logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = get_logger(__name__)


class StockFetcher:
    def __init__(self, db_path: str = 'stock_data.db'):
        self.db_path = db_path
        db_module.init_db(self.db_path)

    @staticmethod
    def _is_trading_day(dt: datetime) -> bool:
        """判斷是否為交易日 (排除週末，國定假日不在此處理)"""
        return dt.weekday() < 5

    def fetch_stock_data(self, ticker: str, target_date: Optional[datetime] = None) -> pd.DataFrame:
        """
        抓取單檔股票的日 K 資料 (含 SQLite 快取 + 增量下載)
        target_date: 指定掃描日期，若為 None 則為今日。
        """
        if target_date is None:
            today_date = datetime.today().date()
        else:
            today_date = target_date.date()

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
                if not self._is_trading_day(datetime.combine(today_date, datetime.min.time())):
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

    def fetch_batch_data(self, tickers: list, max_workers: int = 5, target_date: Optional[datetime] = None) -> dict:
        """
        以 ThreadPoolExecutor 多執行緒批次下載所有股票
        回傳: {ticker: DataFrame, ...}
        """
        results = {}
        total = len(tickers)
        done  = 0
        logger.info(f"[Fetcher] 使用 {max_workers} 執行緒下載 {total} 檔資料...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(self.fetch_stock_data, t, target_date): t for t in tickers}
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

    def _fetch_twse_chip_day(self, date_str: str) -> pd.DataFrame:
        """從 TWSE 取得單日上市法人買賣超 (date_str: YYYYMMDD)"""
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={date_str}&selectType=ALL"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            r = requests.get(url, headers=headers, timeout=15, verify=False)
            data = r.json()
            if data.get('stat') != 'OK' or not data.get('data'):
                return pd.DataFrame()
                
            fields = data['fields']
            df = pd.DataFrame(data['data'], columns=fields)
            
            # 精確比對 TWSE RWD API 的欄位名稱 (2026 年實測)
            col_map = {}
            for c in df.columns:
                if c == '證券代號':
                    col_map[c] = 'ticker'
                elif c == '外陸資買賣超股數(不含外資自營商)':
                    col_map[c] = 'foreign_net_buy'
                elif c == '投信買賣超股數':
                    col_map[c] = 'trust_net_buy'
                elif c == '自營商買賣超股數(自行買賣)':
                    col_map[c] = 'dealer_net_buy'
                
            required = {'ticker', 'foreign_net_buy', 'trust_net_buy', 'dealer_net_buy'}
            if not required.issubset(set(col_map.values())):
                logger.warning(f"[TWSE Chip] {date_str} 欄位比對不完整: {col_map}")
                return pd.DataFrame()

            df = df.rename(columns=col_map)
            df = df[['ticker', 'foreign_net_buy', 'trust_net_buy', 'dealer_net_buy']]
            df['ticker'] = df['ticker'].astype(str).str.strip() + '.TW'
            
            for col in ['foreign_net_buy', 'trust_net_buy', 'dealer_net_buy']:
                df[col] = df[col].astype(str).str.replace(',', '', regex=False).apply(pd.to_numeric, errors='coerce').fillna(0)
            
            df['date'] = pd.to_datetime(date_str).strftime('%Y-%m-%d')
            return df
        except Exception as e:
            logger.debug(f"[TWSE Chip] {date_str} 取得失敗或為非交易日: {e}")
            return pd.DataFrame()

    def _fetch_tpex_chip_day(self, date_dt: datetime) -> pd.DataFrame:
        """從 TPEX 取得單日上櫃法人買賣超 (date_dt: datetime)"""
        roc_year = date_dt.year - 1911
        date_str = f"{roc_year}/{date_dt.strftime('%m/%d')}"
        url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=csv&se=EW&t=D&d={date_str}&s=0,asc"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            r = requests.get(url, headers=headers, timeout=15, verify=False)
            r_text = r.text.strip()
            if not r_text or len(r_text) < 100:
                return pd.DataFrame()

            # TPEX CSV 格式：第 0 行是標題，第 1 行是欄位名，第 2 行起是資料
            # 用 skiprows=1 跳過標題行，讓 pandas 讀取欄位名
            df = pd.read_csv(io.StringIO(r_text), skiprows=1, engine='python',
                             on_bad_lines='skip')
            
            col_map = {}
            for c in df.columns:
                c_str = str(c).strip()
                if c_str == '代號':
                    col_map[c] = 'ticker'
                elif c_str == '外資及陸資(不含外資自營商)-買賣超股數':
                    col_map[c] = 'foreign_net_buy'
                elif c_str == '投信-買賣超股數':
                    col_map[c] = 'trust_net_buy'
                elif c_str == '自營商(自行買賣)-買賣超股數':
                    col_map[c] = 'dealer_net_buy'
                
            df = df.rename(columns=col_map)
            if 'ticker' not in df.columns:
                logger.warning(f"[TPEX Chip] {date_dt.strftime('%Y-%m-%d')} 欄位比對失敗: {list(df.columns)[:5]}")
                return pd.DataFrame()

            required = {'ticker', 'foreign_net_buy', 'trust_net_buy', 'dealer_net_buy'}
            if not required.issubset(set(col_map.values())):
                logger.warning(f"[TPEX Chip] {date_dt.strftime('%Y-%m-%d')} 欄位不完整: {col_map}")
                return pd.DataFrame()
                
            df = df[['ticker', 'foreign_net_buy', 'trust_net_buy', 'dealer_net_buy']]
            # 清除無效行（如 footer 統計行，ticker 不是數字開頭的）
            df = df[df['ticker'].astype(str).str.match(r'^\d')]
            df['ticker'] = df['ticker'].astype(str).str.strip() + '.TWO'
            
            for col in ['foreign_net_buy', 'trust_net_buy', 'dealer_net_buy']:
                df[col] = df[col].astype(str).str.replace(',', '', regex=False).apply(pd.to_numeric, errors='coerce').fillna(0)
                
            df['date'] = date_dt.strftime('%Y-%m-%d')
            return df
        except Exception as e:
            logger.debug(f"[TPEX Chip] {date_dt.strftime('%Y-%m-%d')} 取得失敗或為非交易日: {e}")
            return pd.DataFrame()

    def update_market_chip_data(self, days: int = 30):
        """下載過去 N 個交易日的法人籌碼資料，批次寫入 SQLite"""
        logger.info(f"[Fetcher] 開始更新全市場籌碼資料 (過去 {days} 交易日)...")
        today = datetime.today()
        current_date = today
        days_processed = 0
        
        while days_processed < days:
            if self._is_trading_day(current_date):
                twse_date = current_date.strftime('%Y%m%d')
                df_twse = self._fetch_twse_chip_day(twse_date)
                df_tpex = self._fetch_tpex_chip_day(current_date)
                
                if not df_twse.empty or not df_tpex.empty:
                    df_all = pd.concat([df_twse, df_tpex], ignore_index=True)
                    if not df_all.empty:
                        db_module.save_bulk_chip_data(df_all, self.db_path)
                    logger.info(f"  [Chip Data] 完成 {current_date.strftime('%Y-%m-%d')} ({len(df_all)} 檔)")
                else:
                    logger.debug(f"  [Chip Data] {current_date.strftime('%Y-%m-%d')} 查無資料 (可能為國定假日)")
                
                days_processed += 1
                time.sleep(random.uniform(0.5, 1.0))
                
            current_date -= timedelta(days=1)
            
            # 安全機制，避免進入無窮迴圈
            if (today - current_date).days > days * 3:
                break
        
        logger.info(f"[Fetcher] 籌碼資料更新完成！")

    def close(self):
        """相容介面：無狀態架構下不需要實際關閉連線"""
        pass
