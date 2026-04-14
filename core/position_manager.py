"""
持倉管理模組 (Position Manager)
功能:
  - 記錄使用者的實際持倉 (買進日期、成本、張數)
  - 每日監控持倉標的，計算動態 ATR 追蹤止損、減碼建議
  - 與 main.py 整合，在掃描報表最上方顯示持倉操作建議
"""
import json
import os
import pandas as pd
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List

from utils.logger import get_logger

logger = get_logger(__name__)

POSITIONS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "my_positions.json"
)


@dataclass
class Position:
    """單一持倉紀錄"""
    ticker: str
    entry_date: str          # YYYY-MM-DD
    entry_price: float       # 買進均價
    shares: int              # 持有張數 (原始)
    remaining_ratio: float = 1.0   # 剩餘比例 (1.0 = 全倉, 0.5 = 已減碼一半)
    partial_exit_date: str = ""    # 減碼日期
    partial_exit_price: float = 0  # 減碼價格
    note: str = ""                 # 備註


@dataclass
class PositionAdvice:
    """每日持倉監控的建議結果"""
    ticker: str
    entry_price: float
    current_price: float
    profit_pct: float            # 帳面損益 %
    remaining_ratio: float       # 目前剩餘比例
    # 止損相關
    hard_stop: float             # 硬停損價
    atr_trail_stop: float        # ATR 追蹤止損價
    kline_trail_stop: float      # K 線追蹤止損價
    recommended_stop: float      # 最終建議止損價 (取最高者)
    # 動作建議
    action: str                  # 操作建議文字
    should_partial_exit: bool    # 是否建議本次減碼
    highest_since_entry: float   # 持倉期間最高價


