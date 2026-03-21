"""
Optuna 參數自動優化器 (Walk-Forward Optimization)
功能:
  - 使用 Optuna (TPE Bayesian sampler) 搜尋最佳參數組合
  - Walk-Forward 滾動視窗: 訓練 6 個月 / 驗證 2 個月
  - 目標函數: 最大化 Sharpe Ratio
  - 防止過擬合: 以 Out-of-Sample 結果為最終評估
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional

try:
    import optuna
    from optuna.samplers import TPESampler
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False
    print("[Optimizer] 請安裝 optuna: pip install optuna")

from backtest.engine import BacktestEngine, BacktestConfig
from backtest.metrics import calculate_metrics, metrics_to_dict
from backtest.report import generate_report


class WalkForwardOptimizer:
    """
    Walk-Forward 參數優化器
    
    流程:
    1. 將歷史資料切成多個 (訓練集, 驗證集) 視窗
    2. 在每個訓練集上用 Optuna 搜尋最佳參數
    3. 用該參數在驗證集上測試 (Out-of-Sample)
    4. 收集所有 OOS 結果作為最終績效評估
    """

    def __init__(self, stock_data: dict,
                 train_months: int = 6,
                 test_months: int = 2,
                 n_trials: int = 100):
        """
        stock_data: {ticker: DataFrame} — 完整歷史資料
        train_months: 訓練集長度 (月)
        test_months: 驗證集長度 (月)
        n_trials: 每個視窗的 Optuna 搜尋次數
        """
        self.stock_data = stock_data
        self.train_months = train_months
        self.test_months = test_months
        self.n_trials = n_trials

    def _get_date_range(self) -> tuple:
        """取得所有資料的最早/最晚日期"""
        min_date = None
        max_date = None
        for df in self.stock_data.values():
            if df.empty:
                continue
            if min_date is None or df.index[0] < min_date:
                min_date = df.index[0]
            if max_date is None or df.index[-1] > max_date:
                max_date = df.index[-1]
        return min_date, max_date

    def _create_windows(self) -> list:
        """
        建立 Walk-Forward 滾動視窗
        回傳: [(train_start, train_end, test_start, test_end), ...]
        """
        min_date, max_date = self._get_date_range()
        if min_date is None:
            return []

        windows = []
        cursor = min_date + timedelta(days=self.train_months * 30)

        while cursor + timedelta(days=self.test_months * 30) <= max_date:
            train_start = cursor - timedelta(days=self.train_months * 30)
            train_end = cursor
            test_start = cursor
            test_end = cursor + timedelta(days=self.test_months * 30)

            windows.append((
                train_start.strftime('%Y-%m-%d'),
                train_end.strftime('%Y-%m-%d'),
                test_start.strftime('%Y-%m-%d'),
                test_end.strftime('%Y-%m-%d'),
            ))
            cursor += timedelta(days=self.test_months * 30)

        return windows

    def _objective(self, trial, train_start: str, train_end: str) -> float:
        """Optuna 目標函數: 在訓練集上回測，回傳 Sharpe Ratio"""
        # 搜尋空間
        config = BacktestConfig(
            start_date=train_start,
            end_date=train_end,
            order=trial.suggest_int('order', 3, 8),
            min_points=trial.suggest_int('min_points', 2, 5),
            min_bars=trial.suggest_int('min_bars', 10, 35),
            convergence_threshold=trial.suggest_float('convergence_threshold', 0.15, 0.45),
            max_hold_days=trial.suggest_int('max_hold_days', 5, 15),
            score_threshold=trial.suggest_float('score_threshold', 25, 60),
            stop_loss_pct=trial.suggest_float('stop_loss_pct', -8.0, -3.0),
            take_profit_pct=trial.suggest_float('take_profit_pct', 8.0, 20.0),
            atr_stop_mult=trial.suggest_float('atr_stop_mult', 1.5, 3.0),
            max_positions=trial.suggest_int('max_positions', 3, 8),
        )

        engine = BacktestEngine(config)
        
        # 靜音模式跑回測
        import io
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            trades = engine.run(self.stock_data)
        finally:
            sys.stdout = old_stdout

        if len(trades) < 5:
            return -10.0  # 交易次數太少，視為無效

        metrics = engine.get_metrics()

        # 主目標: Sharpe Ratio，加入 MDD 懲罰
        sharpe = metrics.sharpe_ratio
        mdd_penalty = max(0, metrics.max_drawdown_pct - 20) * 0.1  # MDD > 20% 扣分

        return sharpe - mdd_penalty

    def optimize(self, verbose: bool = True) -> dict:
        """
        執行 Walk-Forward 優化
        
        回傳: {
            'best_params': {...},
            'oos_metrics': BacktestMetrics,
            'oos_trades': [...],
            'windows': [...]
        }
        """
        if not HAS_OPTUNA:
            print("[Optimizer] Optuna 未安裝！請執行: pip install optuna")
            return {}

        windows = self._create_windows()
        if not windows:
            print("[Optimizer] 資料不足，無法建立 Walk-Forward 視窗")
            return {}

        print(f"[Optimizer] Walk-Forward 優化")
        print(f"  訓練: {self.train_months} 個月 | 驗證: {self.test_months} 個月")
        print(f"  視窗數: {len(windows)} | 每視窗 trials: {self.n_trials}")
        print()

        all_oos_trades = []
        window_results = []

        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            print(f"  ── Window {i+1}/{len(windows)} ──")
            print(f"     訓練: {train_start} ~ {train_end}")
            print(f"     驗證: {test_start} ~ {test_end}")

            # Step 1: 在訓練集上用 Optuna 搜尋最佳參數
            study = optuna.create_study(
                direction='maximize',
                sampler=TPESampler(seed=42 + i),
            )
            optuna.logging.set_verbosity(optuna.logging.WARNING)

            study.optimize(
                lambda trial: self._objective(trial, train_start, train_end),
                n_trials=self.n_trials,
                show_progress_bar=verbose,
            )

            best_params = study.best_trial.params
            best_sharpe = study.best_trial.value
            print(f"     訓練集 Best Sharpe: {best_sharpe:.3f}")
            if verbose:
                print(f"     最佳參數: {best_params}")

            # Step 2: 用最佳參數在驗證集上回測 (OOS)
            oos_config = BacktestConfig(
                start_date=test_start,
                end_date=test_end,
                **best_params,
            )
            oos_engine = BacktestEngine(oos_config)
            
            import io, sys
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                oos_trades = oos_engine.run(self.stock_data)
            finally:
                sys.stdout = old_stdout

            oos_metrics = oos_engine.get_metrics()
            print(f"     OOS: {len(oos_trades)} 筆交易 | "
                  f"勝率: {oos_metrics.win_rate:.1f}% | "
                  f"Sharpe: {oos_metrics.sharpe_ratio:.3f}")

            all_oos_trades.extend(oos_trades)
            window_results.append({
                'window': i + 1,
                'train': f"{train_start}~{train_end}",
                'test': f"{test_start}~{test_end}",
                'best_params': best_params,
                'train_sharpe': best_sharpe,
                'oos_sharpe': oos_metrics.sharpe_ratio,
                'oos_win_rate': oos_metrics.win_rate,
                'oos_trades': len(oos_trades),
            })
            print()

        # 彙整所有 OOS 結果
        oos_trade_dicts = [
            {
                'entry_date': t.entry_date,
                'exit_date': t.exit_date,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'return_pct': t.return_pct,
                'hold_days': t.hold_days,
            }
            for t in all_oos_trades
        ]
        overall_oos = calculate_metrics(oos_trade_dicts)

        print(f"═══ Walk-Forward 最終結果 (所有 OOS 合併) ═══")
        for k, v in metrics_to_dict(overall_oos).items():
            print(f"  {k}: {v}")

        # 找出使用最多的參數 (眾數)
        if window_results:
            # 取最後一個視窗的參數作為推薦 (最近的市場環境)
            best_params = window_results[-1]['best_params']
            print(f"\n[Optimizer] 推薦參數 (最新視窗):")
            for k, v in best_params.items():
                print(f"  {k}: {v}")

        return {
            'best_params': best_params if window_results else {},
            'oos_metrics': overall_oos,
            'oos_trades': all_oos_trades,
            'windows': window_results,
        }


if __name__ == "__main__":
    import data.database as db_module

    print("=== Optuna Walk-Forward 參數優化 ===")
    db_module.init_db()

    # 載入資料 (使用快取中的全部資料)
    # 正式使用建議先跑一次 main.py 下載全台股資料
    sample = ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW",
              "3711.TW", "2303.TW", "3443.TW", "2345.TW"]
    stock_data = {}
    for t in sample:
        df = db_module.load_dataframe(t)
        if not df.empty:
            stock_data[t] = df

    if len(stock_data) < 3:
        print("快取中資料不足，請先執行 main.py 下載")
    else:
        optimizer = WalkForwardOptimizer(
            stock_data=stock_data,
            train_months=6,
            test_months=2,
            n_trials=50,  # 測試時用少量 trials，正式可設 200+
        )
        result = optimizer.optimize()
