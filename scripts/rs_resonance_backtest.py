"""
scripts/rs_resonance_backtest.py
RS Breakout + Sector Resonance Strategy Backtest

Strategy:
  Universe  = RS(12mo) >= threshold  (top N% relative strength vs all scanned stocks)
  Entry     = price within X% of 52-week high (momentum breakout)
  Filter    = ≥2 stocks in same theme also near/at 52w high on the same day (sector resonance)

目標: 捕捉 AI/半導體族群的動能強勢, 目標 2 週獲利 15%

用法:
  python scripts/rs_resonance_backtest.py
  python scripts/rs_resonance_backtest.py --start 2023-01-01 --end 2025-12-31
  python scripts/rs_resonance_backtest.py --max-tickers 300 --scan-interval 3
"""
import sys
import os
import argparse
import sqlite3
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestConfig, BacktestEngine
from backtest.metrics import calculate_metrics
from core.sector_rotation import THEME_SECTOR_MAP

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--start",         default="2023-01-01")
parser.add_argument("--end",           default="2025-12-31")
parser.add_argument("--max-tickers",   type=int, default=400)
parser.add_argument("--scan-interval", type=int, default=5,
                    help="每幾個交易日掃描一次 (越大越快，預設 5 = 每週一次)")
parser.add_argument("--db",            default="stock_data.db")
args = parser.parse_args()

DB_PATH = args.db

# ── 測試的參數組合 ─────────────────────────────────────────────────────────────
PARAM_SETS = [
    {
        "label":        "RS80 嚴格新高+共振",
        "rs_threshold": 80,
        "high_pct":     1.00,   # close >= 52w high × 1.00 (嚴格突破新高)
        "resonance":    True,
        "hold_days":    20,
        "tp_pct":       15.0,
    },
    {
        "label":        "RS80 近高3%+共振",
        "rs_threshold": 80,
        "high_pct":     0.97,   # close >= 52w high × 0.97 (近高 3% 以內)
        "resonance":    True,
        "hold_days":    20,
        "tp_pct":       15.0,
    },
    {
        "label":        "RS70 近高3%+共振",
        "rs_threshold": 70,
        "high_pct":     0.97,
        "resonance":    True,
        "hold_days":    20,
        "tp_pct":       15.0,
    },
    {
        "label":        "RS80 近高3% 無共振",
        "rs_threshold": 80,
        "high_pct":     0.97,
        "resonance":    False,  # 不要求族群共振，對照組
        "hold_days":    20,
        "tp_pct":       15.0,
    },
    {
        "label":        "RS80 近高3%+共振 10d",
        "rs_threshold": 80,
        "high_pct":     0.97,
        "resonance":    True,
        "hold_days":    10,     # 縮短持有期 (更短線)
        "tp_pct":       15.0,
    },
    {
        "label":        "RS80 近高3%+共振 TP20%",
        "rs_threshold": 80,
        "high_pct":     0.97,
        "resonance":    True,
        "hold_days":    20,
        "tp_pct":       20.0,   # 目標放大到 20%
    },
]

# THEME_SECTOR_MAP 使用短碼 (e.g. "2330")，DB ticker 帶 suffix (e.g. "2330.TW")
def _short(ticker: str) -> str:
    """Strip .TW / .TWO suffix for THEME_SECTOR_MAP lookup"""
    return ticker.split(".")[0]

# ── 反查 theme → [short_codes] ─────────────────────────────────────────────
# THEME_SECTOR_MAP: {short_code: [theme1, theme2, ...]}
# 轉置為 {theme: [short_code1, ...]}
THEME_TO_SHORT: dict[str, list[str]] = {}
for _sc, _themes in THEME_SECTOR_MAP.items():
    for _theme in _themes:
        THEME_TO_SHORT.setdefault(_theme, []).append(_sc)


# ── 載入股票資料 ───────────────────────────────────────────────────────────────

def load_stock_data(db_path: str, start: str, end: str, max_tickers: int) -> dict:
    print(f"[Loader] 從 {db_path} 載入 {start} ~ {end} 股票資料...")
    with sqlite3.connect(db_path) as conn:
        count_df = pd.read_sql(
            """SELECT ticker, COUNT(*) as cnt FROM daily_prices
               WHERE date BETWEEN ? AND ?
               GROUP BY ticker ORDER BY cnt DESC""",
            conn, params=(start, end)
        )
        top_tickers = count_df.head(max_tickers)["ticker"].tolist()

        # 需要多 1 年前資料供 RS 和 52w high 計算
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
            "low": "Low",   "close": "Close", "volume": "Volume"
        })[["Open", "High", "Low", "Close", "Volume"]]
        stock_data[ticker] = grp

    print(f"[Loader] 載入完成: {len(stock_data)} 檔")
    return stock_data


