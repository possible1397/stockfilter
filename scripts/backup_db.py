"""
scripts/backup_db.py — 資料庫備份

價格壞了可以跟 yfinance 重抓，但 2020 年起的籌碼資料補回來很痛，
scan_results 更是完全無法重建（每天的訊號是當下算出來的）。
這個腳本用 SQLite 的線上備份 API，即使資料庫正在被讀寫也安全，
不會像直接複製檔案那樣可能抓到寫到一半的狀態。

用法:
    python scripts/backup_db.py                          # 備份到預設位置
    python scripts/backup_db.py --dest E:\\backup\\stockfilter  # 指定位置（建議放另一顆磁碟）
    python scripts/backup_db.py --keep 4                 # 保留最近 4 份（預設 3）

建議搭配 Windows 工作排程器每週跑一次。
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data.database as db_module

DEFAULT_DEST = os.environ.get("STOCKFILTER_BACKUP_DIR", r"D:\stockfilter_backup")


def human(n):
    return f"{n / 1024 ** 3:.2f} GB" if n > 1024 ** 3 else f"{n / 1024 ** 2:.1f} MB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=DEFAULT_DEST,
                    help="備份目錄（建議另一顆磁碟）")
    ap.add_argument("--keep", type=int, default=3, help="保留幾份（預設 3）")
    args = ap.parse_args()

    src = db_module.DB_PATH
    if not os.path.exists(src):
        print(f"找不到資料庫: {src}")
        return 1

    os.makedirs(args.dest, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(args.dest, f"stock_data_{stamp}.db")

    size = os.path.getsize(src)
    print(f"來源: {src} ({human(size)})")
    print(f"目標: {dst}")

    t0 = time.time()
    done = {"pages": 0}

    def progress(status, remaining, total):
        if total:
            pct = 100 * (total - remaining) / total
            if pct - done["pages"] >= 10:
                done["pages"] = pct
                print(f"  {pct:.0f}% ...", flush=True)

    con = sqlite3.connect(src)
    try:
        out = sqlite3.connect(dst)
        with out:
            con.backup(out, pages=20000, progress=progress)
        out.close()
    finally:
        con.close()

    print(f"完成，耗時 {time.time() - t0:.0f} 秒 ({human(os.path.getsize(dst))})")

    # 輪替：只留最近 N 份
    files = sorted(
        (f for f in os.listdir(args.dest)
         if f.startswith("stock_data_") and f.endswith(".db")),
        reverse=True)
    for old in files[args.keep:]:
        path = os.path.join(args.dest, old)
        try:
            os.remove(path)
            print(f"已刪除舊備份: {old}")
        except OSError as e:
            print(f"刪除失敗 {old}: {e}")

    kept = min(len(files), args.keep)
    print(f"目前保留 {kept} 份備份於 {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
