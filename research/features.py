"""
research/features.py — 建立特徵矩陣

規格見 docs/飆股解剖_特徵清單_v1.md（20 個特徵，清單已凍結）。
所有特徵只使用 t 日（含）以前的資料。輸出 research/data/features.parquet。

用法: python research/features.py
"""
import json
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data.database as db_module

OUT = os.path.join("research", "data", "features.parquet")
LOAD_FROM = "2020-07-01"
MIN_TURNOVER = 5_000_000        # 20 日成交金額中位數下限（元）


def price_features(g: pd.DataFrame) -> pd.DataFrame:
    """單一標的的價量特徵。g 需依日期排序。"""
    o, h, l, c, v = g["open"], g["high"], g["low"], g["close"], g["volume"]
    f = pd.DataFrame(index=g.index)

    # --- A. 波動壓縮 ---
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    atr60 = tr.rolling(60).mean()
    f["atr14_pct"] = atr14 / c
    f["atr_ratio_14_60"] = atr14 / atr60
    f["bb_width_20"] = 4 * c.rolling(20).std() / c.rolling(20).mean()

    # --- B. 量能 ---
    f["vol_ratio_5_50"] = v.rolling(5).mean() / v.rolling(50).mean()
    f["vol_ratio_20_60"] = v.rolling(20).mean() / v.rolling(60).mean()
    turnover = c * v
    turnover_med20 = turnover.rolling(20).median()
    f["turnover_20_log"] = np.log10(turnover_med20.where(turnover_med20 > 0))
    f["_turnover_med20"] = turnover_med20          # 只用於流動性過濾，不是特徵

    # --- C. 位置 ---
    f["pct_from_52w_high"] = c / h.rolling(250).max() - 1
    f["pct_from_52w_low"] = c / l.rolling(250).min() - 1
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    f["close_sma50"] = c / sma50 - 1
    f["sma50_sma200"] = sma50 / sma200 - 1

    # --- D. 動能 ---
    f["ret_20"] = c / c.shift(20) - 1
    f["ret_60"] = c / c.shift(60) - 1
    f["ret_120"] = c / c.shift(120) - 1
    f["vol_std_60"] = c.pct_change().rolling(60).std()

    return f


