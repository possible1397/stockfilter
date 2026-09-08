"""
research/incremental.py — 增量分析:營收是不是多餘的?

階段 2 的結果是「月營收有訊號，但與價量特徵同一量級」。
但「相當」不等於「冗餘」——

  rev_high_12m（營收創 12 月新高）超額 +1.89%
  pct_from_52w_high D10（股價接近 52 週高）超額 +2.66%

這兩個有可能在測同一件事（營收創新高的公司，股價本來就容易在高點附近），
也有可能是兩件獨立的事，那組合起來會更強。

沒測就下結論太草率，而階段 3 的規則最多 3 個條件，
要不要分一個給營收，取決於這個答案。

做三件事（全部在探索期，不動保留期）:
  A. 重疊度:兩個條件同時成立的比例，對照獨立情況下的期望值
  B. 2x2 表:四種組合各自的 n、命中率、超額報酬 —— 最直接的答案
  C. 候選組合:對預先登記的候選池做幾個 2~3 條件的組合，看樣本數還夠不夠

用法: python research/incremental.py
"""
import os
import sys

import numpy as np
import pandas as pd

FEATURES = os.path.join("research", "data", "features_v2.parquet")
LABELS = os.path.join("research", "data", "labels.parquet")
OUT = os.path.join("output", "incremental_v1.csv")
EXPLORE = ("2021-09-01", "2024-12-31")


def stats(g, base):
    n = len(g)
    if n == 0:
        return None
    k = float(g["flyer_100"].sum())
    ex = g["excess_60"].dropna()
    m = ex.mean() if len(ex) else np.nan
    se = ex.std(ddof=1) / np.sqrt(len(ex)) if len(ex) > 1 else np.nan
    return {
        "n": n, "rate_pct": 100 * k / n, "lift": (k / n) / base,
        "excess_pct": 100 * m,
        "lo": 100 * (m - 1.96 * se) if np.isfinite(se) else np.nan,
        "hi": 100 * (m + 1.96 * se) if np.isfinite(se) else np.nan,
        "median_pct": 100 * ex.median() if len(ex) else np.nan,
        "win_pct": 100 * (ex > 0).mean() if len(ex) else np.nan,
    }


def line(label, s):
    if s is None:
        print(f"  {label:<34} 無樣本")
        return
    ci = "[{:+.2f}%, {:+.2f}%]".format(s["lo"], s["hi"])
    print(f"  {label:<34}{s['n']:>8,}{s['rate_pct']:>7.2f}%{s['lift']:>7.2f}"
          f"{s['excess_pct']:>9.2f}%{ci:>20}{s['median_pct']:>8.2f}%{s['win_pct']:>6.1f}%")


HEAD = (f"  {'組合':<34}{'n':>8}{'命中率':>8}{'lift':>7}"
        f"{'超額平均':>10}{'超額95%CI':>20}{'中位數':>9}{'勝率':>7}")


