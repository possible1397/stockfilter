"""
research/lift.py — 特徵分層與 lift 表

問的不是「飆股有什麼共同點」，而是「長這樣的股票裡，後來飆起來的佔幾成，
比全市場基準高幾倍」。差別在於後者一定要同時看沒飆的那些。

- 分位以「同一天的橫斷面」計算（同一天所有股票比較），不是跨時間的池化分位。
  這樣自然控制掉多空頭市場的差異 —— 問的是選股，不是擇時。
- 每一格都報 n、命中率、lift、Wilson 95% 信賴區間。任何 lift 都不單獨呈現。
- 只跑探索期。保留期在階段 3 才動，而且只動一次。

用法:
    python research/lift.py                    # 探索期
    python research/lift.py --holdout          # 保留期（階段 3 才用，會二次確認）
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

FEATURES = os.path.join("research", "data", "features.parquet")
LABELS = os.path.join("research", "data", "labels.parquet")
OUT = os.path.join("output", "lift_table_v1.csv")

EXPLORE = ("2021-09-01", "2024-12-31")
HOLDOUT = ("2025-01-01", "2026-05-31")

BINARY = {"is_breakout", "is_converging"}
SKIP = {"ticker", "date"}
LABEL_COLS = ["flyer_100", "flyer_50", "flyer_120"]
N_BINS = 10


def wilson(k, n, z=1.96):
    """Wilson 95% 信賴區間 —— 小樣本、極端比例下比常態近似可靠得多。"""
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def analyse(df, feat, label, base):
    rows = []
    sub = df[[feat, label]].dropna()
    if len(sub) < 100:
        return rows

    if feat in BINARY:
        groups = [(f"={int(v)}", sub[sub[feat] == v]) for v in sorted(sub[feat].unique())]
    else:
        try:
            q = pd.qcut(sub[feat].rank(method="first"), N_BINS,
                        labels=[f"D{i+1}" for i in range(N_BINS)])
        except ValueError:
            return rows
        groups = [(str(g), sub[q == g]) for g in q.cat.categories]

    for name, g in groups:
        n = len(g)
        if n == 0:
            continue
        k = int(g[label].sum())
        rate = k / n
        lo, hi = wilson(k, n)
        rows.append({
            "feature": feat, "label": label, "bucket": name,
            "n": n, "hits": k,
            "rate_pct": round(100 * rate, 4),
            "lift": round(rate / base, 3) if base > 0 else np.nan,
            "ci_lo_pct": round(100 * lo, 4), "ci_hi_pct": round(100 * hi, 4),
            "lift_ci_lo": round(lo / base, 3) if base > 0 else np.nan,
            "lift_ci_hi": round(hi / base, 3) if base > 0 else np.nan,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true",
                    help="改跑保留期（階段 3 專用，只能跑一次）")
    args = ap.parse_args()

    if args.holdout:
        print("\n" + "!" * 72)
        print("  你正要動保留期資料。依紀律規則，保留期只能看一次，看完就凍結結論。")
        print("  確定的話，在下一行輸入 YES（大寫）:")
        print("!" * 72)
        if input("> ").strip() != "YES":
            print("已取消。")
            return
        start, end, tag = *HOLDOUT, "holdout"
    else:
        start, end, tag = *EXPLORE, "explore"

    for p in (FEATURES, LABELS):
        if not os.path.exists(p):
            print(f"找不到 {p}，請先跑 labels.py / features.py")
            return

    print(f"載入資料（{tag}: {start} ~ {end}）...")
    feat = pd.read_parquet(FEATURES)
    lab = pd.read_parquet(LABELS, columns=["ticker", "date"] + LABEL_COLS)
    df = feat.merge(lab, on=["ticker", "date"], how="inner")
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    print(f"  {len(df):,} 列 / {df.ticker.nunique():,} 檔 / {df.date.nunique():,} 個交易日")

    # 橫斷面分位（同一天內比較）
    cols = [c for c in df.columns if c not in SKIP and c not in LABEL_COLS]
    print("計算同日橫斷面分位...")
    for c in cols:
        if c in BINARY:
            continue
        df[c] = df.groupby("date")[c].rank(pct=True)

    print("\n基準率（同期全市場）:")
    bases = {}
    for lb in LABEL_COLS:
        s = df[lb].dropna()
        bases[lb] = s.mean() if len(s) else np.nan
        print(f"  {lb:<10} 有效 {len(s):>9,} 列   命中 {int(s.sum()):>7,}   "
              f"基準率 {100*bases[lb]:.3f}%")

    rows = []
    for c in cols:
        for lb in LABEL_COLS:
            rows += analyse(df, c, lb, bases[lb])
    res = pd.DataFrame(rows)

    os.makedirs("output", exist_ok=True)
    out = OUT.replace("_v1.csv", f"_v1_{tag}.csv")
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n完整結果已輸出: {out}  ({len(res)} 列)")

    # --- 摘要:主標籤下，信賴區間下界仍 > 1 的格子 ---
    main_lb = res[res["label"] == "flyer_100"].copy()
    strong = main_lb[main_lb["lift_ci_lo"] > 1.0].sort_values("lift", ascending=False)
    print("\n" + "=" * 78)
    print("  flyer_100（三個月翻倍）· lift 信賴區間下界 > 1 的分位格，依 lift 排序")
    print("=" * 78)
    if strong.empty:
        print("  沒有任何一格的信賴區間下界超過 1 —— 這本身就是結論。")
    else:
        print(f"  {'特徵':<22}{'格':<6}{'n':>8}{'命中':>7}{'命中率':>9}"
              f"{'lift':>7}{'lift 95%CI':>18}")
        for _, r in strong.head(25).iterrows():
            print(f"  {r.feature:<22}{r.bucket:<6}{r.n:>8,}{r.hits:>7,}"
                  f"{r.rate_pct:>8.2f}%{r.lift:>7.2f}"
                  f"{f'[{r.lift_ci_lo:.2f}, {r.lift_ci_hi:.2f}]':>18}")
        print(f"\n  共 {len(strong)} 格。提醒:20 個特徵 x 10 分位 x 3 標籤 = 600 次檢定，")
        print("  探索期的任何發現都只是候選，真偽由保留期決定（階段 3）。")

    # 主標籤與密度標籤是否指向同一批特徵
    print("\n" + "-" * 78)
    print("  交叉檢查:同一格在 flyer_50 上的表現")
    print("-" * 78)
    d50 = res[res["label"] == "flyer_50"].set_index(["feature", "bucket"])["lift"]
    both = 0
    for _, r in strong.head(15).iterrows():
        l50 = d50.get((r.feature, r.bucket), np.nan)
        agree = "一致" if l50 > 1.2 else ("不一致" if l50 < 1.0 else "偏弱")
        both += agree == "一致"
        print(f"  {r.feature:<22}{r.bucket:<6}"
              f"flyer_100 lift {r.lift:>5.2f}   flyer_50 lift {l50:>5.2f}   {agree}")
    print(f"\n  兩個標籤一致的格數: {both} / {min(15, len(strong))}")


if __name__ == "__main__":
    main()
