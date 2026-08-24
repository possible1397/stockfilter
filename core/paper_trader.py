"""
core/paper_trader.py
模擬交易核心引擎
"""
import pandas as pd
import numpy as np
from typing import List, Dict
from utils.logger import get_logger
from data.database import get_open_paper_trades, upsert_paper_trade

logger = get_logger(__name__)

class PaperTrader:
    def __init__(self, config: dict, db_path: str):
        self.config = config
        self.db_path = db_path
        
        bt = self.config.get("backtest", {})
        self.capital_per_trade = 100000.0  # 固定資金 10萬
        self.stop_loss_pct = float(bt.get("stop_loss", 0.10))
        self.atr_mult = float(bt.get("atr_stop_mult", 4.0))
        self.atr_activate_pct = float(bt.get("atr_activate_pct", 15.0)) / 100
        self.partial_threshold = float(bt.get("partial_profit_threshold", 0.15))
        self.partial_ratio = float(bt.get("partial_profit_ratio", 0.5))
        self.dynamic_atr = bool(bt.get("dynamic_atr_update", True))
        self.k_line_trail = bool(bt.get("k_line_trail", True))

    def _calc_stops(self, df: pd.DataFrame, entry_date: str, entry_price: float) -> tuple:
        """計算當日為止的硬停損、ATR追蹤、K線追蹤，回傳 (推薦停損價, 是否達標減碼)"""
        if df.empty:
            return 0.0, False
            
        entry_ts = pd.Timestamp(entry_date)
        mask_held = df.index >= entry_ts
        if not mask_held.any():
            return 0.0, False
            
        df_calc = df.copy()
        
        df_calc['PrevClose'] = df_calc['Close'].shift(1)
        df_calc['TR'] = np.maximum(
            df_calc['High'] - df_calc['Low'],
            np.maximum(
                (df_calc['High'] - df_calc['PrevClose']).abs(),
                (df_calc['Low'] - df_calc['PrevClose']).abs()
            )
        )
        df_calc['ATR'] = df_calc['TR'].rolling(window=14).mean()
        
        df_calc['ProfitPct'] = (df_calc['Close'] / entry_price) - 1
        df_calc['IsActive'] = df_calc['ProfitPct'] >= self.atr_activate_pct
        df_calc['ActiveFlag'] = False
        df_calc.loc[mask_held, 'ActiveFlag'] = df_calc.loc[mask_held, 'IsActive'].cummax().astype(bool)
        
        df_calc['PrevLow'] = df_calc['Low'].shift(1)
        df_calc['KlineStop'] = df_calc[['PrevLow', 'PrevClose']].min(axis=1)
        
        df_calc.loc[mask_held, 'HighestHeld'] = df_calc.loc[mask_held, 'High'].cummax()
        df_calc['CurrentATR'] = df_calc['ATR']
        df_calc['ATRStop'] = df_calc['HighestHeld'] - df_calc['CurrentATR'] * self.atr_mult
        
        active_df = df_calc[df_calc['ActiveFlag']]
        
        atr_stop = round(float(active_df['ATRStop'].max()), 2) if not active_df.empty else 0.0
        kline_stop = round(float(active_df['KlineStop'].max()), 2) if (self.k_line_trail and not active_df.empty) else 0.0
        hard_stop = round(entry_price * (1 - self.stop_loss_pct), 2)
        
        recommended_stop = max([hard_stop, atr_stop, kline_stop])
        
        # Check partial
        latest_profit = float(df_calc['ProfitPct'].iloc[-1])
        should_partial = (self.partial_threshold > 0 and latest_profit >= self.partial_threshold)
        
        return recommended_stop, should_partial

    def process_exits(self, today_date: str, stock_data: Dict[str, pd.DataFrame]) -> list:
        """處理所有未平倉單的停損停利"""
        open_trades = get_open_paper_trades(self.db_path)
        actions = []
        
        for trade in open_trades:
            ticker = trade['ticker']
            df = stock_data.get(ticker)
            if df is None or df.empty:
                continue
                
            today_ts = pd.Timestamp(today_date)
            df_up_to_today = df[df.index <= today_ts]
            if df_up_to_today.empty:
                continue
                
            today_bar = df_up_to_today.iloc[-1]
            if today_bar.name.strftime('%Y-%m-%d') != today_date:
                continue # No data for today
                
            entry_price = trade['buy_price']
            
            # Use data up to YESTERDAY to calculate stops for TODAY
            df_up_to_yesterday = df_up_to_today.iloc[:-1]
            stop_price, should_partial = self._calc_stops(df_up_to_yesterday, trade['buy_date'], entry_price)
            
            if stop_price == 0:
                stop_price = round(entry_price * (1 - self.stop_loss_pct), 2)
                
            trade['highest_price'] = max(trade.get('highest_price') or entry_price, float(today_bar['High']))
            
            exit_reason = None
            exit_price = 0.0
            
            if today_bar['Low'] <= stop_price:
                exit_price = stop_price if today_bar['Open'] > stop_price else float(today_bar['Open'])
                exit_reason = "stop_loss"
            elif should_partial and trade['remaining_ratio'] == 1.0:
                trade['partial_sell_date'] = today_date
                trade['partial_sell_price'] = float(today_bar['Open'])
                trade['remaining_ratio'] = round(1 - self.partial_ratio, 2)
                actions.append(f"[{ticker}] 達標 {self.partial_threshold*100}% 減碼一半 @ {today_bar['Open']:.2f}")
                upsert_paper_trade(trade, self.db_path)
                continue
                
            if exit_reason:
                trade['status'] = 'CLOSED'
                trade['sell_date'] = today_date
                trade['sell_price'] = exit_price
                trade['sell_reason'] = exit_reason
                trade['pnl_pct'] = round((exit_price / entry_price - 1) * 100, 2)
                actions.append(f"[{ticker}] 出場 ({exit_reason}) @ {exit_price:.2f} (損益: {trade['pnl_pct']}%)")
                upsert_paper_trade(trade, self.db_path)
            else:
                trade['stop_loss'] = stop_price
                upsert_paper_trade(trade, self.db_path)
                
        return actions

    def process_entries(self, today_date: str, yesterdays_signals: list, stock_data: Dict[str, pd.DataFrame]) -> list:
        """處理昨日訊號的進場"""
        actions = []
        for sig in yesterdays_signals:
            if not sig['is_breakout']:
                continue
                
            ticker = sig['ticker']
            df = stock_data.get(ticker)
            if df is None or df.empty:
                continue
                
            today_ts = pd.Timestamp(today_date)
            if today_ts not in df.index:
                continue
                
            today_bar = df.loc[today_ts]
            open_price = float(today_bar['Open'])
            
            shares = int(self.capital_per_trade / open_price)
            if shares <= 0:
                shares = 1
                
            new_trade = {
                'ticker': ticker,
                'status': 'OPEN',
                'buy_date': today_date,
                'buy_price': open_price,
                'buy_reason': f"Breakout score={sig['score']:.1f}",
                'shares': shares,
                'remaining_ratio': 1.0,
                'highest_price': float(today_bar['High']),
                'stop_loss': round(open_price * (1 - self.stop_loss_pct), 2)
            }
            upsert_paper_trade(new_trade, self.db_path)
            actions.append(f"[{ticker}] 模擬進場 @ {open_price:.2f} ({shares}股)")
            
        return actions
