"""
大盤環境濾網模組 (Market Regime Detector) — 評分邏輯共用 utils/regime.py
功能:
  - 抓取加權指數 (^TWII) 與櫃買指數 (^TWOII) 資料
  - 判斷大盤是否站上 20MA (月線) / 60MA (季線)
  - 提供市場趨勢評分，決定是否執行突破策略
"""
import yfinance as yf
import pandas as pd
from datetime import datetime
from typing import Optional

from utils.logger import get_logger
from utils.regime import regime_score_from_closes

logger = get_logger(__name__)


class MarketRegimeDetector:
    def __init__(self, use_db_cache: bool = True):
        """
        use_db_cache: 是否優先從 SQLite 讀取指數資料。
          測試時設 False 可完全隔離 (只走 mock 的 yfinance)。
        """
        self.indices = {
            'TSE': '^TWII',
            'OTC': '^TWOII'
        }
        self.use_db_cache = use_db_cache
        # TTL 快取：同一小時內不重複下載
        self._cache: Optional[dict] = None
        self._cache_hour: Optional[int] = None

    def get_market_status(self, end_date: Optional[str] = None) -> dict:
        """
        取得大盤與櫃買的目前趨勢狀態 (連續分數版)
        end_date: 指定掃描日期 (YYYY-MM-DD)，若為 None 則使用目前最新資料。
        """
        # TTL 快取：同一小時內不重複下載 (僅限非指定日期模式)
        current_hour = datetime.now().hour
        if end_date is None and self._cache is not None and self._cache_hour == current_hour:
            return self._cache

        results = {}
        for name, ticker in self.indices.items():
            try:
                # 優先從資料庫抓取指數 (如果有)，否則從 yf 下載
                import data.database as db_module
                df = pd.DataFrame()
                if self.use_db_cache:
                    df = db_module.load_dataframe(ticker)

                if df.empty or (end_date and df.index.max() < pd.to_datetime(end_date)):
                    df = yf.download(ticker, period="1y", progress=False)
                    if not df.empty and self.use_db_cache:
                        db_module.save_dataframe(ticker, df)

                if df.empty:
                    results[name] = {"status": "Unknown", "score": 0}
                    continue

                # 如果有指定日期，執行裁切
                if end_date:
                    df = df.loc[:end_date]
                    if df.empty:
                        results[name] = {"status": "NoData", "score": 0}
                        continue

                # 評分邏輯統一使用 utils.regime (與回測/預計算共用同一實作)
                closes = df['Close'].squeeze()
                if isinstance(closes, pd.DataFrame):
                    closes = closes.iloc[:, 0]
                score, d = regime_score_from_closes(closes)

                if not d:
                    # 資料不足 60 筆 → 中性分數
                    results[name] = {"status": "InsufficientData",
                                     "score": round(score, 1)}
                    continue

                results[name] = {
                    "price": d["close"],
                    "ma20": d["ma20"],
                    "ma60": d["ma60"],
                    "is_above_ma20": bool(d["close"] > d["ma20"]),
                    "is_above_ma60": bool(d["close"] > d["ma60"]),
                    "score": round(score, 1),
                    "detail": {
                        "position": d["position"],
                        "slope": d["slope"],
                        "momentum": d["momentum"],
                    }
                }
            except Exception as e:
                logger.error(f"[MarketRegime] 抓取 {name} 失敗: {e}")
                results[name] = {"status": "Error", "score": 0}

        self._cache = results
        self._cache_hour = current_hour
        return results

    def get_market_score(self, end_date: Optional[str] = None) -> float:
        """取得大盤環境的量化分數 (0-100)，加權: TSE 60% + OTC 40%"""
        res = self.get_market_status(end_date=end_date)
        tse_score = res.get('TSE', {}).get('score', 0)
        otc_score = res.get('OTC', {}).get('score', 0)
        return round(tse_score * 0.6 + otc_score * 0.4, 1)

    def is_safe_to_trade(self, threshold: int = 50) -> bool:
        """是否適合交易 (預設門檻 50 分)"""
        return self.get_market_score() >= threshold


if __name__ == "__main__":
    detector = MarketRegimeDetector()
    status = detector.get_market_status()
    print("=== 大盤環境檢查 ===")
    for name, info in status.items():
        print(f"[{name}] 股價: {info.get('price')} | 20MA: {info.get('ma20')} | 60MA: {info.get('ma60')}")
        print(f"      站上月線: {info.get('is_above_ma20')} | 站上季線: {info.get('is_above_ma60')} | 分數: {info.get('score')}")
    print(f"\n適合交易: {detector.is_safe_to_trade()}")
