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

# 0/1 特徵。切十分位對二元變數沒有意義（D1~D8 會全是同一群），
# 要用值本身分組。
BINARY = {"is_breakout", "is_converging", "rev_high_12m"}
# 非特徵欄位。fwd_ret_60 是前瞻資料（放進來只為了算超額報酬基準），
# excess_60 是評估指標 —— 兩者都絕不能當成特徵，也絕不能被分位化覆寫。
SKIP = {"ticker", "date", "fwd_ret_60", "excess_60"}
LABEL_COLS = ["flyer_100", "flyer_50", "flyer_120"]
EXCESS = "excess_60"      # 可交易母體內的 60 日超額報酬
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
    cols = [feat, label] + ([EXCESS] if EXCESS in df.columns else [])
    sub = df[cols].dropna(subset=[feat, label])
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
        ex = g[EXCESS].dropna() if EXCESS in g else pd.Series(dtype=float)
        ex_m = ex.mean() if len(ex) else np.nan
        ex_se = ex.std(ddof=1) / np.sqrt(len(ex)) if len(ex) > 1 else np.nan
        rows.append({
            "feature": feat, "label": label, "bucket": name,
            "n": n, "hits": k,
            "rate_pct": round(100 * rate, 4),
            "lift": round(rate / base, 3) if base > 0 else np.nan,
            "ci_lo_pct": round(100 * lo, 4), "ci_hi_pct": round(100 * hi, 4),
            "lift_ci_lo": round(lo / base, 3) if base > 0 else np.nan,
            "lift_ci_hi": round(hi / base, 3) if base > 0 else np.nan,
            "excess_mean_pct": round(100 * ex_m, 4) if np.isfinite(ex_m) else np.nan,
            "excess_lo_pct": round(100 * (ex_m - 1.96 * ex_se), 4) if np.isfinite(ex_se) else np.nan,
            "excess_hi_pct": round(100 * (ex_m + 1.96 * ex_se), 4) if np.isfinite(ex_se) else np.nan,
            "excess_median_pct": round(100 * ex.median(), 4) if len(ex) else np.nan,
            "excess_winrate_pct": round(100 * (ex > 0).mean(), 2) if len(ex) else np.nan,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true",
                    help="改跑保留期（階段 3 專用，只能跑一次）")
    ap.add_argument("--v2", action="store_true",
                    help="改讀 features_v2.parquet（含月營收特徵）")
    args = ap.parse_args()

    global FEATURES, OUT
    if args.v2:
        FEATURES = os.path.join("research", "data", "features_v2.parquet")
        OUT = os.path.join("output", "lift_table_v2.csv")

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
    lab = pd.read_parquet(LABELS,
                          columns=["ticker", "date", "fwd_ret_60"] + LABEL_COLS)
    df = feat.merge(lab, on=["ticker", "date"], how="inner")
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    # 超額報酬的基準用「可交易母體」（已過流動性門檻），不是全市場
    df[EXCESS] = (df["fwd_ret_60"]
                  - df.groupby("date")["fwd_ret_60"].transform("mean"))
    print(f"  {len(df):,} 列 / {df.ticker.nunique():,} 檔 / {df.date.nunique():,} 個交易日")

    # 橫斷面分位（同一天內比較）
    cols = [c for c in df.columns if c not in SKIP and c not in LABEL_COLS]
    leak = [c for c in cols if c.startswith("fwd_") or c.startswith("excess")]
    assert not leak, f"特徵清單混入前瞻欄位: {leak}"
    print(f"計算同日橫斷面分位（{len(cols)} 個特徵）...")
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
    # 一個特徵要算數，必須「同時」提高命中率與期望報酬。
    # 只提高命中率的（例如高波動）是標籤造成的陷阱，不是 alpha。
    strong = main_lb[(main_lb["lift_ci_lo"] > 1.0) &
                     (main_lb["excess_lo_pct"] > 0)].sort_values(
                         "excess_mean_pct", ascending=False)
    print("\n" + "=" * 96)
    print("  候選:lift 信賴區間下界 > 1 且 超額報酬信賴區間下界 > 0（兩者都要過）")
    print("  依超額報酬排序 —— 命中率高但不賺錢的不算數")
    print("=" * 96)
    if strong.empty:
        print("  沒有任何一格同時通過 —— 這本身就是結論。")
    else:
        print(f"  {'特徵':<22}{'格':<6}{'n':>8}{'命中率':>8}{'lift':>7}"
              f"{'超額平均':>10}{'超額95%CI':>20}{'中位數':>9}{'勝率':>7}")
        for _, r in strong.head(25).iterrows():
            ci = "[{:+.2f}%, {:+.2f}%]".format(r.excess_lo_pct, r.excess_hi_pct)
            print(f"  {r.feature:<22}{r.bucket:<6}{r.n:>8,}{r.rate_pct:>7.2f}%"
                  f"{r.lift:>7.2f}{r.excess_mean_pct:>9.2f}%{ci:>20}"
                  f"{r.excess_median_pct:>8.2f}%{r.excess_winrate_pct:>6.1f}%")
        print(f"\n  共 {len(strong)} 格。提醒:20 個特徵 x 10 分位 x 3 標籤 = 600 次檢定，")
        print("  探索期的任何發現都只是候選，真偽由保留期決定（階段 3）。")

    # 對照:只看命中率會挑出哪些（示範標籤陷阱）
    trap = main_lb[(main_lb["lift_ci_lo"] > 1.5) &
                   (main_lb["excess_hi_pct"] < 0)].sort_values("lift", ascending=False)
    if not trap.empty:
        print("\n" + "-" * 96)
        print("  標籤陷阱:命中率顯著高、但超額報酬顯著為負的分位格")
        print("-" * 96)
        for _, r in trap.head(12).iterrows():
            print(f"  {r.feature:<22}{r.bucket:<6}{r.n:>8,}  lift {r.lift:>5.2f}"
                  f"   超額 {r.excess_mean_pct:>+6.2f}%   中位數 {r.excess_median_pct:>+6.2f}%")

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
