"""
scripts/run_backtest.py
使用 SQLite 快取的資料執行回測，產生績效報告

優化記錄 (2026-03-28):
  - 改從 config.yaml 讀取參數，不再 hardcode
  - 新增 --start / --end / --no-next-open / --breakout-only CLI 參數
  - 新增 ^TWII benchmark 比較
  - 新增逐年勝率分解輸出
"""
import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import data.database as db_module
from data.cleaner import DataCleaner
from backtest.engine import BacktestEngine, BacktestConfig
from backtest.report import generate_report

# ── CLI 參數 ─────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="台股三角收斂回測")
parser.add_argument("--start", default="2020-07-01",
                    help="回測起始日 (預設 2020-07-01，跳過 COVID 崩盤異常期)")
parser.add_argument("--end", default="2026-03-26", help="回測結束日")
parser.add_argument("--no-next-open", action="store_true",
                    help="以當日收盤進場 (預設: 次日開盤)")
parser.add_argument("--breakout-only", action="store_true",
                    help="只進突破確認的訊號 (更保守)")
parser.add_argument("--config", default="config.yaml", help="設定檔路徑")
parser.add_argument("--max-tickers", type=int, default=1700, help="最多載入幾檔")
parser.add_argument("--min-grade", default="",
                    help="最低族群評級門檻: '' = 不限, 'B' = 只進A/B, 'A' = 只進A")
parser.add_argument("--market-threshold", type=float, default=None,
                    help="大盤分數門檻 (覆蓋 config.yaml)")
parser.add_argument("--dynamic-position", action="store_true",
                    help="啟用動態倉位 (依市場分數調整: >=60→5倉, >=45→3倉, <45→2倉)")
parser.add_argument("--portfolio-stop", type=float, default=None,
                    help="Portfolio drawdown stop pct (e.g. 15 = pause new entries if down 15 pct from peak)")
parser.add_argument("--require-ma200", action="store_true",
                    help="只在指數站上年線(MA200)時允許進場")
parser.add_argument("--ma20-trail", action="store_true",
                    help="Use MA20 as trailing stop instead of ATR after profit exceeds 5pct")
parser.add_argument("--atr-mult", type=float, default=None,
                    help="ATR trailing stop multiplier (e.g. 4.0)")
parser.add_argument("--atr-activate", type=float, default=None,
                    help="ATR trailing stop activation threshold in pct (e.g. 3.0)")
args = parser.parse_args()

print("=== 台股三角收斂回測 ===\n")

# ── 初始化 DB ────────────────────────────────────────────────────────────────
db_module.init_db()

# ── 載入股票清單 ──────────────────────────────────────────────────────────────
print("[1] 從 SQLite 載入股票清單...")
import sqlite3
with sqlite3.connect(db_module.DB_PATH) as conn:
    rows = conn.execute("""
        SELECT ticker, COUNT(*) as bars
        FROM daily_prices
        GROUP BY ticker
        HAVING bars >= 200
        ORDER BY bars DESC
    """).fetchall()

print(f"    符合條件 (>=200 bars): {len(rows)} 檔")

# ── 載入 OHLCV 資料 ───────────────────────────────────────────────────────────
cleaner = DataCleaner()
stock_data = {}

print(f"[2] 載入前 {args.max_tickers} 檔股票資料...")
for ticker, bars in rows[:args.max_tickers]:
    df = db_module.load_dataframe(ticker)
    if df.empty:
        continue
    if cleaner.is_valid(ticker, df):
        stock_data[ticker] = df

print(f"    成功載入: {len(stock_data)} 檔\n")

# ── 建立回測設定 (從 config.yaml 讀取，再覆蓋 CLI 參數) ──────────────────────
print(f"[3] 載入回測參數 ({args.config})...")
config = BacktestConfig.from_yaml(args.config)
config.start_date = args.start
config.end_date = args.end
config.entry_on_next_open = not args.no_next_open
config.require_breakout = args.breakout_only
config.min_grade = args.min_grade
if args.market_threshold is not None:
    config.market_threshold = args.market_threshold
