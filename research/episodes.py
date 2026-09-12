"""
research/episodes.py — 事件級分析:修正重疊觀察值造成的樣本數高估

增量分析得到「股價近 52 週高 + 營收年增最高一成」超額 +4.56%，n=6,372。
但那 6,372 是**日觀察值**，不是 6,372 個獨立事件 —— 同一檔股票會連續
好幾週都符合條件，每一天都被算成一筆。有效樣本遠小於 n，信賴區間被低估。

而且這也不符合實際操作:你是在它「首次符合條件」那天進場，
不是每天都買一次。

本檔把連續符合的日子收斂成「事件」(episode):
  - 同一檔股票，符合條件的日子之間相隔 <= GAP 個交易日，算同一個事件
  - 取每個事件的**第一天**當作訊號日
  - 用這些第一天重算統計 —— 這才是可交易的獨立樣本

用法: python research/episodes.py
"""
import os
import sys

import numpy as np
import pandas as pd

FEATURES = os.path.join("research", "data", "features_v2.parquet")
LABELS = os.path.join("research", "data", "labels.parquet")
OUT = os.path.join("output", "episodes_v1.csv")
EXPLORE = ("2021-09-01", "2024-12-31")
HOLDOUT = ("2025-01-01", "2026-05-31")
GAP = 5          # 相隔 <= 5 個交易日視為同一個事件


def episodes(df, mask, gap=GAP):
    """回傳每個事件第一天的列。"""
    sel = df[mask].sort_values(["ticker", "date"])
    if sel.empty:
        return sel
    d = pd.to_datetime(sel["date"])
    same = sel["ticker"].eq(sel["ticker"].shift(1))
    近 = (d - d.shift(1)).dt.days.le(gap * 2)     # 曆日寬鬆換算交易日
    cont = same & 近
    sel = sel.assign(_first=~cont)
    return sel[sel["_first"]]


def stats(g, base, label, col="flyer_100"):
    n = len(g)
    if n == 0:
        return None
    k = float(g[col].sum())
    ex = g["excess_60"].dropna()
    m = ex.mean() if len(ex) else np.nan
    se = ex.std(ddof=1) / np.sqrt(len(ex)) if len(ex) > 1 else np.nan
    return {
        "combo": label, "n": n,
        "rate_pct": 100 * k / n, "lift": (k / n) / base,
        "excess_pct": 100 * m,
        "lo": 100 * (m - 1.96 * se) if np.isfinite(se) else np.nan,
        "hi": 100 * (m + 1.96 * se) if np.isfinite(se) else np.nan,
        "median_pct": 100 * ex.median() if len(ex) else np.nan,
        "win_pct": 100 * (ex > 0).mean() if len(ex) else np.nan,
    }


def line(s, tag=""):
    if s is None:
        return
    ci = "[{:+.2f}%, {:+.2f}%]".format(s["lo"], s["hi"])
    print(f"  {s['combo']:<32}{tag:<6}{s['n']:>8,}{s['rate_pct']:>7.2f}%"
          f"{s['lift']:>7.2f}{s['excess_pct']:>9.2f}%{ci:>20}"
          f"{s['median_pct']:>8.2f}%{s['win_pct']:>6.1f}%")


HEAD = (f"  {'組合':<32}{'層級':<6}{'n':>8}{'命中率':>8}{'lift':>7}"
        f"{'超額平均':>10}{'超額95%CI':>20}{'中位數':>9}{'勝率':>7}")


