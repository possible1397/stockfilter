"""
research/diagnose.py — 決定性診斷:lift 是預測力還是波動的機械效果?

主標籤「60 日內最大漲幅 >= 100%」天生偏愛高波動股 —— 波動加倍會大幅提高
觸及 +100% 的機率，即使期望報酬為零。所以探索期看到的那串
「高波動 + 高動能」的 lift，可能只是同義反覆。

本檔做三件事（全部在探索期，不動保留期）:

  A. 命中率 vs 超額報酬:同一批分位，除了 flyer_100 命中率，另外報
     excess_60 的平均、中位數與勝率。若命中率高但超額報酬 ~0，
     那個特徵就是標籤造成的錯覺。
  B. 波動的機械效果:直接看命中率與 excess_60 隨波動分位的變化。
  C. 條件分析（雙重排序）:固定波動分位後，動能還有沒有 lift。

用法: python research/diagnose.py
"""
import os
import sys

import numpy as np
import pandas as pd

FEATURES = os.path.join("research", "data", "features.parquet")
LABELS = os.path.join("research", "data", "labels.parquet")
OUT = os.path.join("output", "diagnose_v1.csv")
EXPLORE = ("2021-09-01", "2024-12-31")
N_BINS = 10

# A 段要檢視的特徵:探索期 lift 前段 + 幾個對照
FOCUS = ["ret_60", "atr14_pct", "close_sma50", "ret_120", "vol_std_60",
         "ret_20", "bb_width_20", "pct_from_52w_low", "atr_ratio_14_60",
         "vol_ratio_20_60", "vol_ratio_5_50", "pct_from_52w_high",
         "sma50_sma200", "turnover_20_log", "foreign_net_20", "trust_net_20",
         "is_breakout", "is_converging"]
BINARY = {"is_breakout", "is_converging"}


def stats(g, base):
    """一格的統計:命中率/lift + 超額報酬的平均、中位數、勝率。"""
    n = len(g)
    k = float(g["flyer_100"].sum())
    ex = g["excess_60"].dropna()
    m = ex.mean() if len(ex) else np.nan
    se = ex.std(ddof=1) / np.sqrt(len(ex)) if len(ex) > 1 else np.nan
    return {
        "n": n, "hits": int(k),
        "rate_pct": 100 * k / n if n else np.nan,
        "lift": (k / n) / base if n and base > 0 else np.nan,
        "excess_mean_pct": 100 * m,
        "excess_ci_lo_pct": 100 * (m - 1.96 * se) if np.isfinite(se) else np.nan,
        "excess_ci_hi_pct": 100 * (m + 1.96 * se) if np.isfinite(se) else np.nan,
        "excess_median_pct": 100 * ex.median() if len(ex) else np.nan,
        "excess_winrate_pct": 100 * (ex > 0).mean() if len(ex) else np.nan,
    }


