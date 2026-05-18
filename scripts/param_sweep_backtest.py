"""
scripts/param_sweep_backtest.py
VCP 收斂條件參數掃描回測

用途: 比較不同 PatternRecognizer 參數組合的歷史表現，
     找出在放寬條件後仍能維持良好勝率的最佳設定。

用法:
  python scripts/param_sweep_backtest.py
  python scripts/param_sweep_backtest.py --start 2023-01-01 --end 2025-12-31
  python scripts/param_sweep_backtest.py --max-tickers 200 --scan-interval 5
"""
import sys
import os
import argparse
import sqlite3
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestConfig, BacktestEngine
from backtest.metrics import calculate_metrics
from core.recognizer import PatternRecognizer

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--start",         default="2023-01-01")
parser.add_argument("--end",           default="2025-12-31")
parser.add_argument("--max-tickers",   type=int,   default=400)
parser.add_argument("--scan-interval", type=int,   default=10,
                    help="每幾個交易日掃描一次 (越大越快，預設 10 = 約每 2 週)")
parser.add_argument("--db",            default="stock_data.db")
args = parser.parse_args()

DB_PATH = args.db

# ── 測試的參數組合 ─────────────────────────────────────────────────────────────
PARAM_SETS = [
    {
        "label":                "Baseline (現行)",
        "convergence_threshold": 0.30,
        "min_points":           3,
        "min_bars":             20,
        "order":                5,
        "volume_dryup":         True,
    },
    {
        "label":                "Ratio 0.40",
        "convergence_threshold": 0.40,
        "min_points":           3,
        "min_bars":             20,
        "order":                5,
        "volume_dryup":         True,
    },
    {
        "label":                "Ratio 0.45",
        "convergence_threshold": 0.45,
        "min_points":           3,
        "min_bars":             20,
        "order":                5,
        "volume_dryup":         True,
    },
    {
        "label":                "MinPoints=2",
        "convergence_threshold": 0.30,
        "min_points":           2,
        "min_bars":             20,
        "order":                5,
        "volume_dryup":         True,
    },
    {
        "label":                "No VolumeFilter",
        "convergence_threshold": 0.30,
        "min_points":           3,
        "min_bars":             20,
        "order":                5,
        "volume_dryup":         False,
    },
    {
        "label":                "Order=3 (更敏感)",
        "convergence_threshold": 0.30,
        "min_points":           3,
        "min_bars":             15,
        "order":                3,
        "volume_dryup":         True,
    },
    {
        "label":                "Combined Relaxed",
        "convergence_threshold": 0.40,
        "min_points":           2,
        "min_bars":             15,
        "order":                3,
        "volume_dryup":         False,
    },
]

# ── 載入股票資料 ───────────────────────────────────────────────────────────────

def load_stock_data(db_path: str, start: str, end: str, max_tickers: int) -> dict:
    """
    從 daily_prices 載入歷史 K 線，選取資料最完整的前 N 檔。
    回傳 {ticker: DataFrame(index=DatetimeIndex, columns=[Open,High,Low,Close,Volume])}
    """
    print(f"[Loader] 從 {db_path} 載入 {args.start} ~ {args.end} 股票資料...")
    with sqlite3.connect(db_path) as conn:
        # 先統計每檔的 bar 數，挑資料最完整的前 N 檔
        count_df = pd.read_sql(
            """SELECT ticker, COUNT(*) as cnt FROM daily_prices
               WHERE date BETWEEN ? AND ?
               GROUP BY ticker ORDER BY cnt DESC""",
            conn, params=(start, end)
        )
        top_tickers = count_df.head(max_tickers)["ticker"].tolist()

        # 載入這些 ticker 的完整資料 (需要足夠前置歷史給 SMA200/ATR)
        early_start = str(pd.Timestamp(start) - pd.DateOffset(years=1))[:10]
        df_all = pd.read_sql(
            f"""SELECT ticker, date, open, high, low, close, volume
                FROM daily_prices
                WHERE ticker IN ({','.join('?' * len(top_tickers))})
                  AND date >= ?
                ORDER BY ticker, date""",
            conn, params=top_tickers + [early_start]
        )

    stock_data = {}
    for ticker, grp in df_all.groupby("ticker"):
        grp = grp.set_index(pd.DatetimeIndex(grp["date"]))
        grp = grp.rename(columns={
            "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume"
        })[["Open", "High", "Low", "Close", "Volume"]]
        stock_data[ticker] = grp

    print(f"[Loader] 載入完成: {len(stock_data)} 檔")
    return stock_data


# ── 獨立掃描函式 (不依賴引擎內部方法) ─────────────────────────────────────────

