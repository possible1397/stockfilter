"""
大盤環境濾網模組 (Market Regime Detector)
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

logger = get_logger(__name__)


class MarketRegimeDetector:
    def __init__(self):
        self.indices = {
            'TSE': '^TWII',
            'OTC': '^TWOII'
        }
        # TTL 快取：同一小時內不重複下載
        self._cache: Optional[dict] = None
        self._cache_hour: Optional[int] = None

    def get_market_status(self) -> dict:
        """
        取得大盤與櫃買的目前趨勢狀態 (連續分數版)
        同一小時內多次呼叫會使用快取，避免重複下載指數資料。
        """
        current_hour = datetime.now().hour
        if self._cache is not None and self._cache_hour == current_hour:
            return self._cache

        results = {}
        for name, ticker in self.indices.items():
            try:
                df = yf.download(ticker, period="6mo", progress=False)
                if df.empty:
                    results[name] = {"status": "Unknown", "score": 0}
                    continue

                closes = df['Close'].values.flatten()
                close = float(closes[-1])

                all_ma20 = df['Close'].rolling(window=20).mean().values.flatten()
                all_ma60 = df['Close'].rolling(window=60).mean().values.flatten()
                ma20 = float(all_ma20[-1])
                ma60 = float(all_ma60[-1])

                # 維度 1: MA 位置分 (0~40)
                dist_ma20 = (close - ma20) / ma20 * 100
                dist_ma60 = (close - ma60) / ma60 * 100
                pos_score_20 = max(0, min(20, 10 + dist_ma20 * 2))
                pos_score_60 = max(0, min(20, 10 + dist_ma60 * 2))
                position_score = pos_score_20 + pos_score_60

                # 維度 2: MA20 斜率分 (0~30)
                if len(all_ma20) >= 6:
                    ma20_5d_ago = float(all_ma20[-6])
                    ma20_slope_pct = (ma20 - ma20_5d_ago) / ma20_5d_ago * 100
                    slope_score = max(0, min(30, 15 + ma20_slope_pct * 10))
                else:
                    slope_score = 15.0

                # 維度 3: 短期動能分 (0~30)
                if len(closes) >= 6:
                    ret_5d = (close - float(closes[-6])) / float(closes[-6]) * 100
                    momentum_score = max(0, min(30, 15 + ret_5d * 3))
                else:
                    momentum_score = 15.0

                score = round(position_score + slope_score + momentum_score, 1)

                results[name] = {
                    "price": round(float(close), 2),
                    "ma20": round(float(ma20), 2),
                    "ma60": round(float(ma60), 2),
                    "is_above_ma20": bool(close > ma20),
                    "is_above_ma60": bool(close > ma60),
                    "score": score,
                    "detail": {
                        "position": round(position_score, 1),
                        "slope": round(slope_score, 1),
                        "momentum": round(momentum_score, 1),
                    }
                }
            except Exception as e:
                logger.error(f"[MarketRegime] 抓取 {name} 失敗: {e}")
                results[name] = {"status": "Error", "score": 0}

        self._cache = results
        self._cache_hour = current_hour
        return results

    def get_market_score(self) -> float:
        """取得大盤環境的量化分數 (0-100)，加權: TSE 60% + OTC 40%"""
        res = self.get_market_status()
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
