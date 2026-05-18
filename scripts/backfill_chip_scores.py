"""
scripts/backfill_chip_scores.py
將歷史 scan_results 中 chip_score=0 的記錄，用現有 chip_data 重新計算後寫回。

chip_data 已有 2020-2026 完整資料，只是當時掃描時沒有計算分數。
對每個 ticker：載入一次 chip_data + price_df，逐筆掃描日期切片計算，批次寫回。

用法:
  python scripts/backfill_chip_scores.py
  python scripts/backfill_chip_scores.py --start 2022-01-01 --dry-run
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
from tqdm import tqdm

import data.database as db_module
from core.chip_analysis import ChipAnalyzer

parser = argparse.ArgumentParser()
parser.add_argument("--start",   default="2020-01-01", help="補齊起始日")
parser.add_argument("--end",     default="2026-05-07", help="補齊結束日")
parser.add_argument("--dry-run", action="store_true",  help="僅計算，不寫入")
args = parser.parse_args()

DB_PATH = db_module.DB_PATH
chip_eng = ChipAnalyzer()

print(f"=== chip_score 歷史回填 ({args.start} ~ {args.end}) ===\n")

# ── 1. 撈出所有 chip_score=0 的記錄 ─────────────────────────────────────────
with sqlite3.connect(DB_PATH) as conn:
    need = pd.read_sql("""
        SELECT id, ticker, scan_date
        FROM scan_results
        WHERE scan_date BETWEEN ? AND ?
          AND (chip_score IS NULL OR chip_score = 0)
        ORDER BY ticker, scan_date
    """, conn, params=(args.start, args.end))

print(f"需要補齊: {len(need):,} 筆，涉及 {need['ticker'].nunique()} 個 ticker")
if need.empty:
    print("全部已有 chip_score，無需補齊。")
    sys.exit(0)

# ── 2. 按 ticker 分組，逐一處理 ─────────────────────────────────────────────
updates: list[tuple] = []   # (chip_score, id)
skipped = 0

tickers = need['ticker'].unique()
for ticker in tqdm(tickers, desc="計算中", unit="ticker"):
    rows = need[need['ticker'] == ticker]

    # 載入 price + chip（各只載一次）
    price_df = db_module.load_dataframe(ticker)
    chip_df  = db_module.load_chip_data(ticker)

    if price_df.empty:
        skipped += len(rows)
        continue

    # price_df index 統一為 DatetimeIndex
    if not isinstance(price_df.index, pd.DatetimeIndex):
        price_df.index = pd.to_datetime(price_df.index)

    for _, row in rows.iterrows():
        scan_date = row['scan_date']
        rec_id    = row['id']

        # 切到掃描日（不含未來資料）
        p_slice = price_df[price_df.index <= scan_date]
        if len(p_slice) < 5:
            skipped += 1
            continue

        c_slice = None
        if chip_df is not None and not chip_df.empty:
            c_slice = chip_df.loc[:scan_date]   # DatetimeIndex 支援字串切片，自動截止到 scan_date

        result = chip_eng.analyze(ticker, p_slice, c_slice)
        score  = result.get('score', 0.0)
        updates.append((score, rec_id))

print(f"\n計算完成: {len(updates)} 筆有效, {skipped} 筆跳過（無價格資料）")

if args.dry_run:
    sample = updates[:10]
    print(f"[dry-run] 前 10 筆預覽: {sample}")
    sys.exit(0)

# ── 3. 批次寫回 ──────────────────────────────────────────────────────────────
BATCH = 500
with sqlite3.connect(DB_PATH) as conn:
    for i in range(0, len(updates), BATCH):
        batch = updates[i:i+BATCH]
        conn.executemany("UPDATE scan_results SET chip_score = ? WHERE id = ?", batch)
    conn.commit()

print(f"成功寫回 {len(updates)} 筆 chip_score 至 scan_results。")

# ── 4. 驗證 ──────────────────────────────────────────────────────────────────
with sqlite3.connect(DB_PATH) as conn:
    cur = conn.execute("""
        SELECT SUBSTR(scan_date,1,4) as yr,
               COUNT(*) as total,
               SUM(CASE WHEN chip_score > 0 THEN 1 ELSE 0 END) as has_chip,
               ROUND(AVG(chip_score),1) as avg_chip
        FROM scan_results
        WHERE scan_date BETWEEN ? AND ?
        GROUP BY yr ORDER BY yr
    """, (args.start, args.end))
    print("\n補齊後各年 chip_score 覆蓋率:")
    print(f"  {'年份':6s}  {'總筆數':>8s}  {'有chip':>8s}  {'覆蓋率':>8s}  {'平均分':>8s}")
    for yr, total, has, avg in cur.fetchall():
        pct = has / total * 100 if total else 0
        print(f"  {yr:6s}  {total:>8d}  {has:>8d}  {pct:>7.1f}%  {avg:>8.1f}")
