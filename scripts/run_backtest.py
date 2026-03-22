"""
scripts/run_backtest.py
使用 SQLite 快取的資料執行回測，產生績效報告
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data.database as db_module
from data.cleaner import DataCleaner
from backtest.engine import BacktestEngine, BacktestConfig
from backtest.report import generate_report

print("=== 台股三角收斂回測 ===\n")

# 初始化 DB
db_module.init_db()

# 取得所有有足夠資料的股票
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

# 載入資料 (限制數量以加速)
MAX_TICKERS = 300  # 測試用前 300 檔（按資料量排序）
cleaner = DataCleaner()
stock_data = {}

print(f"[2] 載入前 {MAX_TICKERS} 檔股票資料...")
loaded = 0
for ticker, bars in rows[:MAX_TICKERS]:
    df = db_module.load_dataframe(ticker)
    if df.empty:
        continue
    if cleaner.is_valid(ticker, df):
        stock_data[ticker] = df
        loaded += 1

print(f"    成功載入: {loaded} 檔\n")

# 設定回測參數
config = BacktestConfig(
    start_date="2025-06-01",
    end_date="2026-03-20",
    order=5,
    min_points=3,
    min_bars=20,
    convergence_threshold=0.3,
    max_hold_days=10,
    score_threshold=30.0,   # 較低門檻以取得足夠交易次數
    stop_loss_pct=-5.0,
    take_profit_pct=15.0,
    atr_stop_mult=2.0,
    max_positions=5,
    position_size_pct=20.0,
    use_market_filter=False,  # 關閉大盤濾網（DB 無法模擬大盤即時分數）
)

print(f"[3] 執行回測...")
print(f"    期間: {config.start_date} ~ {config.end_date}")
print(f"    進場門檻: {config.score_threshold} 分")
print(f"    停損/停利: {config.stop_loss_pct}% / +{config.take_profit_pct}%\n")

engine = BacktestEngine(config)
trades = engine.run(stock_data)

if not trades:
    print("\n[!] 未產生任何交易，請確認資料範圍或降低 score_threshold")
    sys.exit(1)

# 計算績效
metrics = engine.get_metrics()

# 印出摘要
from backtest.metrics import metrics_to_dict
print("\n=== 績效摘要 ===")
for k, v in metrics_to_dict(metrics).items():
    print(f"  {k}: {v}")

# 出場原因統計
from collections import Counter
exit_reasons = Counter(t.exit_reason for t in trades)
print("\n出場原因:")
for reason, cnt in exit_reasons.most_common():
    pct = cnt / len(trades) * 100
    print(f"  {reason}: {cnt} ({pct:.1f}%)")

# 生成報告
print("\n[4] 生成回測報告...")
report_path = generate_report(metrics, trades, config=config, output_dir="output")
print(f"\n完成！報告位置: {report_path}")
