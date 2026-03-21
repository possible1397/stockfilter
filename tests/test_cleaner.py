"""tests/test_cleaner.py — DataCleaner 單元測試"""
import pandas as pd
import numpy as np
import pytest
from data.cleaner import DataCleaner


def _make_df(bars: int = 100, avg_vol: int = 1_000_000) -> pd.DataFrame:
    """建立假 K 線資料"""
    dates = pd.date_range("2024-01-01", periods=bars, freq="B")
    return pd.DataFrame({
        "Open":   np.ones(bars) * 100,
        "High":   np.ones(bars) * 105,
        "Low":    np.ones(bars) * 95,
        "Close":  np.ones(bars) * 100,
        "Volume": np.ones(bars, dtype=int) * avg_vol,
    }, index=dates)


class TestDataCleaner:
    def setup_method(self):
        self.cleaner = DataCleaner(min_vol_lots=500, min_bars=60)

    def test_valid_stock_passes(self):
        df = _make_df(bars=100, avg_vol=1_000_000)  # 1000 張，通過
        assert self.cleaner.is_valid("TEST", df) is True

    def test_empty_df_fails(self):
        assert self.cleaner.is_valid("TEST", pd.DataFrame()) is False

    def test_none_df_fails(self):
        assert self.cleaner.is_valid("TEST", None) is False

    def test_insufficient_bars_fails(self):
        df = _make_df(bars=30, avg_vol=1_000_000)  # 30 < 60
        assert self.cleaner.is_valid("TEST", df) is False

    def test_low_volume_fails(self):
        df = _make_df(bars=100, avg_vol=100_000)  # 100 張 < 500
        assert self.cleaner.is_valid("TEST", df) is False

    def test_filter_batch_removes_invalid(self):
        good = _make_df(bars=100, avg_vol=1_000_000)
        bad  = _make_df(bars=10,  avg_vol=1_000_000)
        result = self.cleaner.filter_batch({"GOOD": good, "BAD": bad})
        assert "GOOD" in result
        assert "BAD" not in result

    def test_filter_batch_empty_input(self):
        result = self.cleaner.filter_batch({})
        assert result == {}

    def test_custom_thresholds(self):
        cleaner = DataCleaner(min_vol_lots=100, min_bars=20)
        df = _make_df(bars=25, avg_vol=200_000)  # 200 張 > 100，25 > 20
        assert cleaner.is_valid("TEST", df) is True