def main():
    # --holdout 只改動期間，其餘邏輯完全不動（階段 3 一次性驗證用）
    hold = "--holdout" in sys.argv
    period = HOLDOUT if hold else EXPLORE
    global OUT
    if hold:
        OUT = os.path.join("output", "episodes_holdout.csv")
    print(f"載入資料...（{'保留期' if hold else '探索期'} {period[0]} ~ {period[1]}）")
    feat = pd.read_parquet(FEATURES)
    lab = pd.read_parquet(LABELS, columns=["ticker", "date", "flyer_100", "flyer_50",
                                           "flyer_120", "fwd_ret_60"])
    df = feat.merge(lab, on=["ticker", "date"], how="inner")
    df = df[(df["date"] >= period[0]) & (df["date"] <= period[1])]
    df = df.dropna(subset=["flyer_100"])
    df["excess_60"] = (df["fwd_ret_60"]
                       - df.groupby("date")["fwd_ret_60"].transform("mean"))
    base = df["flyer_100"].mean()
    print(f"  {len(df):,} 列 ｜ 基準率 {100*base:.3f}%")

    for c in ("pct_from_52w_high", "rev_yoy", "atr_ratio_14_60"):
        df[c + "_d"] = df.groupby("date")[c].rank(pct=True)

    hi52 = df["pct_from_52w_high_d"] >= 0.9
    revyoy = df["rev_yoy_d"] >= 0.9
    revhi = df["rev_high_12m"] == 1
    atrx = df["atr_ratio_14_60_d"] >= 0.9

    combos = [
        ("股價近高點", hi52),
        ("股價近高點 + 波動擴張", hi52 & atrx),
        ("股價近高點 + 營收創新高", hi52 & revhi),
        ("股價近高點 + 營收年增高", hi52 & revyoy),
        ("營收年增高（單獨）", revyoy),
    ]

    print("\n" + "=" * 104)
    print("  日觀察值 vs 事件級（每段連續符合只取第一天）")
    print("=" * 104)
    print(HEAD)
    print("  " + "-" * 100)

    rows = []
    for lbl, mask in combos:
        s_day = stats(df[mask], base, lbl)
        ep = episodes(df, mask)
        s_ep = stats(ep, base, lbl)
        line(s_day, "日")
        line(s_ep, "事件")
        if s_day:
            rows.append({"level": "day", **s_day})
        if s_ep:
            rows.append({"level": "episode", **s_ep})
            # 每年幾次訊號 —— 決定這個策略實際上能不能操作
            years = (pd.to_datetime(df["date"].max())
                     - pd.to_datetime(df["date"].min())).days / 365.25
            print(f"  {'':<32}{'':6}→ 平均每年 {s_ep['n']/years:,.0f} 次訊號，"
                  f"日觀察值 / 事件 = {s_day['n']/s_ep['n']:.1f} 倍")
        print("  " + "·" * 100)

    # ── 三個標籤的事件級 lift ────────────────────────────────────
    # 主目標的門檻之一是「換第二種標籤（flyer_120）仍 >= 1.5」，
    # 這是判定階段 3 能不能過關的必要數字。
    print("\n" + "=" * 104)
    print("  事件級 · 三個標籤的 lift（主目標門檻:flyer_100 >= 2.0 且 flyer_120 >= 1.5）")
    print("=" * 104)
    print(f"  {'組合':<32}{'事件數':>8}"
          f"{'flyer_100':>12}{'flyer_50':>11}{'flyer_120':>12}")
    print("  " + "-" * 100)
    bases = {c: df[c].dropna().mean() for c in ("flyer_100", "flyer_50", "flyer_120")}
    for lbl, mask in combos:
        ep = episodes(df, mask)
        if ep.empty:
            continue
        cells = []
        for c in ("flyer_100", "flyer_50", "flyer_120"):
            s = stats(ep, bases[c], lbl, col=c)
            cells.append(f"{s['lift']:.2f}" if s else "—")
        n_ep = len(ep)
        mark = ""
        try:
            if float(cells[0]) >= 2.0 and float(cells[2]) >= 1.5:
                mark = "  ← 兩門檻皆過"
        except ValueError:
            pass
        print(f"  {lbl:<32}{n_ep:>8,}{cells[0]:>12}{cells[1]:>11}{cells[2]:>12}{mark}")
    print(f"\n  基準率:flyer_100 {100*bases['flyer_100']:.3f}%  "
          f"flyer_50 {100*bases['flyer_50']:.3f}%  flyer_120 {100*bases['flyer_120']:.3f}%")

    os.makedirs("output", exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n完整結果: {OUT}")
    print("\n判讀:")
    print("  - 事件級的 n 才是獨立樣本數，信賴區間才誠實")
    print("  - 若事件級的超額報酬與日級接近，代表結論穩健")
    print("  - 若事件級大幅衰減，代表原本的數字是被重複計算撐起來的")
    print("  - 每年訊號次數決定可操作性:太少無法分散，太多超出資金")


if __name__ == "__main__":
    main()
