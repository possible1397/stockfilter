"""
scripts/full_rebuild.py — 一鍵完成資料回補與重新驗證的全部步驟。

執行:
    python scripts/full_rebuild.py

依序做:
  1. 回補 5 年價格 (backfill_5y_prices，可斷點續跑)
  2. 重建 sector 對照表 (刪過期快取 → TWSE OpenAPI 重抓)
  3. 回補籌碼資料 (backfill_5y_chips 若存在，否則抓近 120 交易日)
  4. 清空 scan_results → 以新邏輯重跑 precompute (2020-06-01 起，可續跑)
  5. 跑基準回測產出報告

全程約 1.5~2.5 小時，中斷後重跑本腳本會自動接續 (步驟 1/4 均可 resume)。
"""
import os
import sys
import subprocess
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

PY = sys.executable


def step(n, title):
    print(f"\n{'='*60}\n[Step {n}] {title}\n{'='*60}")


def run(cmd):
    print(f"$ {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"[!] 指令失敗 (exit {r.returncode})，中止。重跑本腳本可續傳。")
        sys.exit(r.returncode)


# ── 1. 價格回補 ──────────────────────────────────────────────────────────────
step(1, "回補 5 年價格 (yfinance 批次)")
run([PY, "scripts/backfill_5y_prices.py", "--resume"])

# ── 2. sector 對照表 ─────────────────────────────────────────────────────────
step(2, "重建 sector 對照表 (TWSE OpenAPI)")
cache = os.path.join(ROOT, "sector_list_cache.json")
if os.path.exists(cache):
    os.remove(cache)
    print("已刪除過期的 sector_list_cache.json")
from core.sector_rotation import SectorRotationEngine
eng = SectorRotationEngine()
eng.update_sector_map()
n = len(eng.sector_map)
print(f"sector 對照表重建完成: {n} 檔")
if n < 1000:
    print("[!] 覆蓋率仍偏低 (<1000 檔)，請檢查 TWSE OpenAPI 是否可連。中止。")
    sys.exit(1)

# ── 3. 籌碼回補 ──────────────────────────────────────────────────────────────
step(3, "回補籌碼資料")
if os.path.exists("scripts/backfill_5y_chips.py"):
    run([PY, "scripts/backfill_5y_chips.py"])
else:
    from data.fetcher import StockFetcher
    StockFetcher().update_market_chip_data(days=120)

# ── 4. 重跑 precompute ───────────────────────────────────────────────────────
step(4, "清空舊 scan_results → 重跑 precompute (2020-06-01 起)")
if "--yes" in sys.argv or os.environ.get("NON_INTERACTIVE") == "1":
    resp = "y"
else:
    resp = input("將刪除 scan_results 全部資料並重算 (約 1~2 小時)，繼續? [y/N] ")

if resp.strip().lower() == "y":
    with sqlite3.connect("stock_data.db") as conn:
        conn.execute("DELETE FROM scan_results")
        conn.commit()
    print("scan_results 已清空")
    run([PY, "scripts/precompute_backtest.py", "--start", "2020-06-01"])
else:
    print("略過 (稍後可手動執行 precompute_backtest.py)")

# ── 5. 基準回測 ──────────────────────────────────────────────────────────────
step(5, "基準回測 (config.yaml 目前參數)")
run([PY, "scripts/run_backtest.py", "--start", "2020-06-01"])

print("\n全部完成。接下來把 output/ 的回測報告拿來，")
print("我們再照『訓練/保留分離』的流程在 5 年資料上重驗參數與密度假說。")
