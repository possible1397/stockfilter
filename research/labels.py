"""
research/labels.py — 建立標記資料集

對每一筆 (ticker, 交易日) 計算「之後會發生什麼」，寫成 research/data/labels.parquet。
規格見 docs/飆股解剖_特徵清單_v1.md，任何改動都要先改規格。

主標籤: flyer_100 = 未來 60 個交易日內 max(High)/Close - 1 >= 1.00（三個月翻倍）
密度標籤: flyer_50（同窗 >= 0.50）、flyer_120（120 日窗 >= 1.00）

關鍵細節:
  - 用 High 不用 Close —— 問的是「有沒有出現過這個機會」
  - 前推視窗不完整的列標籤為 NaN 並整列剔除，不當作 False
  - excess_60 以同一天全市場等權平均為基準，供校準測試使用

用法: python research/labels.py
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data.database as db_module

OUT = os.path.join("research", "data", "labels.parquet")
LOAD_FROM = "2020-07-01"        # 含前置期，特徵需要 250 根 K 線
HORIZONS = (20, 60, 120)


def fwd_max(high: pd.Series, n: int) -> pd.Series:
    """max(High[t+1 .. t+n])；視窗不足時為 NaN。"""
    fut = high.shift(-1)
    return fut[::-1].rolling(n, min_periods=n).max()[::-1]


def fwd_min(low: pd.Series, n: int) -> pd.Series:
    fut = low.shift(-1)
    return fut[::-1].rolling(n, min_periods=n).min()[::-1]


def main():
    t0 = time.time()
    import sqlite3
    con = sqlite3.connect(f"file:{db_module.DB_PATH}?mode=ro", uri=True)
    print(f"讀取 daily_prices（{LOAD_FROM} 起，排除指數）...")
    df = pd.read_sql(
        "SELECT ticker, date, high, low, close FROM daily_prices "
        "WHERE ticker NOT LIKE '^%' AND date >= ? ORDER BY ticker, date",
        con, params=(LOAD_FROM,))
    con.close()
    print(f"  {len(df):,} 列 / {df.ticker.nunique():,} 檔  ({time.time()-t0:.0f}s)")

    out = []
    for tk, g in df.groupby("ticker", sort=False):
        if len(g) < 30:
            continue
        g = g.reset_index(drop=True)
        c = g["close"]
        rec = pd.DataFrame({"ticker": tk, "date": g["date"]})
        for n in HORIZONS:
            rec[f"fwd_max_{n}"] = (fwd_max(g["high"], n) / c - 1).astype("float32")
        rec["fwd_mdd_60"] = (fwd_min(g["low"], 60) / c - 1).astype("float32")
        rec["fwd_ret_60"] = (c.shift(-60) / c - 1).astype("float32")
        out.append(rec)

    lab = pd.concat(out, ignore_index=True)
    print(f"前推報酬完成 ({time.time()-t0:.0f}s)")

    # 超額報酬：同一天全市場等權平均
    mkt = lab.groupby("date")["fwd_ret_60"].transform("mean")
    lab["excess_60"] = (lab["fwd_ret_60"] - mkt).astype("float32")

    # 標籤（視窗不完整者維持 NaN）
    lab["flyer_100"] = np.where(lab["fwd_max_60"].isna(), np.nan,
                                (lab["fwd_max_60"] >= 1.00).astype(float))
    lab["flyer_50"] = np.where(lab["fwd_max_60"].isna(), np.nan,
                               (lab["fwd_max_60"] >= 0.50).astype(float))
    lab["flyer_120"] = np.where(lab["fwd_max_120"].isna(), np.nan,
                                (lab["fwd_max_120"] >= 1.00).astype(float))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    lab.to_parquet(OUT, index=False, compression="zstd")
    size = os.path.getsize(OUT) / 1024 ** 2

    print(f"\n已寫入 {OUT}  ({len(lab):,} 列, {size:.0f} MB, {time.time()-t0:.0f}s)")
    print("\n各標籤的整體基準率（含前置期，僅供理解量級，正式數字看 lift 報表）:")
    for col in ("flyer_100", "flyer_50", "flyer_120"):
        s = lab[col].dropna()
        print(f"  {col:<10} 有效 {len(s):>10,} 列   命中 {int(s.sum()):>8,}   "
              f"基準率 {100*s.mean():.3f}%")


if __name__ == "__main__":
    main()
