import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TriangleResult:
    """儲存三角收斂形態的分析結果"""
    ticker: str
    is_converging: bool = False
    slope_up: float = 0.0
    slope_down: float = 0.0
    convergence_ratio: float = 1.0
    bar_span: int = 0
    current_price: float = 0.0
    resistance_price: float = 0.0
    support_price: float = 0.0
    is_breakout: bool = False
    pattern_score: float = 0.0
    breakout_score: float = 0.0
    sector: str = "Unknown"


class PatternRecognizer:
    """
    三角收斂形態識別器
    基於 scipy.signal.argrelextrema 找出局部高低點，
    並透過線性回歸判定壓力線/支撐線的斜率是否符合三角收斂幾何條件。
    """

    def __init__(self, order: int = 5, min_points: int = 3,
                 min_bars: int = 20, convergence_threshold: float = 0.3,
                 long_only: bool = True, trend_filter: bool = True, volume_dryup: bool = True):
        self.order = order
        self.min_points = min_points
        self.min_bars = min_bars
        self.convergence_threshold = convergence_threshold
        self.long_only = long_only
        self.trend_filter = trend_filter
        self.volume_dryup = volume_dryup

    def _find_pivot_points(self, df: pd.DataFrame):
        """
        找出局部高點與低點，只在最近 min_bars*2 根 K 線內搜尋以提升效能。
        """
        high_col = 'High' if 'High' in df.columns else 'high'
        low_col  = 'Low'  if 'Low'  in df.columns else 'low'

        # 只取最近需要的資料範圍，避免全歷史計算浪費
        window = self.min_bars * 2 + self.order * 2
        if len(df) > window:
            df_window = df.iloc[-window:]
            offset = len(df) - window
        else:
            df_window = df
            offset = 0

        highs = df_window[high_col].values
        lows  = df_window[low_col].values

        peak_idx   = argrelextrema(highs, np.greater, order=self.order)[0] + offset
        valley_idx = argrelextrema(lows,  np.less,    order=self.order)[0] + offset

        return peak_idx, valley_idx

    def _linear_regression(self, x: np.ndarray, y: np.ndarray):
        """對給定的 x, y 做線性回歸，回傳 (斜率 slope, 截距 intercept)"""
        if len(x) < 2:
            return None, None
        coeffs = np.polyfit(x, y, 1)
        return coeffs[0], coeffs[1]

    def analyze(self, ticker: str, df: pd.DataFrame) -> TriangleResult:
        """執行三角收斂分析，回傳 TriangleResult 物件"""
        result = TriangleResult(ticker=ticker)

        if df is None or len(df) < self.min_bars + self.order * 2:
            return result

        high_col  = 'High'   if 'High'   in df.columns else 'high'
        low_col   = 'Low'    if 'Low'    in df.columns else 'low'
        close_col = 'Close'  if 'Close'  in df.columns else 'close'
        vol_col   = 'Volume' if 'Volume' in df.columns else 'volume'

        # --- 實戰濾網 1: 大趨勢濾網 ---
        if self.trend_filter and len(df) >= 200:
            sma50  = df[close_col].rolling(window=50).mean().iloc[-1]
            sma200 = df[close_col].rolling(window=200).mean().iloc[-1]
            current_close = df[close_col].iloc[-1]
            if not (current_close > sma50 and sma50 > sma200):
                return result

        # --- 實戰濾網 2: 籌碼量縮濾網 (VCP) ---
        if self.volume_dryup and len(df) >= 50:
            vol_5d  = df[vol_col].tail(5).mean()
            vol_50d = df[vol_col].tail(50).mean()
            if vol_5d > vol_50d * 0.75:
                return result

        peak_idx, valley_idx = self._find_pivot_points(df)

        recent_peaks   = peak_idx[-5:]   if len(peak_idx)   >= self.min_points else peak_idx
        recent_valleys = valley_idx[-5:] if len(valley_idx) >= self.min_points else valley_idx

        if len(recent_peaks) < self.min_points or len(recent_valleys) < self.min_points:
            return result

        peak_prices   = df[high_col].values[recent_peaks]
        valley_prices = df[low_col].values[recent_valleys]

        slope_up,   intercept_up   = self._linear_regression(recent_peaks.astype(float),   peak_prices)
        slope_down, intercept_down = self._linear_regression(recent_valleys.astype(float), valley_prices)

        if slope_up is None or slope_down is None:
            return result

        if self.long_only:
            if not (slope_down > slope_up and slope_up <= 0.05 and slope_down > 0):
                return result
        else:
            if not (slope_down > slope_up and slope_up <= 0.05 and slope_down >= -0.05):
                return result

        start_idx = int(min(recent_peaks[0], recent_valleys[0]))
        end_idx   = int(max(recent_peaks[-1], recent_valleys[-1]))
        last_idx  = len(df) - 1

        resist_start  = slope_up   * start_idx + intercept_up
        support_start = slope_down * start_idx + intercept_down
        dist_start    = resist_start - support_start

        resist_end  = slope_up   * last_idx + intercept_up
        support_end = slope_down * last_idx + intercept_down
        dist_end    = resist_end - support_end

        if dist_start <= 0:
            return result

        convergence_ratio = dist_end / dist_start

        if convergence_ratio >= self.convergence_threshold or convergence_ratio < 0:
            return result

        bar_span = end_idx - start_idx
        if bar_span < self.min_bars:
            return result

        current_price = float(df[close_col].iloc[-1])
        is_breakout = False
        if current_price > resist_end:
            vol_today = float(df[vol_col].iloc[-1])
            vol_5d    = df[vol_col].tail(6).head(5).mean()
            if vol_today > vol_5d * 1.5:
                is_breakout = True
            else:
                return result
        elif current_price < support_end:
            return result

        result.is_converging     = True
        result.is_breakout       = is_breakout
        result.slope_up          = round(slope_up, 6)
        result.slope_down        = round(slope_down, 6)
        result.convergence_ratio = round(convergence_ratio, 4)
        result.bar_span          = bar_span
        result.current_price     = round(current_price, 2)
        result.resistance_price  = round(resist_end, 2)
        result.support_price     = round(support_end, 2)
        result.pattern_score     = self._calculate_pattern_score(result, df)
        result.breakout_score    = self._calculate_breakout_score(result, df)

        return result

    def _calculate_pattern_score(self, result: TriangleResult, df: pd.DataFrame) -> float:
        """量化型態品質 (0-100)"""
        balance = abs(result.slope_up + result.slope_down)
        balance_score = max(0, 40 - (balance * 100))

        conv_score = max(0, 40 * (1 - result.convergence_ratio))

        try:
            high_low   = df['High'] - df['Low']
            recent_atr = high_low.tail(5).mean()
            avg_atr    = high_low.tail(20).mean()
            atr_ratio  = recent_atr / avg_atr if avg_atr > 0 else 1.0
            atr_score  = max(0, 20 * (1.2 - atr_ratio))
        except Exception:
            atr_score = 10

        return float(min(100, balance_score + conv_score + atr_score))

    def _calculate_breakout_score(self, result: TriangleResult, df: pd.DataFrame) -> float:
        """量化突破品質 (0-100)"""
        if not result.is_breakout:
            return 0.0

        try:
            last_vol  = float(df['Volume'].values.flatten()[-1])
            ma20_vol  = float(df['Volume'].rolling(20).mean().values.flatten()[-1])
            vol_ratio = last_vol / ma20_vol if ma20_vol > 0 else 1.0
            vol_score = min(60, (vol_ratio / 2) * 60)
        except Exception:
            vol_score = 30

        try:
            last_row  = df.iloc[-1]
            h, l, c   = last_row['High'], last_row['Low'], last_row['Close']
            range_pos = (c - l) / (h - l) if (h - l) > 0 else 0.5
            pos_score = range_pos * 40
        except Exception:
            pos_score = 20

        return float(min(100, vol_score + pos_score))
