"""
回測引擎 (Backtest Engine)
功能:
  - 讀取 SQLite 歷史資料，模擬逐日掃描
  - 進場: total_score >= threshold + 收斂/突破確認
  - 出場: 持有天數達上限 / 跌破突破K低點 / ATR移動停利
  - 含手續費 0.1425% + 交易稅 0.3% + 估計滑價 0.1%
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

from core.recognizer import PatternRecognizer
from core.market_regime import MarketRegimeDetector
from backtest.metrics import calculate_metrics, BacktestMetrics


@dataclass
class BacktestConfig:
    """回測參數配置"""
    # 時間範圍
    start_date: str = "2025-01-01"
    end_date: str = "2026-03-20"
    
    # PatternRecognizer 參數
    order: int = 5
    min_points: int = 3
    min_bars: int = 20
    convergence_threshold: float = 0.3

    # 交易參數
    max_hold_days: int = 10           # 最大持有天數
    score_threshold: float = 40.0     # 最低進場總分
    
    # 風控參數
    stop_loss_pct: float = -5.0       # 固定停損 (%)
    take_profit_pct: float = 15.0     # 固定停利 (%)
    atr_stop_mult: float = 2.0        # ATR 停損倍數 (0 = 關閉)
    
    # 交易成本
    commission_rate: float = 0.001425  # 手續費 (買賣各一次)
    tax_rate: float = 0.003            # 交易稅 (賣出)
    slippage_rate: float = 0.001       # 滑價估計

    # 倉位
    max_positions: int = 5             # 同時最多持有檔數
    position_size_pct: float = 20.0    # 每筆佔總資金比例 (%)

    # 大盤濾網
    use_market_filter: bool = True
    market_threshold: float = 40.0


@dataclass
class Trade:
    """單筆交易記錄"""
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float       # 扣除交易成本的淨報酬率
    hold_days: int
    exit_reason: str        # 'max_hold', 'stop_loss', 'take_profit', 'atr_stop'
    entry_score: float = 0
    sector: str = ""
    grade: str = ""


class BacktestEngine:
    """
    回測引擎
    逐日模擬策略執行，記錄交易結果
    """

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        self.trades: list[Trade] = []
        self.active_positions: dict = {}  # {ticker: position_info}

    def _calculate_transaction_cost(self, is_buy: bool) -> float:
        """計算交易成本比率"""
        cost = self.config.commission_rate + self.config.slippage_rate
        if not is_buy:
            cost += self.config.tax_rate
        return cost

    def _check_exit(self, ticker: str, pos: dict, current_bar: pd.Series,
                    day_count: int) -> Optional[tuple]:
        """
        檢查是否觸發出場條件
        回傳: (exit_price, exit_reason) 或 None
        """
        close = float(current_bar['Close'])
        high = float(current_bar['High'])
        low = float(current_bar['Low'])
        entry_price = pos['entry_price']

        current_return = (close - entry_price) / entry_price * 100

        # 1. 固定停損
        if current_return <= self.config.stop_loss_pct:
            return (close, 'stop_loss')

        # 2. 固定停利
        if current_return >= self.config.take_profit_pct:
            return (close, 'take_profit')

        # 3. ATR 停損 (動態)
        if self.config.atr_stop_mult > 0 and 'atr' in pos:
            atr_stop = pos.get('trailing_stop', entry_price - pos['atr'] * self.config.atr_stop_mult)
            # 移動停利: 若價格創新高，上移停損線
            if close > pos.get('highest', entry_price):
                pos['highest'] = close
                atr_stop = close - pos['atr'] * self.config.atr_stop_mult
                pos['trailing_stop'] = atr_stop
            if low <= atr_stop:
                return (atr_stop, 'atr_stop')

        # 4. 最大持有天數
        if day_count >= self.config.max_hold_days:
            return (close, 'max_hold')

        return None

    def run(self, stock_data: dict, scan_results_by_date: dict = None) -> list[Trade]:
        """
        執行回測
        
        stock_data: {ticker: DataFrame} — 完整歷史資料
        scan_results_by_date: {date_str: [TriangleResult, ...]} — 預計算的掃描結果
                              若為 None，則引擎自行逐日掃描 (較慢)
        
        回傳: 交易記錄列表
        """
        self.trades = []
        self.active_positions = {}

        # 如果沒有提供預計算結果，自行建立簡化版掃描
        if scan_results_by_date is None:
            scan_results_by_date = self._run_daily_scan(stock_data)

        # 建立日期軸
        all_dates = sorted(scan_results_by_date.keys())

        print(f"[Backtest] 回測期間: {all_dates[0]} ~ {all_dates[-1]} "
              f"({len(all_dates)} 個交易日)")

        for date_str in all_dates:
            # 1. 檢查現有持倉的出場條件
            closed_tickers = []
            for ticker, pos in self.active_positions.items():
                df = stock_data.get(ticker)
                if df is None or df.empty:
                    continue

                date_mask = df.index <= pd.Timestamp(date_str)
                df_up_to = df[date_mask]
                if df_up_to.empty:
                    continue

                current_bar = df_up_to.iloc[-1]
                pos['hold_days'] += 1

                exit_result = self._check_exit(
                    ticker, pos, current_bar, pos['hold_days'])

                if exit_result:
                    exit_price, exit_reason = exit_result
                    # 計算淨報酬
                    gross_return = (exit_price - pos['entry_price']) / pos['entry_price']
                    buy_cost = self._calculate_transaction_cost(is_buy=True)
                    sell_cost = self._calculate_transaction_cost(is_buy=False)
                    net_return = (gross_return - buy_cost - sell_cost) * 100

                    trade = Trade(
                        ticker=ticker,
                        entry_date=pos['entry_date'],
                        exit_date=date_str,
                        entry_price=pos['entry_price'],
                        exit_price=exit_price,
                        return_pct=round(net_return, 2),
                        hold_days=pos['hold_days'],
                        exit_reason=exit_reason,
                        entry_score=pos.get('score', 0),
                        sector=pos.get('sector', ''),
                        grade=pos.get('grade', ''),
                    )
                    self.trades.append(trade)
                    closed_tickers.append(ticker)

            for t in closed_tickers:
                del self.active_positions[t]

            # 2. 檢查新進場機會
            if len(self.active_positions) >= self.config.max_positions:
                continue

            day_results = scan_results_by_date.get(date_str, [])
            for signal in day_results:
                if len(self.active_positions) >= self.config.max_positions:
                    break
                
                ticker = signal.get('ticker', '')
                if ticker in self.active_positions:
                    continue

                score = signal.get('score', 0)
                if score < self.config.score_threshold:
                    continue

                # 取得進場日的資料
                df = stock_data.get(ticker)
                if df is None or df.empty:
                    continue

                date_mask = df.index <= pd.Timestamp(date_str)
                df_up_to = df[date_mask]
                if df_up_to.empty:
                    continue

                entry_bar = df_up_to.iloc[-1]
                entry_price = float(entry_bar['Close'])

                # 計算 ATR (用於動態停損)
                if len(df_up_to) >= 14:
                    highs = df_up_to['High'].values[-14:]
                    lows = df_up_to['Low'].values[-14:]
                    closes = df_up_to['Close'].values[-14:]
                    tr = np.maximum(highs - lows,
                                    np.maximum(np.abs(highs - np.roll(closes, 1)),
                                               np.abs(lows - np.roll(closes, 1))))
                    atr = float(np.mean(tr[1:]))  # 排除第一個 (roll artifact)
                else:
                    atr = entry_price * 0.03  # 預設 3%

                self.active_positions[ticker] = {
                    'entry_date': date_str,
                    'entry_price': entry_price,
                    'hold_days': 0,
                    'atr': atr,
                    'highest': entry_price,
                    'trailing_stop': entry_price - atr * self.config.atr_stop_mult,
                    'score': score,
                    'sector': signal.get('sector', ''),
                    'grade': signal.get('grade', ''),
                }

        # 強制平倉所有剩餘持倉
        for ticker, pos in self.active_positions.items():
            df = stock_data.get(ticker)
            if df is None or df.empty:
                continue
            last_bar = df.iloc[-1]
            exit_price = float(last_bar['Close'])
            gross_return = (exit_price - pos['entry_price']) / pos['entry_price']
            buy_cost = self._calculate_transaction_cost(is_buy=True)
            sell_cost = self._calculate_transaction_cost(is_buy=False)
            net_return = (gross_return - buy_cost - sell_cost) * 100

            self.trades.append(Trade(
                ticker=ticker,
                entry_date=pos['entry_date'],
                exit_date=str(df.index[-1].date()),
                entry_price=pos['entry_price'],
                exit_price=exit_price,
                return_pct=round(net_return, 2),
                hold_days=pos['hold_days'],
                exit_reason='end_of_backtest',
                entry_score=pos.get('score', 0),
            ))
        self.active_positions = {}

        print(f"[Backtest] 完成！共 {len(self.trades)} 筆交易")
        return self.trades

    def _run_daily_scan(self, stock_data: dict) -> dict:
        """
        簡化版逐日掃描 (用於沒有預計算結果時)
        注意: 這是簡化版，實際使用建議預計算
        """
        recognizer = PatternRecognizer(
            order=self.config.order,
            min_points=self.config.min_points,
            min_bars=self.config.min_bars,
            convergence_threshold=self.config.convergence_threshold,
        )

        # 收集所有交易日
        all_dates = set()
        for df in stock_data.values():
            if not df.empty:
                for d in df.index:
                    date_str = str(d.date())
                    if self.config.start_date <= date_str <= self.config.end_date:
                        all_dates.add(date_str)

        all_dates = sorted(all_dates)
        results_by_date = {}

        print(f"[Backtest] 逐日掃描中 ({len(all_dates)} 日 × {len(stock_data)} 檔)...")
        
        # 每隔 N 天掃描一次 (節省計算量)
        scan_interval = 5  # 每 5 個交易日掃描一次
        
        for i, date_str in enumerate(all_dates):
            if i % scan_interval != 0:
                continue

            day_signals = []
            for ticker, df in stock_data.items():
                if df.empty:
                    continue

                # 截取到當天為止的資料
                date_mask = df.index <= pd.Timestamp(date_str)
                df_up_to = df[date_mask]
                if len(df_up_to) < self.config.min_bars + self.config.order * 2:
                    continue

                result = recognizer.analyze(ticker, df_up_to)
                if result.is_converging:
                    day_signals.append({
                        'ticker': ticker,
                        'score': result.pattern_score + result.breakout_score,
                        'is_breakout': result.is_breakout,
                        'sector': '',
                        'grade': 'C',
                    })

            if day_signals:
                # 按分數排序
                day_signals.sort(key=lambda x: x['score'], reverse=True)
                results_by_date[date_str] = day_signals

            if (i + 1) % 50 == 0:
                print(f"  掃描進度: {i+1}/{len(all_dates)}")

        return results_by_date

    def get_metrics(self) -> BacktestMetrics:
        """取得回測績效指標"""
        trade_dicts = [
            {
                'entry_date': t.entry_date,
                'exit_date': t.exit_date,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'return_pct': t.return_pct,
                'hold_days': t.hold_days,
            }
            for t in self.trades
        ]
        return calculate_metrics(trade_dicts)


if __name__ == "__main__":
    import data.database as db_module
    from data.cleaner import DataCleaner

    print("=== 回測引擎測試 ===")
    db_module.init_db()

    # 載入快取中的所有股票資料
    config = BacktestConfig(
        start_date="2025-06-01",
        end_date="2026-03-15",
        max_hold_days=10,
        score_threshold=30,
    )
    engine = BacktestEngine(config)

    # 用樣本測試
    sample = ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW"]
    stock_data = {}
    for t in sample:
        df = db_module.load_dataframe(t)
        if not df.empty:
            stock_data[t] = df

    if stock_data:
        trades = engine.run(stock_data)
        metrics = engine.get_metrics()
        from backtest.metrics import metrics_to_dict
        for k, v in metrics_to_dict(metrics).items():
            print(f"  {k}: {v}")
    else:
        print("  快取中無資料，請先執行 main.py 下載")
