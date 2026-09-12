"""
research/portfolio.py — 階段 4 投資組合模擬

規格見 docs/階段4_排序規則_預先登記.md（候選清單已凍結）。

用法:
  python research/portfolio.py --stage 1     # 選排序規則（出場固定 60 天）
  python research/portfolio.py --stage 2 --rank <欄位>   # 選出場規則
"""
import argparse
import os
import numpy as np
import pandas as pd

PATHS = os.path.join("output", "r1_paths_explore.csv")
DESIGN = ("2021-09-01", "2023-06-30")
CHECK = ("2023-07-01", "2024-12-31")
SLOTS = 20
COST = 0.006          # 來回手續費 + 證交稅
MAXHOLD = 60          # 交易日

RANKERS = {
    "R-a rev_yoy":            ("rev_yoy", False),
    "R-b pct_from_52w_high":  ("pct_from_52w_high", False),
    "R-c atr_ratio_14_60":    ("atr_ratio_14_60", False),
    "R-d turnover_20_log":    ("turnover_20_log", False),
    "R-e close_sma50":        ("close_sma50", False),
    "R-f rev_yoy_3m":         ("rev_yoy_3m", False),
    "R-g atr14_pct (低者優先)": ("atr14_pct", True),
}


def trade_outcome(path, stop=None, trail=None, maxhold=MAXHOLD):
    """回傳 (毛報酬率, 持有交易日數)。path 為該事件的 k=0..120 價格。"""
    seg = path[(path.k >= 1) & (path.k <= maxhold + 1)]
    if len(seg) < 2:
        return None
    entry = seg.iloc[0]["open"]
    if not np.isfinite(entry) or entry <= 0:
        return None
    peak = entry
    for j, row in enumerate(seg.itertuples(), start=1):
        lo, hi = row.low, row.high
        # 停損優先於移動停利（較保守）
        if stop is not None and lo <= entry * (1 + stop):
            return entry * (1 + stop) / entry - 1, j
        if trail is not None and peak > entry and lo <= peak * (1 + trail):
            return max(peak * (1 + trail), lo) / entry - 1, j
        peak = max(peak, hi)
    return seg.iloc[-1]["close"] / entry - 1, len(seg)


def simulate(ep, paths, rank_col, ascending, period, stop=None, trail=None,
             seed=None):
    sel = ep[(ep.date >= period[0]) & (ep.date <= period[1])].copy()
    if seed is not None:                      # R-0 隨機基準
        sel["_r"] = np.random.default_rng(seed).random(len(sel))
        rank_col, ascending = "_r", False
    days = sorted(sel.date.unique())
    free_at = []                              # 各持倉的結束序號
    trades = []
    day_index = {d: i for i, d in enumerate(sorted(paths.date.unique()))}
    for d in days:
        di = day_index.get(d)
        if di is None:
            continue
        free_at = [x for x in free_at if x > di]
        room = SLOTS - len(free_at)
        if room <= 0:
            continue
        cand = sel[sel.date == d].sort_values(rank_col, ascending=ascending)
        for r in cand.head(room).itertuples():
            p = paths[(paths.ep_ticker == r.ticker) & (paths.ep_date == r.date)]
            res = trade_outcome(p, stop, trail)
            if res is None:
                continue
            gross, held = res
            trades.append({"ticker": r.ticker, "date": d,
                           "ret": gross - COST, "held": held})
            free_at.append(di + held)
    return pd.DataFrame(trades)


def summarize(tr, period, label):
    if tr.empty:
        return {"規則": label, "筆數": 0}
    yrs = (pd.to_datetime(period[1]) - pd.to_datetime(period[0])).days / 365.25
    # 等權重、每位置 1/20，資金連續運用 → 以平均每筆報酬 x 週轉次數估年化
    turns = len(tr) / SLOTS / yrs          # 每個位置每年周轉幾次
    ann = (1 + tr.ret.mean()) ** turns - 1
    eq = (1 + tr.sort_values("date").ret / SLOTS).cumprod()
    mdd = (eq / eq.cummax() - 1).min()
    return {"規則": label, "筆數": len(tr),
            "年化%": 100 * ann, "平均每筆%": 100 * tr.ret.mean(),
            "中位數%": 100 * tr.ret.median(),
            "勝率%": 100 * (tr.ret > 0).mean(),
            "MDD%": 100 * mdd, "平均持有日": tr.held.mean()}
