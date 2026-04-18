"""
共用技術指標工具函式
"""
import numpy as np
import pandas as pd


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """計算 ATR (Average True Range)，與回測引擎邏輯一致。

    df 需包含 'High', 'Low', 'Close' 欄位 (大小寫均可)。
    若資料不足則回傳收盤價的 3% 作為估計值。
    """
    if df is None or df.empty:
        return 0.0

    high_col  = 'High'  if 'High'  in df.columns else 'high'
    low_col   = 'Low'   if 'Low'   in df.columns else 'low'
    close_col = 'Close' if 'Close' in df.columns else 'close'

    if len(df) < period + 1:
        close = float(df[close_col].iloc[-1]) if not df.empty else 0.0
        return close * 0.03

    highs      = df[high_col].values[-period:]
    lows       = df[low_col].values[-period:]
    closes     = df[close_col].values[-period:]
    prev_close = np.roll(closes, 1)
    tr = np.maximum(
        highs - lows,
        np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close))
    )
    return float(np.mean(tr[1:]))
