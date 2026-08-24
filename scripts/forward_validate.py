"""
scripts/forward_validate.py — 向前驗證每日報告

以「凍結版規格」(config.yaml backtest 區段) 從 2026-04-01 重放至今日:
  1. 自動補算缺少的每日訊號 (Precomputer, 已存在的日期自動跳過)
  2. 用 BacktestEngine 重放 (與回測完全同一條程式路徑，無實作漂移)
  3. 輸出:目前持倉 / 已平倉交易 / 累積績效 → output/forward_validation.md

每日使用: 收盤後先跑 python main.py，再跑 python scripts/forward_validate.py
"""
import sys
import os
import contextlib
import io
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
import data.database as db_module
from backtest.engine import BacktestEngine, BacktestConfig
from backtest.metrics import metrics_to_dict

FREEZE_START = "2026-04-01"   # 向前驗證起點 (凍結規格,不得更動)
TODAY = datetime.today().strftime("%Y-%m-%d")

db_module.init_db()

# ── 0. 凍結規格檢查 (參數漂移會讓向前驗證失去意義) ──────────────────────────
from scripts.check_freeze import assert_frozen
assert_frozen(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"))

# ── 1. 補算缺少的訊號日 ──────────────────────────────────────────────────────
print(f"[1] 檢查 {FREEZE_START} ~ {TODAY} 訊號完整性...")
from scripts.precompute_backtest import Precomputer
pre = Precomputer()
pre.run(FREEZE_START, TODAY, ticker_limit=1700)   # 已存在的日期自動跳過

# ── 2. 載入資料 ──────────────────────────────────────────────────────────────
print("[2] 載入價格資料...")
con = sqlite3.connect(db_module.DB_PATH)
raw = pd.read_sql(
    "SELECT ticker, date, open, high, low, close, volume FROM daily_prices "
    "WHERE ticker NOT LIKE '^%' AND date >= '2025-06-01'", con)
raw["date"] = pd.to_datetime(raw["date"])
stock_data = {}
for t, g in raw.groupby("ticker"):
    g = g.set_index("date").sort_index()
    g.columns = [c.capitalize() for c in g.columns]
    stock_data[t] = g.drop(columns=["Ticker"])

scans = db_module.load_precomputed_results(db_module.DB_PATH, FREEZE_START, TODAY)
print(f"    {len(stock_data)} 檔 / {len(scans)} 個訊號日")

# ── 3. 以凍結參數重放 ────────────────────────────────────────────────────────
print("[3] 重放交易 (凍結版規格)...")
cfg = BacktestConfig.from_yaml("config.yaml")
cfg.start_date, cfg.end_date = FREEZE_START, TODAY
eng = BacktestEngine(cfg)
with contextlib.redirect_stdout(io.StringIO()):
    trades = eng.run(stock_data, scan_results_by_date=scans)

open_pos = [t for t in trades if t.exit_reason == "end_of_backtest"]
closed = [t for t in trades if t.exit_reason != "end_of_backtest"]
m = eng.get_metrics()

# ── 4. 報告 ──────────────────────────────────────────────────────────────────
lines = [f"# 向前驗證報告 ({TODAY})", "",
         f"驗證期間: {FREEZE_START} ~ {TODAY} | 規格: 凍結版 2026-07-11", ""]

lines.append("## 目前持倉")
if open_pos:
    lines.append("| 股票 | 進場日 | 進場價 | 現價 | 未實現損益 | 持有天數 | 初始停損 |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in open_pos:
        lines.append(f"| {t.ticker} | {t.entry_date} | {t.entry_price:.2f} | "
                     f"{t.exit_price:.2f} | {t.return_pct:+.1f}% | {t.hold_days} | "
                     f"{t.entry_price*0.90:.2f} |")
    lines.append("")
    lines.append("(浮盈 >= +15% 者改為追蹤停損: max(最高價-4*ATR, 前日低點))")
else:
    lines.append("(無持倉)")
lines.append("")

lines.append(f"## 已平倉交易 ({len(closed)} 筆)")
if closed:
    lines.append("| 股票 | 進場 | 出場 | 報酬 | 原因 |")
    lines.append("|---|---|---|---|---|")
    for t in sorted(closed, key=lambda x: x.exit_date):
        lines.append(f"| {t.ticker} | {t.entry_date} | {t.exit_date} | "
                     f"{t.return_pct:+.1f}% | {t.exit_reason} |")
lines.append("")

lines.append("## 累積績效 (含未實現)")
for k, v in metrics_to_dict(m).items():
    lines.append(f"- {k}: {v}")

report = "\n".join(lines)
out_path = os.path.join("output", "forward_validation.md")
os.makedirs("output", exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    f.write(report)
print()
print(report)
print(f"\n報告已存: {out_path}")