if args.dynamic_position:
    config.dynamic_position = True
if args.portfolio_stop is not None:
    config.portfolio_stop = True
    config.portfolio_stop_pct = args.portfolio_stop
if args.require_ma200:
    config.require_index_above_ma200 = True
if args.ma20_trail:
    config.use_ma20_trail = True
if args.atr_mult is not None:
    config.atr_stop_mult = args.atr_mult
if args.atr_activate is not None:
    config.atr_activate_pct = args.atr_activate

print(f"    start={config.start_date}  end={config.end_date}")
print(f"    score>={config.score_threshold}  hold<={config.max_hold_days}d")
print(f"    SL={config.stop_loss_pct}%  TP={config.take_profit_pct}%")
print(f"    ATR={config.atr_stop_mult}x (啟動>{config.atr_activate_pct}%)")
print(f"    next_open={config.entry_on_next_open}  "
      f"breakout_only={config.require_breakout}\n")

# ── 載入預計算掃描結果 ────────────────────────────────────────────────────────
print("[4] 載入預計算掃描結果...")
scan_results = db_module.load_precomputed_results(
    db_module.DB_PATH, config.start_date, config.end_date)
print(f"    載入 {len(scan_results)} 天的預算結果\n")

# ── 執行回測 ──────────────────────────────────────────────────────────────────
print("[5] 執行回測...")
engine = BacktestEngine(config)
trades = engine.run(stock_data, scan_results_by_date=scan_results)

if not trades:
    print("\n[!] 未產生任何交易，請確認資料範圍或降低 score_threshold")
    sys.exit(1)

# ── 計算績效 ──────────────────────────────────────────────────────────────────
metrics = engine.get_metrics()

# ── 終端摘要 ──────────────────────────────────────────────────────────────────
from backtest.metrics import metrics_to_dict
from collections import Counter

print("\n=== 績效摘要 ===")
for k, v in metrics_to_dict(metrics).items():
    print(f"  {k}: {v}")

print("\n逐年報酬:")
for year, ret in sorted(metrics.yearly_returns.items()):
    icon = "[+]" if ret > 0 else "[-]"
    print(f"  {year}: {ret:+.1f}% {icon}")

print("\n出場原因:")
exit_reasons = Counter(t.exit_reason for t in trades)
for reason, cnt in exit_reasons.most_common():
    pct = cnt / len(trades) * 100
    print(f"  {reason}: {cnt} ({pct:.1f}%)")

# ── Benchmark 比較 (^TWII 買進持有) ──────────────────────────────────────────
benchmark_return = None
print("\n[6] 計算 Benchmark (^TWII 買進持有)...")
try:
    twii = yf.download('^TWII', start=config.start_date, end=config.end_date,
                       progress=False)
    if not twii.empty:
        closes = twii['Close'].squeeze()
        bm_start = float(closes.dropna().iloc[0])
        bm_end = float(closes.dropna().iloc[-1])
        benchmark_return = (bm_end / bm_start - 1) * 100
        alpha = metrics.total_return_pct - benchmark_return
        alpha_str = f"+{alpha:.1f}%" if alpha >= 0 else f"{alpha:.1f}%"
        print(f"  ^TWII 買進持有: {benchmark_return:.1f}%")
        print(f"  策略總報酬:     {metrics.total_return_pct:.1f}%")
        print(f"  Alpha:          {alpha_str}")
except Exception as e:
    print(f"  [!] Benchmark 下載失敗: {e}")

# ── 生成報告 ──────────────────────────────────────────────────────────────────
print("\n[7] 生成回測報告...")
report_path = generate_report(
    metrics, trades,
    config=config,
    output_dir="output",
    benchmark_return=benchmark_return,
)
print(f"\n完成！報告位置: {report_path}")
