"""tests/test_market_regime.py — MarketRegimeDetector 單元測試 (mock yfinance)"""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock
from core.market_regime import MarketRegimeDetector


def _make_index_df(bars: int = 130, trend: str = "bull") -> pd.DataFrame:
    """建立模擬指數 DataFrame"""
    t = np.arange(bars)
    if trend == "bull":
        close = 15000 + t * 20 + np.random.randn(bars) * 50
    elif trend == "bear":
        close = 20000 - t * 30 + np.random.randn(bars) * 50
    else:
        close = np.ones(bars) * 17000 + np.random.randn(bars) * 100

    dates = pd.date_range("2023-01-01", periods=bars, freq="B")
    df = pd.DataFrame({"Close": close}, index=dates)
    # yfinance 回傳 MultiIndex columns，這裡簡化為 single-level
    return df


class TestMarketRegimeDetector:
    def test_get_market_score_returns_float(self):
        """測試 get_market_score 回傳浮點數"""
        detector = MarketRegimeDetector()
        bull_tse = _make_index_df(trend="bull")
        bull_otc = _make_index_df(trend="bull")

        with patch("yfinance.download") as mock_dl:
            mock_dl.side_effect = [bull_tse, bull_otc, bull_tse, bull_otc]
            detector._cache = None  # 清除快取
            score = detector.get_market_score()
        assert isinstance(score, float)
        assert 0 <= score <= 100

    def test_bull_market_high_score(self):
        """多頭市場分數應 > 50"""
        detector = MarketRegimeDetector()
        bull = _make_index_df(bars=130, trend="bull")

        with patch("yfinance.download", return_value=bull):
            detector._cache = None
            score = detector.get_market_score()
        assert score > 50

    def test_bear_market_low_score(self):
        """空頭市場分數應 < 50"""
        detector = MarketRegimeDetector()
        bear = _make_index_df(bars=130, trend="bear")

        with patch("yfinance.download", return_value=bear):
            detector._cache = None
            score = detector.get_market_score()
        assert score < 50

    def test_ttl_cache_avoids_second_download(self):
        """同一小時內的第二次呼叫應使用快取，不重新下載"""
        detector = MarketRegimeDetector()
        bull = _make_index_df(trend="bull")

        with patch("yfinance.download", return_value=bull) as mock_dl:
            detector._cache = None
            detector.get_market_status()
            detector.get_market_status()  # 第二次應命中快取
            assert mock_dl.call_count == 2  # 只下載 2 次 (TSE + OTC)，不是 4 次

    def test_empty_response_handled(self):
        """yfinance 回傳空 DataFrame 應回傳分數 0"""
        detector = MarketRegimeDetector()
        with patch("yfinance.download", return_value=pd.DataFrame()):
            detector._cache = None
            status = detector.get_market_status()
        assert status['TSE']['score'] == 0
        assert status['OTC']['score'] == 0

    def test_is_safe_to_trade_threshold(self):
        """測試 is_safe_to_trade 門檻判斷 (直接注入快取)"""
        from datetime import datetime as _dt
        detector = MarketRegimeDetector()
        # 注入快取，讓 get_market_status() 直接命中快取
        current_hour = _dt.now().hour
        detector._cache = {"TSE": {"score": 60}, "OTC": {"score": 60}}
        detector._cache_hour = current_hour

        assert detector.is_safe_to_trade(threshold=50) is True
        assert detector.is_safe_to_trade(threshold=70) is False