def main():
    print("載入資料...")
    feat = pd.read_parquet(FEATURES)
    lab = pd.read_parquet(LABELS,
                          columns=["ticker", "date", "flyer_100", "fwd_ret_60", "excess_60"])
    df = feat.merge(lab, on=["ticker", "date"], how="inner")
    df = df[(df["date"] >= EXPLORE[0]) & (df["date"] <= EXPLORE[1])]
    df = df.dropna(subset=["flyer_100"])

    # labels.py 的 excess_60 以「未經流動性過濾的全市場」為基準，
    # 但這裡的母體已經過濾過，兩者不一致會讓所有數字偏移約 1pp。
    # 正確的問法是「在我買得到的股票裡，這個特徵有沒有用」——
    # 所以在過濾後的面板內重算基準。
    df["excess_60"] = (df["fwd_ret_60"]
                       - df.groupby("date")["fwd_ret_60"].transform("mean"))
    base = df["flyer_100"].mean()
    print(f"  {len(df):,} 列 / {df.date.nunique():,} 個交易日")
    print(f"  flyer_100 基準率 {100*base:.3f}%")
    print(f"  超額報酬基準:可交易母體內同日等權平均（依定義為 0，"
          f"實測 {100*df['excess_60'].mean():+.3f}%）")

    # 同日橫斷面分位
    cols = [c for c in FOCUS if c in df.columns and c not in BINARY]
    for c in cols:
        r = df.groupby("date")[c].rank(pct=True)
        df[c + "_d"] = pd.qcut(r.rank(method="first"), N_BINS,
                               labels=list(range(1, N_BINS + 1)))

    rows = []

    # ── A. 命中率 vs 超額報酬 ────────────────────────────────────────
    print("\n" + "=" * 96)
    print("  A. 同一批分位:命中率高，超額報酬跟得上嗎?（只列最高分位 D10 與最低 D1）")
    print("=" * 96)
    print(f"  {'特徵':<20}{'格':<5}{'n':>8}{'命中率':>8}{'lift':>7}"
          f"{'超額平均':>10}{'95% CI':>20}{'中位數':>9}{'勝率':>7}")
    print("  " + "-" * 92)
    for c in FOCUS:
        if c in BINARY:
            groups = [(f"={int(v)}", df[df[c] == v]) for v in (1,) if (df[c] == v).any()]
        else:
            groups = [("D10", df[df[c + "_d"] == N_BINS]), ("D1", df[df[c + "_d"] == 1])]
        for name, g in groups:
            if len(g) < 50:
                continue
            s = stats(g, base)
            s.update({"section": "A", "feature": c, "bucket": name})
            rows.append(s)
            ci = "[{:+.2f}%, {:+.2f}%]".format(s["excess_ci_lo_pct"], s["excess_ci_hi_pct"])
            print(f"  {c:<20}{name:<5}{s['n']:>8,}{s['rate_pct']:>7.2f}%{s['lift']:>7.2f}"
                  f"{s['excess_mean_pct']:>9.2f}%{ci:>20}"
                  f"{s['excess_median_pct']:>8.2f}%{s['excess_winrate_pct']:>6.1f}%")

    # ── B. 波動的機械效果 ────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("  B. 波動分位 (atr14_pct) 由低到高:命中率與超額報酬的走向")
    print("=" * 96)
    print(f"  {'波動分位':<10}{'n':>9}{'命中率':>9}{'lift':>7}{'超額平均':>11}"
          f"{'超額中位數':>12}{'勝率':>8}")
    print("  " + "-" * 66)
    for d in range(1, N_BINS + 1):
        g = df[df["atr14_pct_d"] == d]
        if len(g) < 50:
            continue
        s = stats(g, base)
        s.update({"section": "B", "feature": "atr14_pct", "bucket": f"D{d}"})
        rows.append(s)
        print(f"  D{d:<9}{s['n']:>9,}{s['rate_pct']:>8.2f}%{s['lift']:>7.2f}"
              f"{s['excess_mean_pct']:>10.2f}%{s['excess_median_pct']:>11.2f}%"
              f"{s['excess_winrate_pct']:>7.1f}%")

    # ── C. 條件分析:固定波動後，動能還有效嗎 ────────────────────────
    print("\n" + "=" * 96)
    print("  C. 雙重排序:每個波動分位「之內」，60 日報酬最高 vs 最低分位的差異")
    print("=" * 96)
    print(f"  {'波動分位':<10}{'ret60 格':<10}{'n':>9}{'命中率':>9}{'格內lift':>10}"
          f"{'超額平均':>11}{'超額中位數':>12}")
    print("  " + "-" * 72)
    for d in range(1, N_BINS + 1):
        vol_g = df[df["atr14_pct_d"] == d]
        if len(vol_g) < 500:
            continue
        inner_base = vol_g["flyer_100"].mean()
        for name, sel in (("ret60 D10", vol_g[vol_g["ret_60_d"] == N_BINS]),
                          ("ret60 D1", vol_g[vol_g["ret_60_d"] == 1])):
            if len(sel) < 50:
                continue
            s = stats(sel, inner_base if inner_base > 0 else base)
            s.update({"section": "C", "feature": f"atr_D{d}", "bucket": name})
            rows.append(s)
            print(f"  D{d:<9}{name:<10}{s['n']:>9,}{s['rate_pct']:>8.2f}%"
                  f"{s['lift']:>10.2f}{s['excess_mean_pct']:>10.2f}%"
                  f"{s['excess_median_pct']:>11.2f}%")
        print("  " + "·" * 72)

    os.makedirs("output", exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n完整結果: {OUT}")
    print("\n判讀提示:")
    print("  - A 段命中率高但超額平均 ~0 或為負 → 該特徵是標籤造成的錯覺")
    print("  - B 段命中率隨波動單調上升，但超額報酬平坦 → 證實機械效果")
    print("  - C 段固定波動後 lift 塌掉 → 動能沒有獨立於波動的預測力")
    print("  - 超額平均為正但中位數為負 → 靠少數大贏家，不是普遍現象")


if __name__ == "__main__":
    main()
