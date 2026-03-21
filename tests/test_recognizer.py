"""tests/test_recognizer.py — PatternRecognizer 單元測試"""
import numpy as np
import pandas as pd
import pytest
from core.recognizer import PatternRecognizer, TriangleResult


def _make_triangle_df(bars: int = 120,
                      trend_up: bool = True,
                      volume_dryup: bool = True) -> pd.DataFrame:
    """
    建立一個具有三角收斂特徵的人工 K 線資料。
    - 上壓力線微下傾、下支撐線微上傾 → 對稱三角
    - 近期量縮 (volume_dryup=True)
    - 趨勢向上 (close > SMA50 > SMA200)
    """
    np.random.seed(42)
    t = np.arange(bars)

    # 基礎趨勢
    base = 100 + t * (0.3 if trend_up else -0.3)

    # 壓力線：微下傾振幅
    resist_amp = np.maximum(10 - t * 0.07, 1)
    # 支撐線：微上傾振幅
    support_amp = np.maximum(10 - t * 0.07, 1)

    high  = base + resist_amp + np.random.uniform(0, 0.5, bars)
    low   = base - support_amp - np.random.uniform(0, 0.5, bars)
    close = base + np.random.uniform(-0.5, 0.5, bars)
    open_ = close - np.random.uniform(-0.3, 0.3, bars)

    # 量縮設定
    if volume_dryup:
        volume = np.where(t < bars - 20,
                          np.random.randint(800_000, 1_200_000, bars),
                          np.random.randint(200_000, 400_000, bars))
    else:
        volume = np.random.randint(800_000, 1_500_000, bars)

    dates = pd.date_range("2023-01-01", periods=bars, freq="B")
    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low,
        "Close": close, "Volume": volume.astype(int),
    }, index=dates)


class TestPatternRecognizer:
    def setup_method(self):
        self.rec = PatternRecognizer(
            order=3, min_points=3, min_bars=15,
            convergence_threshold=0.8,  # 放寬門檻以利測試
            trend_filter=False,         # 關閉趨勢濾網，使用合成資料
            volume_dryup=False,         # 關閉量縮濾網
        )

    def test_result_is_triangle_result(self):
        df = _make_triangle_df(bars=120)
        result = self.rec.analyze("TEST", df)
        assert isinstance(result, TriangleResult)
        assert result.ticker == "TEST"

    def test_insufficient_bars_returns_not_converging(self):
        df = _make_triangle_df(bars=10)
        result = self.rec.analyze("TEST", df)
        assert result.is_converging is False

    def test_empty_df_returns_not_converging(self):
        result = self.rec.analyze("TEST", pd.DataFrame())
        assert result.is_converging is False

    def test_none_df_returns_not_converging(self):
        result = self.rec.analyze("TEST", None)
        assert result.is_converging is False

    def test_convergence_ratio_in_range(self):
        df = _make_triangle_df(bars=120)
        result = self.rec.analyze("TEST", df)
        if result.is_converging:
            assert 0 < result.convergence_ratio < self.rec.convergence_threshold

    def test_bar_span_positive(self):
        df = _make_triangle_df(bars=120)
        result = self.rec.analyze("TEST", df)
        if result.is_converging:
            assert result.bar_span >= self.rec.min_bars

    def test_pattern_score_in_range(self):
        df = _make_triangle_df(bars=120)
        result = self.rec.analyze("TEST", df)
        if result.is_converging:
            assert 0 <= result.pattern_score <= 100

    def test_prices_are_positive(self):
        df = _make_triangle_df(bars=120)
        result = self.rec.analyze("TEST", df)
        if result.is_converging:
            assert result.current_price > 0
            assert result.resistance_price > 0
            assert result.support_price > 0
            assert result.resistance_price >= result.support_price