def build_scan_results(params: dict, stock_data: dict, start: str, end: str) -> dict:
    """用指定參數跑逐日 VCP 掃描，回傳 {date_str: [signal_dict, ...]}"""
    recognizer = PatternRecognizer(
        order=params["order"],
        min_points=params["min_points"],
        min_bars=params["min_bars"],
        convergence_threshold=params["convergence_threshold"],
        long_only=True,
        trend_filter=True,
        volume_dryup=params["volume_dryup"],
    )

    all_dates: list[str] = sorted({
        str(d.date())
        for df in stock_data.values()
        for d in df.index
        if start <= str(d.date()) <= end
    })

    results_by_date: dict = {}
    total_signals = 0
    min_len = params["min_bars"] + params["order"] * 2

    print(f"  掃描中 ({len(all_dates)} 日 × {len(stock_data)} 檔, "
          f"每 {args.scan_interval} 日掃一次)...")

    for i, date_str in enumerate(all_dates):
        if i % args.scan_interval != 0:
            continue

        ts = pd.Timestamp(date_str)
        day_signals = []
        for ticker, df in stock_data.items():
            if df.empty:
                continue
            df_up_to = df[df.index <= ts]
            if len(df_up_to) < min_len:
                continue
            result = recognizer.analyze(ticker, df_up_to)
            if result.is_converging:
                day_signals.append({
                    "ticker":            ticker,
                    "score":             result.pattern_score + result.breakout_score,
                    "is_breakout":       result.is_breakout,
                    "sector":            "",
                    "grade":             "C",
                    "convergence_ratio": result.convergence_ratio,
                    "bar_span":          result.bar_span,
                })

        if day_signals:
            day_signals.sort(key=lambda x: x["score"], reverse=True)
            results_by_date[date_str] = day_signals
            total_signals += len(day_signals)

    print(f"  發現 {total_signals} 個訊號 ({len(results_by_date)} 個掃描日有訊號)")
    return results_by_date


# ── 執行一組參數的回測 ──────────────────────────────────────────────────────────

def run_one(params: dict, stock_data: dict) -> dict:
    cfg = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        score_threshold=0.0,
        max_hold_days=30,
        stop_loss_pct=-7.0,
        take_profit_pct=20.0,
        atr_stop_mult=3.0,
        atr_activate_pct=5.0,
        entry_on_next_open=True,
        use_market_filter=False,
        max_positions=10,
        position_size_pct=10.0,
    )

    scan_results = build_scan_results(params, stock_data, args.start, args.end)

    engine = BacktestEngine(cfg)
    t0 = time.time()
    trades = engine.run(stock_data, scan_results_by_date=scan_results)
    elapsed = time.time() - t0

    if not trades:
        return {
            "label":        params["label"],
            "trades":       0,
            "win_rate":     0.0,
            "avg_return":   0.0,
            "profit_factor":0.0,
            "max_dd":       0.0,
            "sharpe":       0.0,
            "elapsed_s":    round(elapsed, 1),
        }

    trade_dicts = [
        {k: getattr(t, k) for k in ("return_pct", "hold_days", "entry_date", "exit_date",
                                     "entry_price", "exit_price", "exit_reason")}
        for t in trades
    ]
    metrics = calculate_metrics(trade_dicts)
    return {
        "label":         params["label"],
        "trades":        metrics.total_trades,
        "win_rate":      round(metrics.win_rate, 1),
        "avg_return":    round(metrics.expectancy, 2),
        "profit_factor": round(metrics.profit_factor, 2),
        "max_dd":        round(metrics.max_drawdown_pct, 1),
        "sharpe":        round(metrics.sharpe_ratio, 2),
        "elapsed_s":     round(elapsed, 1),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    stock_data = load_stock_data(DB_PATH, args.start, args.end, args.max_tickers)

    results = []
    for i, params in enumerate(PARAM_SETS):
        print(f"\n[{i+1}/{len(PARAM_SETS)}] {params['label']}")
        print(f"  threshold={params['convergence_threshold']}  "
              f"min_pts={params['min_points']}  "
              f"min_bars={params['min_bars']}  "
              f"order={params['order']}  "
              f"vol_dryup={params['volume_dryup']}")
        row = run_one(params, stock_data)
        results.append(row)
        print(f"  -> 交易數:{row['trades']}  勝率:{row['win_rate']}%  "
              f"均報酬:{row['avg_return']}%  PF:{row['profit_factor']}  "
              f"MaxDD:{row['max_dd']}%  Sharpe:{row['sharpe']}  ({row['elapsed_s']}s)")

    # ── 彙總表 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("  參數組合回測彙總")
    print("=" * 90)
    print(f"  {'參數組合':<22} {'交易數':>6} {'勝率':>7} {'均報酬':>8} {'PF':>6} {'MaxDD':>8} {'Sharpe':>8}")
    print("-" * 90)
    for r in results:
        marker = ""
        # 標記: 勝率 > baseline 且 交易數明顯更多 → 潛力組合
        if r["win_rate"] >= results[0]["win_rate"] and r["trades"] > results[0]["trades"] * 1.2:
            marker = " <<<"
        print(f"  {r['label']:<22} {r['trades']:>6} {r['win_rate']:>6.1f}% "
              f"{r['avg_return']:>7.2f}% {r['profit_factor']:>6.2f} "
              f"{r['max_dd']:>7.1f}% {r['sharpe']:>8.2f}{marker}")

    # 存 CSV
    out_path = os.path.join("output", "param_sweep_result.csv")
    os.makedirs("output", exist_ok=True)
    pd.DataFrame(results).to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n結果已存至: {out_path}")

    # 結論建議
    best = max(results[1:], key=lambda r: (r["win_rate"], r["trades"]))  # 排除 baseline
    print(f"\n建議: '{best['label']}' 相對最優")
    print(f"  勝率 {best['win_rate']}%  均報酬 {best['avg_return']}%  交易數 {best['trades']}")


if __name__ == "__main__":
    main()
