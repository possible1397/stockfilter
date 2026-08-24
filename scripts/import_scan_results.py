"""
scripts/import_scan_results.py
將 CSV 格式的預算掃描結果匯入 scan_results 表。

用途: 2026-07-09 在沙盒以「修正後邏輯」重算的 215 天預算結果
(output/scan_results_precomputed_20250801_20260709.csv) 匯回本機 DB，
不必重跑 precompute。

用法:
    python scripts/import_scan_results.py output/scan_results_precomputed_20250801_20260709.csv
    # 加 --wipe 先清空舊 scan_results (建議，避免新舊邏輯混雜)
    python scripts/import_scan_results.py <csv> --wipe
"""
import sys
import os
import csv
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data.database as db_module

parser = argparse.ArgumentParser()
parser.add_argument("csv_path")
parser.add_argument("--wipe", action="store_true", help="匯入前先清空 scan_results")
args = parser.parse_args()

db_module.init_db()

cols = ['scan_date', 'ticker', 'grade', 'total_score', 'market_score', 'sector_score',
        'density_score', 'chain_score', 'chip_score', 'pattern_score', 'breakout_score',
        'sector', 'chain_position', 'status', 'current_price', 'convergence_ratio', 'bar_span']

with open(args.csv_path, encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    rows = [tuple(r[c] if r[c] != '' else None for c in cols) for r in reader]

with db_module.get_connection(db_module.DB_PATH) as conn:
    if args.wipe:
        n = conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]
        conn.execute("DELETE FROM scan_results")
        print(f"已清空舊 scan_results ({n} 筆)")
    conn.executemany(
        f"INSERT INTO scan_results ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
        rows)
print(f"匯入完成: {len(rows)} 筆")