def main():
    print("載入資料...")
    feat = pd.read_parquet(FEATURES)
    lab = pd.read_parquet(LABELS, columns=["ticker", "date", "flyer_100", "fwd_ret_60"])
    df = feat.merge(lab, on=["ticker", "date"], how="inner")
    df = df[(df["date"] >= EXPLORE[0]) & (df["date"] <= EXPLORE[1])]
    df = df.dropna(subset=["flyer_100"])
    df["excess_60"] = (df["fwd_ret_60"]
                       - df.groupby("date")["fwd_ret_60"].transform("mean"))
    base = df["flyer_100"].mean()
    print(f"  {len(df):,} 列 ｜ flyer_100 基準率 {100*base:.3f}%")

    # 同日橫斷面分位
    for c in ("pct_from_52w_high", "rev_yoy", "atr_ratio_14_60", "sma50_sma200"):
        df[c + "_d"] = df.groupby("date")[c].rank(pct=True)

    hi52 = df["pct_from_52w_high_d"] >= 0.9          # 距 52 週高點最高一成
    revhi = df["rev_high_12m"] == 1                   # 營收創 12 月新高
    revyoy = df["rev_yoy_d"] >= 0.9                   # 營收年增率最高一成
    atrx = df["atr_ratio_14_60_d"] >= 0.9             # 波動擴張
    bo = df["is_breakout"] == 1

    # 只在「營收資料存在」的子集上比較，否則 2x2 會被缺值汙染
    has_rev = df["rev_high_12m"].notna()
    sub = df[has_rev]
    b2 = sub["flyer_100"].mean()
    print(f"  有營收資料的子集 {len(sub):,} 列（{100*len(sub)/len(df):.0f}%），"
          f"基準率 {100*b2:.3f}%")

    rows = []

    # ── A. 重疊度 ────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("  A. 兩個條件的重疊度")
    print("=" * 96)
    h = (sub["pct_from_52w_high_d"] >= 0.9)
    r = (sub["rev_high_12m"] == 1)
    p_h, p_r = h.mean(), r.mean()
    p_both = (h & r).mean()
    print(f"  股價接近 52 週高（D10）        {100*p_h:.1f}%")
    print(f"  營收創 12 月新高               {100*p_r:.1f}%")
    print(f"  兩者同時成立                   {100*p_both:.1f}%")
    print(f"  若兩者獨立，期望值應為         {100*p_h*p_r:.1f}%")
    ratio = p_both / (p_h * p_r) if p_h * p_r > 0 else np.nan
    print(f"  實際 / 獨立期望 = {ratio:.2f}"
          f"   →  {'高度重疊，可能在測同一件事' if ratio > 1.5 else '重疊有限，比較像兩件事'}")

    # ── B. 2x2 ──────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("  B. 2x2:股價位置 × 營收創新高（只用有營收資料的子集）")
    print("=" * 96)
    print(HEAD)
    print("  " + "-" * 92)
    for lbl, mask in [
        ("兩者皆非", ~h & ~r),
        ("只有營收創新高", ~h & r),
        ("只有股價近高點", h & ~r),
        ("兩者皆是", h & r),
    ]:
        s = stats(sub[mask], b2)
        line(lbl, s)
        if s:
            rows.append({"section": "B", "combo": lbl, **s})

    print("\n  解讀:若「兩者皆是」明顯高於「只有股價近高點」，營收就是增量訊號;")
    print("        若兩者差不多，營收多半是冗餘的，階段 3 不必為它花一個條件。")

    # ── 同樣的檢驗:營收年增率 ────────────────────────────────────
    print("\n" + "=" * 96)
    print("  B2. 2x2:股價位置 × 營收年增率最高一成")
    print("=" * 96)
    print(HEAD)
    print("  " + "-" * 92)
    ry = (sub["rev_yoy_d"] >= 0.9)
    for lbl, mask in [
        ("兩者皆非", ~h & ~ry),
        ("只有營收年增高", ~h & ry),
        ("只有股價近高點", h & ~ry),
        ("兩者皆是", h & ry),
    ]:
        s = stats(sub[mask], b2)
        line(lbl, s)
        if s:
            rows.append({"section": "B2", "combo": lbl, **s})

    # ── C. 候選組合 ──────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("  C. 候選組合（全樣本，非只有營收子集）—— 注意樣本數")
    print("=" * 96)
    print(HEAD)
    print("  " + "-" * 92)
    combos = [
        ("① 股價近高點", hi52),
        ("② 股價近高點 + 波動擴張", hi52 & atrx),
        ("③ 股價近高點 + 突破", hi52 & bo),
        ("④ 突破（單獨）", bo),
        ("⑤ 股價近高點 + 營收創新高", hi52 & revhi),
        ("⑥ 股價近高點 + 營收年增高", hi52 & revyoy),
        ("⑦ 股價近高點 + 波動擴張 + 營收創新高", hi52 & atrx & revhi),
        ("⑧ 股價近高點 + 波動擴張 + 突破", hi52 & atrx & bo),
    ]
    for lbl, mask in combos:
        s = stats(df[mask], base)
        line(lbl, s)
        if s:
            rows.append({"section": "C", "combo": lbl, **s})

    print("\n  提醒:條件疊越多樣本越少。階段 3 的門檻是保留期觸發 >= 200 次，")
    print("        探索期樣本若已經只剩幾百，保留期會不夠用。")

    os.makedirs("output", exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n完整結果: {OUT}")


if __name__ == "__main__":
    main()