# ── 預計算 RS rank 和 52週高點比率 ─────────────────────────────────────────────

def precompute_matrices(stock_data: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    回傳:
      rs_rank_wide : DataFrame (date × ticker)  每日 RS 百分位排名 (0-100)
      high_ratio   : DataFrame (date × ticker)  close / 52w_high 比率
    """
    print("[Precompute] 計算 RS rank 和 52週高點比率...")

    close_wide = pd.DataFrame({t: df["Close"] for t, df in stock_data.items()})

    # RS: 252 交易日報酬率的橫截面百分位排名
    ret_252 = close_wide.pct_change(252, fill_method=None)
    rs_rank_wide = ret_252.rank(axis=1, pct=True, na_option="keep") * 100

    # 52週收盤高點比率: close / rolling(252).max(Close)
    # 使用 close 而非 high，避免 intraday spike 造成條件過嚴
    max_252_close = close_wide.rolling(252, min_periods=200).max()
    high_ratio = close_wide.div(max_252_close)

    print("[Precompute] 完成")
    return rs_rank_wide, high_ratio


# ── 建立掃描訊號 ───────────────────────────────────────────────────────────────

def build_scan_results(params: dict, stock_data: dict,
                       rs_rank_wide: pd.DataFrame,
                       high_ratio: pd.DataFrame,
                       start: str, end: str) -> dict:
    """
    對每個掃描日:
      1. 找出 RS >= rs_threshold 且 close/52wHigh >= high_pct 的標的
      2. 若 resonance=True, 保留「同族群有 ≥2 檔同日符合條件」的標的
      3. score = RS 百分位分數 (越高排越前)
    """
    rs_thr   = params["rs_threshold"]
    high_pct = params["high_pct"]
    need_resonance = params["resonance"]

    all_dates = sorted({
        str(d.date())
        for df in stock_data.values()
        for d in df.index
        if start <= str(d.date()) <= end
    })

    results_by_date: dict = {}
    total_signals = 0

    print(f"  掃描中 ({len(all_dates)} 日 × {len(stock_data)} 檔, "
          f"每 {args.scan_interval} 日掃一次)...")

    for i, date_str in enumerate(all_dates):
        if i % args.scan_interval != 0:
            continue

        ts = pd.Timestamp(date_str)

        # 取當日截面 (align index)
        rs_row   = rs_rank_wide.loc[ts] if ts in rs_rank_wide.index else None
        high_row = high_ratio.loc[ts]   if ts in high_ratio.index   else None
        if rs_row is None or high_row is None:
            continue

        # Step 1: 基本條件篩選
        candidates: list[str] = []
        for ticker in stock_data:
            rs_val   = rs_row.get(ticker, np.nan)
            hr_val   = high_row.get(ticker, np.nan)
            if pd.isna(rs_val) or pd.isna(hr_val):
                continue
            if rs_val >= rs_thr and hr_val >= high_pct:
                candidates.append(ticker)

        if not candidates:
            continue

        cand_set = set(candidates)

        # cand_short: short-code set for THEME_SECTOR_MAP lookup
        cand_short = {_short(t) for t in cand_set}

        # Step 2: 族群共振篩選
        if need_resonance:
            resonant: list[str] = []
            for ticker in candidates:
                sc = _short(ticker)
                themes = THEME_SECTOR_MAP.get(sc, [])
                for theme in themes:
                    peers = THEME_TO_SHORT.get(theme, [])
                    # 同族群有幾個短碼 (含自己) 在 candidates 中
                    hit_count = sum(1 for p in peers if p in cand_short)
                    if hit_count >= 2:
                        resonant.append(ticker)
                        break  # 只要有一個 theme 符合就夠了
            final = resonant
        else:
            final = candidates

        if not final:
            continue

        # Step 3: 建立訊號 dict (score = RS 百分位)
        day_signals = []
        for ticker in final:
            rs_val = float(rs_row.get(ticker, 0))
            themes = THEME_SECTOR_MAP.get(_short(ticker), [])
            day_signals.append({
                "ticker":      ticker,
                "score":       rs_val,
                "is_breakout": high_row.get(ticker, 0) >= 1.0,
                "sector":      "/".join(themes) if themes else "未知",
                "grade":       "A" if themes else "C",
            })

        day_signals.sort(key=lambda x: x["score"], reverse=True)
        results_by_date[date_str] = day_signals
        total_signals += len(day_signals)

    print(f"  發現 {total_signals} 個訊號 ({len(results_by_date)} 個掃描日有訊號)")
    return results_by_date


# ── 執行一組參數的回測 ──────────────────────────────────────────────────────────

def run_one(params: dict, stock_data: dict,
            rs_rank_wide: pd.DataFrame, high_ratio: pd.DataFrame) -> dict:
    cfg = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        score_threshold=0.0,
        max_hold_days=params["hold_days"],
        stop_loss_pct=-7.0,
        take_profit_pct=params["tp_pct"],
        atr_stop_mult=0.0,          # 關閉 ATR 停損，讓固定停利主導
        entry_on_next_open=True,
        use_market_filter=False,
        max_positions=10,
        position_size_pct=10.0,
    )

    scan_results = build_scan_results(params, stock_data, rs_rank_wide, high_ratio,
                                      args.start, args.end)

    t0 = time.time()
    if not scan_results:
        elapsed = time.time() - t0
        trades = []
    else:
        engine = BacktestEngine(cfg)
        trades = engine.run(stock_data, scan_results_by_date=scan_results)
        elapsed = time.time() - t0

    if not trades:
        return {
            "label":          params["label"],
            "trades":         0,
            "win_rate":       0.0,
            "avg_return":     0.0,
            "hit_15pct":      0.0,   # 達到 15% 目標的比率
            "profit_factor":  0.0,
            "max_dd":         0.0,
            "sharpe":         0.0,
            "avg_hold":       0.0,
            "elapsed_s":      round(elapsed, 1),
        }

    trade_dicts = [
        {k: getattr(t, k) for k in ("return_pct", "hold_days", "entry_date", "exit_date",
                                     "entry_price", "exit_price", "exit_reason")}
        for t in trades
    ]
    metrics = calculate_metrics(trade_dicts)

    # 計算「達到 15% 獲利」的比例 (真正的目標達成率)
    tp_target = params["tp_pct"]
    hit_count = sum(1 for t in trade_dicts if t["return_pct"] >= tp_target)

    return {
        "label":         params["label"],
        "trades":        metrics.total_trades,
        "win_rate":      round(metrics.win_rate, 1),
        "avg_return":    round(metrics.expectancy, 2),
        "hit_15pct":     round(hit_count / metrics.total_trades * 100, 1),
        "profit_factor": round(metrics.profit_factor, 2),
        "max_dd":        round(metrics.max_drawdown_pct, 1),
        "sharpe":        round(metrics.sharpe_ratio, 2),
        "avg_hold":      round(metrics.avg_hold_days, 1),
        "elapsed_s":     round(elapsed, 1),
    }


# ── 找出近期真實交易案例 ────────────────────────────────────────────────────────

def find_best_examples(params: dict, stock_data: dict,
                       rs_rank_wide: pd.DataFrame, high_ratio: pd.DataFrame,
                       top_n: int = 5) -> list[dict]:
    """從最佳參數組合中找出實際成功交易案例"""
    cfg = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        score_threshold=0.0,
        max_hold_days=params["hold_days"],
        stop_loss_pct=-7.0,
        take_profit_pct=params["tp_pct"],
        atr_stop_mult=0.0,
        entry_on_next_open=True,
        use_market_filter=False,
        max_positions=10,
        position_size_pct=10.0,
    )
    scan_results = build_scan_results(params, stock_data, rs_rank_wide, high_ratio,
                                      args.start, args.end)
    engine = BacktestEngine(cfg)
    trades = engine.run(stock_data, scan_results_by_date=scan_results)

    winners = sorted(
        [t for t in trades if t.return_pct >= params["tp_pct"]],
        key=lambda t: t.return_pct, reverse=True
    )
    return [
        {
            "ticker":      t.ticker,
            "entry_date":  t.entry_date,
            "exit_date":   t.exit_date,
            "entry_price": round(t.entry_price, 2),
            "exit_price":  round(t.exit_price, 2),
            "return_pct":  round(t.return_pct, 2),
            "hold_days":   t.hold_days,
            "exit_reason": t.exit_reason,
        }
        for t in winners[:top_n]
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    stock_data = load_stock_data(DB_PATH, args.start, args.end, args.max_tickers)
    rs_rank_wide, high_ratio = precompute_matrices(stock_data)

    results = []
    for i, params in enumerate(PARAM_SETS):
        print(f"\n[{i+1}/{len(PARAM_SETS)}] {params['label']}")
        print(f"  RS>={params['rs_threshold']}%  "
              f"高點比率>={params['high_pct']:.0%}  "
              f"族群共振={params['resonance']}  "
              f"持有={params['hold_days']}日  TP={params['tp_pct']}%")
        row = run_one(params, stock_data, rs_rank_wide, high_ratio)
        results.append(row)
        print(f"  -> 交易數:{row['trades']}  勝率:{row['win_rate']}%  "
              f"目標達成率:{row['hit_15pct']}%  均報酬:{row['avg_return']}%  "
              f"PF:{row['profit_factor']}  MaxDD:{row['max_dd']}%  "
              f"Sharpe:{row['sharpe']}  均持:{row['avg_hold']}日  ({row['elapsed_s']}s)")

    # ── 彙總表 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("  RS Breakout + Sector Resonance 回測彙總")
    print("=" * 100)
    print(f"  {'參數組合':<24} {'交易數':>6} {'勝率':>7} {'目標達成率':>10} "
          f"{'均報酬':>8} {'PF':>6} {'MaxDD':>8} {'Sharpe':>8} {'均持天':>7}")
    print("-" * 100)
    for r in results:
        marker = " <<<" if r["hit_15pct"] >= 30 and r["trades"] >= 20 else ""
        print(f"  {r['label']:<24} {r['trades']:>6} {r['win_rate']:>6.1f}% "
              f"{r['hit_15pct']:>9.1f}% "
              f"{r['avg_return']:>7.2f}% {r['profit_factor']:>6.2f} "
              f"{r['max_dd']:>7.1f}% {r['sharpe']:>8.2f} {r['avg_hold']:>7.1f}{marker}")

    # ── 存 CSV ────────────────────────────────────────────────────────────────
    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", "rs_resonance_result.csv")
    pd.DataFrame(results).to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n結果已存至: {out_path}")

    # ── 最佳組合的成功案例 ──────────────────────────────────────────────────────
    best = max(results, key=lambda r: (r["hit_15pct"], r["trades"]))
    best_params = next(p for p in PARAM_SETS if p["label"] == best["label"])
    print(f"\n最佳組合: '{best['label']}'")
    print(f"  目標達成率 {best['hit_15pct']}%  均報酬 {best['avg_return']}%  交易數 {best['trades']}")

    print(f"\n  --- 成功案例 (獲利 >= {best_params['tp_pct']}%) ---")
    examples = find_best_examples(best_params, stock_data, rs_rank_wide, high_ratio, top_n=8)
    if examples:
        print(f"  {'代碼':<8} {'進場日':<12} {'出場日':<12} {'進場價':>8} "
              f"{'出場價':>8} {'報酬':>8} {'持天':>6} {'出場原因'}")
        print("  " + "-" * 75)
        for e in examples:
            print(f"  {e['ticker']:<8} {e['entry_date']:<12} {e['exit_date']:<12} "
                  f"{e['entry_price']:>8.2f} {e['exit_price']:>8.2f} "
                  f"{e['return_pct']:>7.1f}% {e['hold_days']:>6} {e['exit_reason']}")
    else:
        print("  (無達標案例)")

    # ── 逐年分解 ──────────────────────────────────────────────────────────────
    print(f"\n  --- 逐年目標達成率 ({best['label']}) ---")
    cfg = BacktestConfig(
        start_date=args.start, end_date=args.end,
        score_threshold=0.0, max_hold_days=best_params["hold_days"],
        stop_loss_pct=-7.0, take_profit_pct=best_params["tp_pct"],
        atr_stop_mult=0.0, entry_on_next_open=True,
        use_market_filter=False, max_positions=10, position_size_pct=10.0,
    )
    scan = build_scan_results(best_params, stock_data, rs_rank_wide, high_ratio,
                              args.start, args.end)
    eng  = BacktestEngine(cfg)
    all_trades = eng.run(stock_data, scan_results_by_date=scan)

    by_year: dict[str, list] = {}
    for t in all_trades:
        yr = t.entry_date[:4]
        by_year.setdefault(yr, []).append(t.return_pct)

    print(f"  {'年份':>6} {'交易數':>7} {'勝率':>8} {'目標達成率':>11} {'均報酬':>9}")
    print("  " + "-" * 45)
    for yr in sorted(by_year):
        rets = by_year[yr]
        win  = sum(1 for r in rets if r > 0)
        hit  = sum(1 for r in rets if r >= best_params["tp_pct"])
        print(f"  {yr:>6} {len(rets):>7} {win/len(rets)*100:>7.1f}% "
              f"{hit/len(rets)*100:>10.1f}% {sum(rets)/len(rets):>8.2f}%")


if __name__ == "__main__":
    main()
