import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class TriangleResult:
    """儲存三角收斂形態的分析結果"""
    ticker: str
    is_converging: bool = False
    slope_up: float = 0.0          # 壓力線斜率 (應 < 0)
    slope_down: float = 0.0        # 支撐線斜率 (應 > 0)
    convergence_ratio: float = 1.0  # 收斂程度 (越小越收斂，< 0.3 才符合)
    bar_span: int = 0              # 三角形橫跨的 K 線數量 (應 > 20)
    current_price: float = 0.0
    resistance_price: float = 0.0  # 壓力線在最後一根 K 線的價格
    support_price: float = 0.0     # 支撐線在最後一根 K 線的價格
    is_breakout: bool = False      # 是否已經突破壓力線 (真突破)



class PatternRecognizer:
    """
    三角收斂形態識別器
    基於 scipy.signal.argrelextrema 找出局部高低點，
    並透過線性回歸判定壓力線/支撐線的斜率是否符合三角收斂幾何條件。
    """

    def __init__(self, order: int = 5, min_points: int = 3, 
                 min_bars: int = 20, convergence_threshold: float = 0.3,
                 long_only: bool = True, trend_filter: bool = True, volume_dryup: bool = True):
        """
        order: argrelextrema 的鄰近範圍參數，值越大，找出的高低點越少但更具代表性
        min_points: 至少需要幾個高點/低點才能畫趨勢線
        min_bars: 三角收斂區間最少需橫跨幾根 K 線
        convergence_threshold: 收斂程度門檻，尾端距離 < 初始距離的此比例才符合
        long_only: 是否只做多 (排除下降三角形)
        trend_filter: 是否要求大趨勢偏多 (收盤 > 50MA > 200MA)
        volume_dryup: 是否要求尾端量縮 (近 5 日均量 < 近 50 日均量的 75%)
        """
        self.order = order
        self.min_points = min_points
        self.min_bars = min_bars
        self.convergence_threshold = convergence_threshold
        self.long_only = long_only
        self.trend_filter = trend_filter
        self.volume_dryup = volume_dryup

    def _find_pivot_points(self, df: pd.DataFrame):
        """
        找出局部高點 (pivot highs) 與局部低點 (pivot lows) 的索引與價格
        """
        high_col = 'High' if 'High' in df.columns else 'high'
        low_col  = 'Low'  if 'Low'  in df.columns else 'low'

        highs = df[high_col].values
        lows  = df[low_col].values

        # argrelextrema 回傳的是「在 numpy array 中的位置索引」
        peak_idx   = argrelextrema(highs, np.greater, order=self.order)[0]
        valley_idx = argrelextrema(lows,  np.less,    order=self.order)[0]

        return peak_idx, valley_idx

    def _linear_regression(self, x: np.ndarray, y: np.ndarray):
        """
        對給定的 x, y 做線性回歸，回傳 (斜率 slope, 截距 intercept)
        x: 時間序列的索引 (整數)
        y: 對應的價格
        """
        if len(x) < 2:
            return None, None
        coeffs = np.polyfit(x, y, 1)
        return coeffs[0], coeffs[1]  # slope, intercept

    def analyze(self, ticker: str, df: pd.DataFrame) -> TriangleResult:
        """
        執行三角收斂分析，回傳 TriangleResult 物件
        """
        result = TriangleResult(ticker=ticker)

        if df is None or len(df) < self.min_bars + self.order * 2:
            return result

        # 1. 找出極值點的「位置索引」(相對於整個 df 的 row index)
        high_col = 'High' if 'High' in df.columns else 'high'
        low_col  = 'Low'  if 'Low'  in df.columns else 'low'
        close_col = 'Close' if 'Close' in df.columns else 'close'
        vol_col  = 'Volume' if 'Volume' in df.columns else 'volume'

        # --- 實戰濾網 1: 大趨勢濾網 (Trend Filter) ---
        if self.trend_filter and len(df) >= 200:
            sma50 = df[close_col].rolling(window=50).mean().iloc[-1]
            sma200 = df[close_col].rolling(window=200).mean().iloc[-1]
            current_close = df[close_col].iloc[-1]
            # 做多必須滿足: 現在價格 > 季線 > 年線
            if not (current_close > sma50 and sma50 > sma200):
                return result

        # --- 實戰濾網 2: 籌碼量縮濾網 (Volume Dry-up / VCP) ---
        if self.volume_dryup and len(df) >= 50:
            vol_5d = df[vol_col].tail(5).mean()
            vol_50d = df[vol_col].tail(50).mean()
            # 收斂末端的 5 日均量必須小於 50 日均量的 75% (明顯量縮)
            if vol_5d > vol_50d * 0.75:
                return result

        peak_idx, valley_idx = self._find_pivot_points(df)

        # 只取最近的幾個高低點 (預設 5 個)
        recent_peaks   = peak_idx[-5:]   if len(peak_idx)   >= self.min_points else peak_idx
        recent_valleys = valley_idx[-5:] if len(valley_idx) >= self.min_points else valley_idx

        if len(recent_peaks) < self.min_points or len(recent_valleys) < self.min_points:
            return result  # 點位不足，無法畫線

        # 2. 取出對應的價格 (高點用 High，低點用 Low)
        peak_prices   = df[high_col].values[recent_peaks]
        valley_prices = df[low_col].values[recent_valleys]

        # 3. 線性回歸取得斜率
        slope_up,   intercept_up   = self._linear_regression(recent_peaks.astype(float),   peak_prices)
        slope_down, intercept_down = self._linear_regression(recent_valleys.astype(float), valley_prices)

        if slope_up is None or slope_down is None:
            return result

        # 4. 判斷斜率條件: 收斂成三角形
        # 原本只接受對稱三角 (slope_up < 0 且 slope_down > 0)。
        if self.long_only:
            # 只做多：接受「對稱三角」與「上升三角」，『排除下降三角』
            # 支撐線必須向上墊高 (slope_down > 0)
            # 壓力線可以向下或幾乎持平 (slope_up <= 0.05)
            if not (slope_down > slope_up and slope_up <= 0.05 and slope_down > 0):
                return result
        else:
            # 原始寬鬆條件 (含下降三角形)
            if not (slope_down > slope_up and slope_up <= 0.05 and slope_down >= -0.05):
                return result

        # 5. 計算收斂程度
        # 取高低點的最大跨度作為三角形的「起點」和「終點」
        start_idx = int(min(recent_peaks[0], recent_valleys[0]))
        end_idx   = int(max(recent_peaks[-1], recent_valleys[-1]))
        last_idx  = len(df) - 1  # 最後一根 K 線的索引

        # 在起始 idx 的兩線垂直距離
        resist_start = slope_up   * start_idx + intercept_up
        support_start = slope_down * start_idx + intercept_down
        dist_start = resist_start - support_start

        # 在最後一根 K 的兩線價格
        resist_end  = slope_up   * last_idx + intercept_up
        support_end = slope_down * last_idx + intercept_down
        dist_end    = resist_end - support_end

        if dist_start <= 0:
            return result  # 起始距離異常

        convergence_ratio = dist_end / dist_start

        # 6. 收斂程度條件: 尾端距離 < 初始距離的 threshold (預設 30%)
        if convergence_ratio >= self.convergence_threshold or convergence_ratio < 0:
            return result

        # 7. K 線橫跨長度 > min_bars (預設 20 根)
        bar_span = end_idx - start_idx
        if bar_span < self.min_bars:
            return result

        # 8. 當前收盤價與突破判斷
        current_price = float(df[close_col].iloc[-1])
        is_breakout = False
        if current_price > resist_end:
            # 已經向上突破壓力線
            # 檢查突破是否帶量 (今日成交量 > 5日均量 * 1.5)
            vol_today = float(df[vol_col].iloc[-1])
            vol_5d = df[vol_col].tail(6).head(5).mean() # 排除今天的過去5天
            if vol_today > vol_5d * 1.5:
                is_breakout = True
            else:
                return result # 假突破或無量突破，濾掉
        elif current_price < support_end:
            # 跌破支撐線，型態破壞
            return result
        # 如果還在區間內 (未突破)，也是合格的收斂排列

        # 所有條件通過，建立結果物件
        result.is_converging    = True
        result.is_breakout      = is_breakout
        result.slope_up         = round(slope_up, 6)
        result.slope_down       = round(slope_down, 6)
        result.convergence_ratio = round(convergence_ratio, 4)
        result.bar_span         = bar_span
        result.current_price    = round(current_price, 2)
        result.resistance_price = round(resist_end, 2)
        result.support_price    = round(support_end, 2)

        return result
