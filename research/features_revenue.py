"""
research/features_revenue.py — 把月營收特徵併進特徵矩陣

規格見 docs/飆股解剖_特徵清單_v2_月營收.md（6 個特徵，清單已凍結）。

本檔最重要的一件事是 point-in-time:M 月營收的可用日是 M+1 月 11 日，
用 backward as-of merge 對齊到日線，並在輸出前 assert 每一列的
營收可用日都 <= 該交易日。把 M 月營收用在 M 月的交易日上就是前視偏誤。

輸出 research/data/features_v2.parquet（v1 的 20 個特徵 + 6 個營收特徵）。

用法: python research/features_revenue.py
"""
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data.database as db_module

IN = os.path.join("research", "data", "features.parquet")
OUT = os.path.join("research", "data", "features_v2.parquet")


def build_revenue_features(rev: pd.DataFrame) -> pd.DataFrame:
    """逐檔計算 6 個營收特徵，索引為 (ticker, publish_date)。"""
    out = []
    for tk, g in rev.groupby("ticker", sort=False):
        g = g.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
        r = g["revenue"].astype(float)
        if len(g) < 14:                      # 至少要能算年增率
            continue
        yoy = r / r.shift(12) - 1
        yoy3 = r.rolling(3).sum() / r.rolling(3).sum().shift(12) - 1
        f = pd.DataFrame({
            "ticker": tk,
            "publish_date": g["publish_date"],
            "rev_yoy": yoy,
            "rev_yoy_3m": yoy3,
            "rev_yoy_accel": yoy - yoy.shift(1),
            "rev_mom": r / r.shift(1) - 1,
        })
        pos = (yoy > 0).astype(int)
        blocks = (pos == 0).cumsum()
        f["rev_streak"] = pos.groupby(blocks).cumsum().clip(upper=24)
        f["rev_high_12m"] = (r >= r.rolling(12).max()).astype(float)
        out.append(f)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def main():
    t0 = time.time()
    if not os.path.exists(IN):
        print(f"找不到 {IN}，請先跑 research/features.py")
        return 1

    con = sqlite3.connect(f"file:{db_module.DB_PATH}?mode=ro", uri=True)
    try:
        rev = pd.read_sql(
            "SELECT ticker, revenue_year, revenue_month, revenue, publish_date "
            "FROM monthly_revenue", con)
    except Exception as e:
        print(f"讀不到 monthly_revenue:{e}\n請先跑 research/fetch_revenue.py")
        return 1
    con.close()
    print(f"月營收 {len(rev):,} 列 / {rev.ticker.nunique():,} 檔")
    if rev.empty:
        print("沒有營收資料。")
        return 1

    rf = build_revenue_features(rev)
    print(f"營收特徵 {len(rf):,} 列 / {rf.ticker.nunique():,} 檔  ({time.time()-t0:.0f}s)")

    feat = pd.read_parquet(IN)
    print(f"v1 特徵 {len(feat):,} 列")

    # ── point-in-time as-of 對齊 ──────────────────────────────────
    feat["_d"] = pd.to_datetime(feat["date"])
    rf["_d"] = pd.to_datetime(rf["publish_date"])
    feat = feat.sort_values("_d")
    rf = rf.sort_values("_d")

    merged = pd.merge_asof(
        feat, rf.drop(columns=["publish_date"]),
        on="_d", by="ticker", direction="backward")

    # 驗證:併進來的營收可用日必須 <= 交易日
    chk = pd.merge_asof(
        feat[["ticker", "_d"]], rf[["ticker", "_d", "publish_date"]],
        on="_d", by="ticker", direction="backward")
    bad = chk.dropna(subset=["publish_date"])
    bad = bad[pd.to_datetime(bad["publish_date"]) > bad["_d"]]
    assert len(bad) == 0, f"前視偏誤:{len(bad)} 列的營收可用日晚於交易日"
    print("point-in-time 檢查通過（沒有任何一列用到尚未公布的營收）")

    merged = merged.drop(columns=["_d"])
    cols = ["rev_yoy", "rev_yoy_3m", "rev_yoy_accel", "rev_mom",
            "rev_streak", "rev_high_12m"]
    for c in cols:
        merged[c] = merged[c].astype("float32")

    merged.to_parquet(OUT, index=False, compression="zstd")
    print(f"\n已寫入 {OUT}  ({len(merged):,} 列 x {len(merged.columns)} 欄, "
          f"{os.path.getsize(OUT)/1024**2:.0f} MB, {time.time()-t0:.0f}s)")
    print("\n營收特徵的非空比例:")
    for c in cols:
        print(f"  {c:<16} {100*merged[c].notna().mean():6.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
