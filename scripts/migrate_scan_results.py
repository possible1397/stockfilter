"""
scripts/migrate_scan_results.py — scan_results 加上唯一性約束與來源戳記

做三件事（可重複執行，已完成的部分會自動跳過）:
  1. 匯出現有 scan_results 到 CSV 當保險
  2. 重建 scan_results:加 config_hash / git_commit 欄位與 UNIQUE(scan_date, ticker)，
     同一 (日期, 股票) 只保留 id 最大的那筆
  3. 從既有資料回填 scan_runs（哪些日子算過），舊資料的戳記標記為 unknown

為什麼需要:
  - 目前同一天重跑掃描會插入第二份（庫裡已有 2 組重複），回測時同一訊號被算兩次
  - 8,537 筆訊號沒有任何欄位記得「這是用哪組參數算的」，參數一改就無法分辨新舊

用法:
    python scripts/migrate_scan_results.py            # 執行
    python scripts/migrate_scan_results.py --dry-run  # 只看會發生什麼
"""
import csv
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data.database as db_module

DRY = "--dry-run" in sys.argv

NEW_SCHEMA = """
CREATE TABLE scan_results_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date       TEXT    NOT NULL,
    ticker          TEXT    NOT NULL,
    grade           TEXT,
    total_score     REAL,
    market_score    REAL,
    sector_score    REAL,
    density_score   REAL,
    chain_score     REAL,
    chip_score      REAL,
    pattern_score   REAL,
    breakout_score  REAL,
    sector          TEXT,
    chain_position  TEXT,
    status          TEXT,
    current_price   REAL,
    convergence_ratio REAL,
    bar_span        INTEGER,
    config_hash     TEXT,
    git_commit      TEXT,
    UNIQUE (scan_date, ticker)
)
"""

COLS = ["scan_date", "ticker", "grade", "total_score", "market_score", "sector_score",
        "density_score", "chain_score", "chip_score", "pattern_score", "breakout_score",
        "sector", "chain_position", "status", "current_price", "convergence_ratio",
        "bar_span"]


def columns_of(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def main():
    db_path = db_module.DB_PATH
    print(f"資料庫: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=OFF")

    cols = columns_of(conn, "scan_results")
    if not cols:
        print("找不到 scan_results 表，先跑一次 main.py 或 init_db()。")
        return

    before = conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]
    dupes = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT scan_date, ticker FROM scan_results
            GROUP BY 1,2 HAVING COUNT(*) > 1)
    """).fetchone()[0]
    dates = conn.execute("SELECT COUNT(DISTINCT scan_date) FROM scan_results").fetchone()[0]
    print(f"現況: {before} 列 / {dates} 個掃描日 / {dupes} 組重複")

    already = "config_hash" in cols
    if already:
        print("scan_results 已有 config_hash 欄位，跳過重建。")
    else:
        # --- 1. 保險備份 ---
        os.makedirs("output", exist_ok=True)
        backup_csv = os.path.join(
            "output", f"scan_results_backup_{datetime.now():%Y%m%d_%H%M%S}.csv")
        if DRY:
            print(f"[dry-run] 會匯出備份到 {backup_csv}")
        else:
            rows = conn.execute("SELECT * FROM scan_results").fetchall()
            header = columns_of(conn, "scan_results")
            with open(backup_csv, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(rows)
            print(f"已備份 {len(rows)} 列到 {backup_csv}")

        # --- 2. 重建 ---
        col_list = ", ".join(COLS)
        rebuild = f"""
            INSERT INTO scan_results_new ({col_list}, config_hash, git_commit)
            SELECT {col_list}, NULL, 'unknown'
            FROM scan_results
            WHERE id IN (
                SELECT MAX(id) FROM scan_results GROUP BY scan_date, ticker
            )
        """
        if DRY:
            keep = conn.execute(
                "SELECT COUNT(*) FROM (SELECT MAX(id) FROM scan_results GROUP BY scan_date, ticker)"
            ).fetchone()[0]
            print(f"[dry-run] 會重建為 {keep} 列（移除 {before - keep} 列重複）")
        else:
            conn.execute("DROP TABLE IF EXISTS scan_results_new")
            conn.executescript(NEW_SCHEMA)
            conn.execute(rebuild)
            kept = conn.execute("SELECT COUNT(*) FROM scan_results_new").fetchone()[0]
            conn.execute("DROP TABLE scan_results")
            conn.execute("ALTER TABLE scan_results_new RENAME TO scan_results")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_results_date ON scan_results (scan_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_results_ticker ON scan_results (ticker)")
            conn.commit()
            print(f"重建完成: {before} -> {kept} 列（移除 {before - kept} 列重複）")

    # --- 3. 回填 scan_runs ---
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_runs (
            scan_date    TEXT PRIMARY KEY,
            computed_at  TEXT,
            config_hash  TEXT,
            git_commit   TEXT,
            n_signals    INTEGER,
            source       TEXT
        )
    """)
    missing = conn.execute("""
        SELECT scan_date, COUNT(*) FROM scan_results
        WHERE scan_date NOT IN (SELECT scan_date FROM scan_runs)
        GROUP BY 1
    """).fetchall()
    if DRY:
        print(f"[dry-run] 會回填 {len(missing)} 個掃描日到 scan_runs")
    elif missing:
        conn.executemany("""
            INSERT OR REPLACE INTO scan_runs
                (scan_date, computed_at, config_hash, git_commit, n_signals, source)
            VALUES (?, NULL, NULL, 'unknown', ?, 'backfill')
        """, missing)
        conn.commit()
        print(f"已回填 {len(missing)} 個掃描日到 scan_runs")
    else:
        print("scan_runs 已是最新。")

    if not DRY:
        after = conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]
        d2 = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT scan_date, ticker FROM scan_results GROUP BY 1,2 HAVING COUNT(*) > 1)
        """).fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
        print(f"驗證: {after} 列 / 重複 {d2} 組 / scan_runs {runs} 天")
        assert d2 == 0, "仍有重複，請檢查"
    conn.close()
    print("完成。")


if __name__ == "__main__":
    main()