class PositionManager:
    """
    持倉管理器
    讀寫 my_positions.json，提供每日監控建議
    """

    def __init__(self, config: dict = None, positions_file: str = POSITIONS_FILE):
        self.positions_file = positions_file
        self.positions: List[Position] = []
        self.config = config or {}
        bt = self.config.get("backtest", {})
        self.stop_loss_pct = float(bt.get("stop_loss", 0.07))
        self.atr_mult = float(bt.get("atr_stop_mult", 3.0))
        self.atr_activate_pct = float(bt.get("atr_activate_pct", 5.0)) / 100
        self.partial_threshold = float(bt.get("partial_profit_threshold", 0.15))
        self.partial_ratio = float(bt.get("partial_profit_ratio", 0.5))
        self.dynamic_atr = bool(bt.get("dynamic_atr_update", True))
        self.k_line_trail = bool(bt.get("k_line_trail", True))
        self._load()

    def _load(self):
        """從 JSON 讀取持倉"""
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.positions = [Position(**p) for p in data]
                logger.info(f"[PositionManager] 載入 {len(self.positions)} 筆持倉")
            except Exception as e:
                logger.error(f"[PositionManager] 讀取持倉失敗: {e}")
                self.positions = []
        else:
            self.positions = []

    def _save(self):
        """寫入 JSON"""
        data = []
        for p in self.positions:
            data.append({
                "ticker": p.ticker,
                "entry_date": p.entry_date,
                "entry_price": p.entry_price,
                "shares": p.shares,
                "remaining_ratio": p.remaining_ratio,
                "partial_exit_date": p.partial_exit_date,
                "partial_exit_price": p.partial_exit_price,
                "note": p.note,
            })
        with open(self.positions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_position(self, ticker: str, entry_date: str,
                     entry_price: float, shares: int, note: str = ""):
        """新增持倉"""
        pos = Position(
            ticker=ticker,
            entry_date=entry_date,
            entry_price=entry_price,
            shares=shares,
            note=note,
        )
        self.positions.append(pos)
        self._save()
        logger.info(f"[PositionManager] 新增持倉: {ticker} {shares}張 @ {entry_price}")

    def remove_position(self, ticker: str):
        """移除持倉 (全數出場)"""
        self.positions = [p for p in self.positions if p.ticker != ticker]
        self._save()
        logger.info(f"[PositionManager] 移除持倉: {ticker}")

    def record_partial_exit(self, ticker: str, exit_price: float, exit_date: str = ""):
        """記錄已執行減碼"""
        for p in self.positions:
            if p.ticker == ticker:
                p.remaining_ratio = round(p.remaining_ratio * (1 - self.partial_ratio), 2)
                p.partial_exit_date = exit_date or datetime.now().strftime("%Y-%m-%d")
                p.partial_exit_price = exit_price
                self._save()
                logger.info(f"[PositionManager] {ticker} 減碼完成, 剩餘 {p.remaining_ratio*100:.0f}%")
                return
        logger.warning(f"[PositionManager] 找不到 {ticker} 持倉")

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        """計算 ATR"""
        if len(df) < period:
            return float(df['High'].iloc[-1] - df['Low'].iloc[-1])
        high = df['High'].values[-period:]
        low = df['Low'].values[-period:]
        close = df['Close'].values[-period:]
        prev_close = np.roll(close, 1)
        tr = np.maximum(high - low,
                        np.maximum(np.abs(high - prev_close),
                                   np.abs(low - prev_close)))
        return float(np.mean(tr[1:]))

    def monitor(self, stock_data: dict) -> List[PositionAdvice]:
        """
        針對所有持倉，根據最新行情計算操作建議
        stock_data: {ticker: DataFrame}
        """
        advices = []
        for pos in self.positions:
            df = stock_data.get(pos.ticker)
            if df is None or df.empty:
                continue

            current_price = float(df['Close'].iloc[-1])
            profit_pct = (current_price - pos.entry_price) / pos.entry_price

            # --- 1. 硬停損 ---
            hard_stop = round(pos.entry_price * (1 - self.stop_loss_pct), 2)

            # --- 2. ATR 追蹤止損 ---
            if self.dynamic_atr:
                atr = self._calculate_atr(df)
            else:
                # 靜態：用進場日附近的 ATR
                entry_ts = pd.Timestamp(pos.entry_date)
                df_entry = df[df.index <= entry_ts]
                atr = self._calculate_atr(df_entry) if len(df_entry) >= 14 else self._calculate_atr(df)

            # 找持倉期間最高價
            entry_ts = pd.Timestamp(pos.entry_date)
            df_held = df[df.index >= entry_ts]
            highest = float(df_held['High'].max()) if not df_held.empty else current_price

            if profit_pct >= self.atr_activate_pct:
                atr_trail_stop = round(highest - atr * self.atr_mult, 2)
            else:
                atr_trail_stop = 0  # 尚未啟動

            # --- 3. K 線追蹤止損 ---
            kline_trail_stop = 0.0
            if self.k_line_trail and profit_pct >= self.atr_activate_pct and len(df) >= 2:
                prev_low = float(df['Low'].iloc[-2])
                prev_close = float(df['Close'].iloc[-2])
                kline_trail_stop = round(min(prev_low, prev_close), 2)

            # --- 最終建議止損：取三者最高 ---
            active_stops = [hard_stop]
            if atr_trail_stop > 0:
                active_stops.append(atr_trail_stop)
            if kline_trail_stop > 0:
                active_stops.append(kline_trail_stop)
            recommended_stop = max(active_stops)

            # --- 4. 減碼判斷 ---
            should_partial = False
            if (self.partial_threshold > 0
                    and profit_pct >= self.partial_threshold
                    and pos.remaining_ratio >= 1.0):
                should_partial = True

            # --- 5. 組合建議文字 ---
            action_parts = []
            if should_partial:
                exit_shares = int(pos.shares * self.partial_ratio)
                action_parts.append(
                    f"[!] 獲利 {profit_pct*100:.1f}% 已達減碼門檻 "
                    f"→ 建議明日開盤賣出 {exit_shares} 張 (減碼 {self.partial_ratio*100:.0f}%)"
                )
            if pos.remaining_ratio < 1.0:
                action_parts.append(
                    f"[v] 已減碼至 {pos.remaining_ratio*100:.0f}%"
                )

            stop_label = "動態ATR" if atr_trail_stop >= kline_trail_stop else "前日K線"
            action_parts.append(
                f"[S] 追蹤停損: {recommended_stop} ({stop_label}) "
                f"| 硬停損: {hard_stop}"
            )

            if current_price < recommended_stop:
                action_parts.insert(0, f"[!!] 已跌破追蹤停損 {recommended_stop}，建議立即出場！")

            advice = PositionAdvice(
                ticker=pos.ticker,
                entry_price=pos.entry_price,
                current_price=current_price,
                profit_pct=round(profit_pct * 100, 2),
                remaining_ratio=pos.remaining_ratio,
                hard_stop=hard_stop,
                atr_trail_stop=atr_trail_stop,
                kline_trail_stop=kline_trail_stop,
                recommended_stop=recommended_stop,
                action=" | ".join(action_parts),
                should_partial_exit=should_partial,
                highest_since_entry=highest,
            )
            advices.append(advice)

        return advices

    def get_tickers(self) -> list:
        """回傳所有持倉的股票代號"""
        return [p.ticker for p in self.positions]