def main():
    t0 = time.time()
    con = sqlite3.connect(f"file:{db_module.DB_PATH}?mode=ro", uri=True)

    print(f"讀取 daily_prices（{LOAD_FROM} 起）...")
    px = pd.read_sql(
        "SELECT ticker, date, open, high, low, close, volume FROM daily_prices "
        "WHERE ticker NOT LIKE '^%' AND date >= ? ORDER BY ticker, date",
        con, params=(LOAD_FROM,))
    print(f"  {len(px):,} 列 / {px.ticker.nunique():,} 檔  ({time.time()-t0:.0f}s)")

    # --- 價量特徵（逐檔）---
    parts = []
    for tk, g in px.groupby("ticker", sort=False):
        if len(g) < 250:
            continue
        g = g.reset_index(drop=True)
        f = price_features(g)
        f.insert(0, "date", g["date"])
        f.insert(0, "ticker", tk)
        parts.append(f)
    feat = pd.concat(parts, ignore_index=True)
    print(f"價量特徵完成 ({time.time()-t0:.0f}s)")

    # --- E. 籌碼（只取價格表裡有的股票，過濾掉 chip_data 裡的雜訊代號）---
    print("讀取 chip_data...")
    chip = pd.read_sql(
        "SELECT ticker, date, trust_net_buy, foreign_net_buy FROM chip_data "
        "WHERE date >= ? AND ticker IN (SELECT DISTINCT ticker FROM daily_prices) "
        "ORDER BY ticker, date", con, params=(LOAD_FROM,))
    con.close()
    print(f"  {len(chip):,} 列 / {chip.ticker.nunique():,} 檔  ({time.time()-t0:.0f}s)")

    if len(chip):
        chip = chip.sort_values(["ticker", "date"])
        grp = chip.groupby("ticker", sort=False)
        chip["f_net20"] = grp["foreign_net_buy"].transform(
            lambda s: s.rolling(20, min_periods=10).sum())
        chip["t_net20"] = grp["trust_net_buy"].transform(
            lambda s: s.rolling(20, min_periods=10).sum())
        # 投信連續買超天數（上限 20）
        pos = (chip["trust_net_buy"] > 0).astype(int)
        blocks = (pos == 0).groupby(chip["ticker"]).cumsum()
        chip["trust_consec"] = (pos.groupby([chip["ticker"], blocks]).cumsum()
                                .clip(upper=20))
        feat = feat.merge(chip[["ticker", "date", "f_net20", "t_net20", "trust_consec"]],
                          on=["ticker", "date"], how="left")
        # 以 20 日成交量為分母，讓不同股票可以橫向比較
        vol20 = px.groupby("ticker", sort=False)["volume"].transform(
            lambda s: s.rolling(20, min_periods=10).sum())
        px_v = px[["ticker", "date"]].copy()
        px_v["_vol20"] = vol20.values
        feat = feat.merge(px_v, on=["ticker", "date"], how="left")
        feat["foreign_net_20"] = feat["f_net20"] / feat["_vol20"].replace(0, np.nan)
        feat["trust_net_20"] = feat["t_net20"] / feat["_vol20"].replace(0, np.nan)
        feat = feat.drop(columns=["f_net20", "t_net20", "_vol20"])
    else:
        for col in ("foreign_net_20", "trust_net_20", "trust_consec"):
            feat[col] = np.nan

    # --- F. 型態旗標（來自既有 scan_results）---
    con = sqlite3.connect(f"file:{db_module.DB_PATH}?mode=ro", uri=True)
    scans = pd.read_sql(
        "SELECT scan_date AS date, ticker, status FROM scan_results", con)
    runs = pd.read_sql("SELECT scan_date FROM scan_runs", con)
    con.close()
    scans["is_breakout"] = (scans["status"] == "Breakout").astype("float32")
    scans["is_converging"] = (scans["status"] == "Converging").astype("float32")
    feat = feat.merge(scans[["date", "ticker", "is_breakout", "is_converging"]],
                      on=["ticker", "date"], how="left")
    # 有掃描紀錄的日子，沒中就是 0；沒掃描的日子維持 NaN
    scanned = feat["date"].isin(set(runs["scan_date"]))
    for col in ("is_breakout", "is_converging"):
        feat.loc[scanned & feat[col].isna(), col] = 0.0

    # --- F. 族群 20 日報酬中位數 ---
    with open("sector_list_cache.json", encoding="utf-8") as fh:
        smap_raw = json.load(fh)
    smap = {k: v.get("primary") for k, v in smap_raw.items() if isinstance(v, dict)}
    feat["_sector"] = feat["ticker"].map(smap)
    feat["sector_ret20_med"] = feat.groupby(["date", "_sector"])["ret_20"].transform("median")
    feat = feat.drop(columns=["_sector"])

    # --- 流動性過濾 ---
    before = len(feat)
    feat = feat[feat["_turnover_med20"] >= MIN_TURNOVER].drop(columns=["_turnover_med20"])
    print(f"流動性過濾（20 日成交金額中位數 >= {MIN_TURNOVER:,} 元）: "
          f"{before:,} -> {len(feat):,} 列")

    for c in feat.columns:
        if feat[c].dtype == "float64":
            feat[c] = feat[c].astype("float32")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    feat.to_parquet(OUT, index=False, compression="zstd")
    print(f"\n已寫入 {OUT}  ({len(feat):,} 列 x {len(feat.columns)} 欄, "
          f"{os.path.getsize(OUT)/1024**2:.0f} MB, {time.time()-t0:.0f}s)")
    print("\n各特徵的非空比例:")
    for c in feat.columns:
        if c in ("ticker", "date"):
            continue
        print(f"  {c:<20} {100*feat[c].notna().mean():6.1f}%")


if __name__ == "__main__":
    main()
