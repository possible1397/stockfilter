"""
strategies/momentum/scanner.py
RS Breakout + Sector Resonance Scanner

每日掃描邏輯:
  1. 計算全市場 RS 百分位 (12 個月報酬率橫截面排名)
  2. 找出近 52 週高點的標的 (close >= 252日最高收盤 × high_pct)
  3. 族群共振確認 (同 Theme 有 ≥ min_resonance 檔同時符合條件)
  4. 回傳排序後的 MomentumSignal 列表
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from core.sector_rotation import THEME_SECTOR_MAP
from utils.logger import get_logger

logger = get_logger(__name__)

# ── 反查 theme → [short_codes] ────────────────────────────────────────────────
_THEME_TO_SHORT: dict[str, list[str]] = {}
for _sc, _themes in THEME_SECTOR_MAP.items():
    for _th in _themes:
        _THEME_TO_SHORT.setdefault(_th, []).append(_sc)


def _short(ticker: str) -> str:
    return ticker.split(".")[0]


@dataclass
class MomentumSignal:
    """單一動能訊號"""
    ticker: str
    rs_score: float              # RS 百分位 (0-100), 越高越強
    high_ratio: float            # close / 52w max close (1.0 = 新高)
    themes: list[str]            # 觸發共振的主題族群
    resonant_peers: list[str]    # 同族群同日符合條件的其他標的
    current_price: float
    stop_price: float            # 建議停損 (進場價 × 0.93)
    target_price: float          # 目標停利 (進場價 × 1.15)
    score: float = 0.0           # 綜合分 (供排序)
    is_new_high: bool = False    # 是否創 52 週新高 (high_ratio >= 1.0)


class MomentumScanner:
    """
    RS Breakout + Sector Resonance 動能掃描器

    Parameters
    ----------
    rs_threshold : float
        RS 百分位門檻，預設 80 (前 20%)
    high_pct : float
        收盤相對 52 週最高收盤的最低比率，預設 0.97 (近 3% 以內)
    min_resonance : int
        同族群至少需要幾檔同日符合條件，預設 2
    stop_loss_pct : float
        停損比率，預設 0.07 (7%)
    take_profit_pct : float
        目標停利比率，預設 0.15 (15%)
    lookback : int
        RS 和 52 週高點的計算天數，預設 252
    min_rs_bars : int
        計算 RS 所需的最少歷史 K 棒數，預設 200
    """

    def __init__(
        self,
        rs_threshold: float = 80.0,
        high_pct: float = 0.97,
        min_resonance: int = 2,
        stop_loss_pct: float = 0.07,
        take_profit_pct: float = 0.15,
        lookback: int = 252,
        min_rs_bars: int = 200,
    ):
        self.rs_threshold = rs_threshold
        self.high_pct = high_pct
        self.min_resonance = min_resonance
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.lookback = lookback
        self.min_rs_bars = min_rs_bars

    # ── 公開方法 ──────────────────────────────────────────────────────────────

    def scan(self, stock_data: dict, scan_date: Optional[str] = None) -> list[MomentumSignal]:
        """
        對 stock_data 執行一次掃描，回傳排序後的訊號列表。

        Parameters
        ----------
        stock_data : dict
            {ticker: DataFrame(index=DatetimeIndex, columns=[Open,High,Low,Close,Volume])}
        scan_date : str or None
            掃描日期 'YYYY-MM-DD'；None 表示使用各股最新可用日期
        """
        if not stock_data:
            return []

        ts: Optional[pd.Timestamp] = pd.Timestamp(scan_date) if scan_date else None

        # 建立收盤價寬表，裁切至 scan_date
        close_wide = self._build_close_wide(stock_data, ts)
        if close_wide.empty:
            return []

        # 計算 RS 百分位和 52 週高點比率
        rs_row, high_ratio_row = self._compute_metrics(close_wide)
        if rs_row is None:
            return []

        # 篩選: RS >= threshold AND high_ratio >= high_pct
        candidates = self._filter_candidates(rs_row, high_ratio_row)
        if not candidates:
            logger.info("[Momentum] 無符合 RS+高點條件的標的")
            return []

        cand_short = {_short(t) for t in candidates}

        # 族群共振篩選
        signals = []
        for ticker in candidates:
            sc = _short(ticker)
            themes = THEME_SECTOR_MAP.get(sc, [])
            if not themes:
                continue  # 不在 THEME_SECTOR_MAP → 跳過

            resonant_themes = []
            resonant_peers: set[str] = set()

            for theme in themes:
                peers_short = _THEME_TO_SHORT.get(theme, [])
                matching = [p for p in peers_short if p in cand_short and p != sc]
                if len(matching) >= self.min_resonance - 1:  # -1 因為自己也算一檔
                    resonant_themes.append(theme)
                    # 轉回完整 ticker (從 candidates 找)
                    for cand in candidates:
                        if _short(cand) in matching:
                            resonant_peers.add(cand)

            if not resonant_themes:
                continue  # 無共振主題 → 跳過

            # 取當日收盤價
            df = stock_data.get(ticker)
            if df is None or df.empty:
                continue
            df_up = df[df.index <= ts] if ts is not None else df
            if df_up.empty:
                continue
            current_price = float(df_up.iloc[-1]["Close"])

            rs_val = float(rs_row.get(ticker, 0))
            hr_val = float(high_ratio_row.get(ticker, 0))

            # 綜合分: RS 百分位 + 是否創新高加成
            new_high_bonus = 10.0 if hr_val >= 1.0 else 0.0
            score = rs_val + new_high_bonus

            signals.append(MomentumSignal(
                ticker=ticker,
                rs_score=round(rs_val, 1),
                high_ratio=round(hr_val, 4),
                themes=resonant_themes,
                resonant_peers=sorted(resonant_peers),
                current_price=current_price,
                stop_price=round(current_price * (1 - self.stop_loss_pct), 2),
                target_price=round(current_price * (1 + self.take_profit_pct), 2),
                score=round(score, 1),
                is_new_high=hr_val >= 1.0,
            ))

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals

    # ── 私有方法 ──────────────────────────────────────────────────────────────

    def _build_close_wide(
        self, stock_data: dict, ts: Optional[pd.Timestamp]
    ) -> pd.DataFrame:
        series = {}
        for ticker, df in stock_data.items():
            if df is None or df.empty:
                continue
            df_up = df[df.index <= ts] if ts is not None else df
            if len(df_up) < self.min_rs_bars:
                continue
            series[ticker] = df_up["Close"]
        if not series:
            return pd.DataFrame()
        return pd.DataFrame(series)

    def _compute_metrics(
        self, close_wide: pd.DataFrame
    ) -> tuple[Optional[pd.Series], Optional[pd.Series]]:
        if len(close_wide) < self.lookback:
            logger.warning(f"[Momentum] 歷史資料不足 {self.lookback} 日，無法計算 RS")
            return None, None

        # RS: lookback 日報酬率的橫截面百分位排名
        ret = close_wide.pct_change(self.lookback, fill_method=None)
        rs_full = ret.rank(axis=1, pct=True, na_option="keep") * 100
        rs_row = rs_full.iloc[-1]

        # 52 週最高收盤比率
        max_close = close_wide.rolling(self.lookback, min_periods=self.min_rs_bars).max()
        high_ratio_full = close_wide.div(max_close)
        high_ratio_row = high_ratio_full.iloc[-1]

        return rs_row, high_ratio_row

    def _filter_candidates(
        self, rs_row: pd.Series, high_ratio_row: pd.Series
    ) -> list[str]:
        candidates = []
        for ticker in rs_row.index:
            rs_val = rs_row.get(ticker, np.nan)
            hr_val = high_ratio_row.get(ticker, np.nan)
            if pd.isna(rs_val) or pd.isna(hr_val):
                continue
            if rs_val >= self.rs_threshold and hr_val >= self.high_pct:
                candidates.append(ticker)
        return candidates
