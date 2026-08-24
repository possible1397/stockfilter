"""
utils/regime.py — 市場環境評分的單一共用實作

先前 MarketRegimeDetector、BacktestEngine、precompute_backtest 各自複製了一份
相同的三維評分邏輯 (MA位置 40 + MA20斜率 30 + 短期動能 30)，
任何一處修改都會造成回測與實盤的評分漂移。統一收斂到此模組。
"""
import pandas as pd


def regime_score_from_closes(closes: pd.Series) -> tuple[float, dict]:
    """
    三維市場評分 (0-100)：MA位置 (40) + MA20斜率 (30) + 5日動能 (30)。
    closes: 收盤價序列 (需已裁切至目標日，最後一筆即評分基準日)。
    回傳 (總分, 明細 dict)。資料不足 60 筆時回傳 (50.0, {}) 中性分。
    """
    if closes is None or len(closes) < 60:
        return 50.0, {}

    close = float(closes.iloc[-1])
    ma20_all = closes.rolling(window=20).mean()
    ma60_all = closes.rolling(window=60).mean()
    ma20 = float(ma20_all.iloc[-1])
    ma60 = float(ma60_all.iloc[-1])

    # 維度 1: MA 位置分 (0~40)
    p20 = max(0.0, min(20.0, 10.0 + (close - ma20) / ma20 * 200.0))
    p60 = max(0.0, min(20.0, 10.0 + (close - ma60) / ma60 * 200.0))

    # 維度 2: MA20 斜率分 (0~30)
    ma20_5d_ago = float(ma20_all.iloc[-6])
    slope = max(0.0, min(30.0, 15.0 + (ma20 - ma20_5d_ago) / ma20_5d_ago * 1000.0))

    # 維度 3: 短期動能分 (0~30)
    ret_5d = (close - float(closes.iloc[-6])) / float(closes.iloc[-6]) * 100.0
    momentum = max(0.0, min(30.0, 15.0 + ret_5d * 3.0))

    detail = {
        "position": round(p20 + p60, 1),
        "slope": round(slope, 1),
        "momentum": round(momentum, 1),
        "close": round(close, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
    }
    return float(p20 + p60 + slope + momentum), detail


def regime_score_at(df: pd.DataFrame, target_ts: pd.Timestamp = None) -> float:
    """
    計算指定日期的市場評分。
    df: 指數 OHLCV (yfinance 格式)；target_ts: 目標日 (None = 最新)。
    無資料回傳 0.0；資料不足 60 筆回傳 50.0 (中性)。
    """
    if df is None or df.empty:
        return 0.0
    if target_ts is not None:
        df = df[df.index <= target_ts]
    if df.empty:
        return 0.0

    closes = df['Close'].squeeze()
    if isinstance(closes, pd.DataFrame):
        closes = closes.iloc[:, 0]

    score, _ = regime_score_from_closes(closes)
    return score
