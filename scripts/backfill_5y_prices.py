"""
scripts/backfill_5y_prices.py
回補 5 年日 K 歷史 (批次下載，比逐檔快 10 倍以上)。

- 用 yf.download 一次抓一批 (預設 50 檔)，threads 併發
- 以 replace=True 寫入：整段覆蓋，確保還原權值基準一致
  (混用舊快取會在除權息處產生假跳空)
- 同時回補 ^TWII / ^TWOII 指數 7 年

用法:
    python scripts/backfill_5y_prices.py               # 全市場 5y
    python scripts/backfill_5y_prices.py --period 6y   # 改抓 6 年
    python scripts/backfill_5y_prices.py --resume      # 跳過已有 >=1000 根的股票
"""
import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import data.database as db_module
from data.stock_list import get_all_tickers  # 若函式名不同請改成專案實際的清單來源
from utils.logger import get_logger

logger = get_logger(__name__)

parser = argparse.ArgumentParser()
parser.add_argument("--period", default="5y")
parser.add_argument("--batch", type=int, default=50)
parser.add_argument("--resume", action="store_true",
                    help="跳過 DB 中已有 >=1000 根 K 線的股票")
args = parser.parse_args()

db_module.init_db()

# ── 指數 ────────────────────────────────────────────────────────────────
for idx in ("^TWII", "^TWOII"):
    period_idx = "12y" if args.period == "10y" else "7y"
    logger.info(f"[Backfill] 下載指數 {idx} ({period_idx})...")
    df = yf.download(idx, period=period_idx, progress=False)
    if df is not None and not df.empty:
        db_module.save_dataframe(idx, df, replace=True)
        logger.info(f"  {idx}: {len(df)} 根")

# ── 股票清單 ─────────────────────────────────────────────────────────────────
try:
    tickers = get_all_tickers()
except Exception:
    # fallback: 用 DB 現有清單
    import sqlite3
    with sqlite3.connect(db_module.DB_PATH) as conn:
        tickers = [r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM daily_prices WHERE ticker NOT LIKE '^%'")]
logger.info(f"[Backfill] 目標 {len(tickers)} 檔，period={args.period}")

if args.resume:
    import sqlite3
    with sqlite3.connect(db_module.DB_PATH) as conn:
        done = {r[0] for r in conn.execute(
            "SELECT ticker FROM daily_prices GROUP BY ticker HAVING COUNT(*) >= 1000")}
    tickers = [t for t in tickers if t not in done]
    logger.info(f"[Backfill] --resume: 跳過 {len(done)} 檔，剩 {len(tickers)} 檔")

# ── 批次下載 ─────────────────────────────────────────────────────────────────
failed = []
for i in range(0, len(tickers), args.batch):
    batch = tickers[i:i + args.batch]
    try:
        data = yf.download(batch, period=args.period, group_by="ticker",
                           threads=True, progress=False, auto_adjust=True)
        saved = 0
        for t in batch:
            try:
                df = data[t].dropna(how="all") if len(batch) > 1 else data.dropna(how="all")
                if df is not None and not df.empty:
                    db_module.save_dataframe(t, df, replace=True)
                    saved += 1
                else:
                    failed.append(t)
            except Exception:
                failed.append(t)
        logger.info(f"[Backfill] {i + len(batch)}/{len(tickers)} (本批成功 {saved}/{len(batch)})")
    except Exception as e:
        logger.error(f"[Backfill] 批次失敗 {batch[:3]}...: {e}")
        failed.extend(batch)
    time.sleep(1.5)  # 溫和限速，避免被 Yahoo 封鎖

if failed:
    logger.warning(f"[Backfill] 失敗 {len(failed)} 檔 (可重跑 --resume 補): {failed[:20]}...")
logger.info("[Backfill] 完成！接著執行: DELETE FROM scan_results → python scripts/precompute_backtest.py --start 2020-06-01")
