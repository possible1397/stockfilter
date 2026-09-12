"""階段 4 執行腳本 —— 依 docs/階段4_排序規則_預先登記.md 的順序，不得調換。"""
import numpy as np, pandas as pd, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portfolio import (RANKERS, DESIGN, CHECK, SLOTS, COST, MAXHOLD,
                       trade_outcome, summarize)

ep = pd.read_csv("output/r1_episodes_ranked.csv")
paths = pd.read_csv("output/r1_paths_explore.csv")
PG = {k: v.sort_values("k") for k, v in paths.groupby(["ep_ticker", "ep_date"])}
ALLDAYS = {d: i for i, d in enumerate(sorted(paths.date.unique()))}


def outcomes(stop, trail):
    o = {}
    for key, p in PG.items():
        r = trade_outcome(p, stop, trail)
        if r is not None:
            o[key] = r
    return o


def sim(rank_col, asc, period, out, seed=None):
    sel = ep[(ep.date >= period[0]) & (ep.date <= period[1])].copy()
    if seed is not None:
        sel["_r"] = np.random.default_rng(seed).random(len(sel))
        rank_col, asc = "_r", False
    sel = sel.dropna(subset=[rank_col])
    busy, trades = [], []
    for d in sorted(sel.date.unique()):
        di = ALLDAYS.get(d)
        if di is None:
            continue
        busy = [x for x in busy if x > di]
        room = SLOTS - len(busy)
        if room <= 0:
            continue
        cand = sel[sel.date == d].sort_values(rank_col, ascending=asc)
        for r in cand.head(room).itertuples():
            res = out.get((r.ticker, r.date))
            if res is None:
                continue
            g, held = res
            trades.append({"date": d, "ret": g - COST, "held": held})
            busy.append(di + held)
    return pd.DataFrame(trades)


def baseline(period, out, n=200):
    rows = [summarize(sim(None, None, period, out, seed=s), period, "x")
            for s in range(n)]
    df = pd.DataFrame([r for r in rows if r.get("筆數", 0) > 0])
    return df.mean(numeric_only=True), df["年化%"].std()


def show(rows, title):
    print("\n" + "=" * 96); print("  " + title); print("=" * 96)
    d = pd.DataFrame(rows)
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].round(2)
    print(d.to_string(index=False))
    return d


if __name__ == "__main__":
    out60 = outcomes(None, None)
    print(f"可成交事件 {len(out60)}/{len(PG)}")
    for period, nm in ((DESIGN, "設計期"), (CHECK, "內部檢查期")):
        b, sd = baseline(period, out60)
        rows = [{"規則": "R-0 隨機（200 次平均）", "筆數": round(b["筆數"]),
                 "年化%": b["年化%"], "平均每筆%": b["平均每筆%"],
                 "中位數%": b["中位數%"], "勝率%": b["勝率%"],
                 "MDD%": b["MDD%"], "平均持有日": b["平均持有日"]}]
        for lbl, (col, asc) in RANKERS.items():
            rows.append(summarize(sim(col, asc, period, out60), period, lbl))
        d = show(rows, f"第一階段 · {nm} {period[0]} ~ {period[1]}（出場固定 60 天，已扣 0.6% 成本）")
        print(f"  隨機基準年化的標準差 = {sd:.2f}pp（判斷優勢是否只是運氣）")
        d.to_csv(f"output/stage4_rank_{nm}.csv", index=False, encoding="utf-8-sig")
    stage2()


def stage2():
    """第二階段:排序規則已判定無效 → 以 R-0 隨機選為基礎，只測出場規則。"""
    stops = [None, -0.10, -0.15, -0.20]
    trails = [None, -0.15, -0.20, -0.25]
    for period, nm in ((DESIGN, "設計期"), (CHECK, "內部檢查期")):
        rows = []
        for s in stops:
            for t in trails:
                out = outcomes(s, t)
                res = [summarize(sim(None, None, period, out, seed=k), period, "x")
                       for k in range(30)]
                df = pd.DataFrame([r for r in res if r.get("筆數", 0) > 0])
                m = df.mean(numeric_only=True)
                rows.append({"停損": "無" if s is None else f"{s:.0%}",
                             "移動停利": "無" if t is None else f"{t:.0%}",
                             "筆數": round(m["筆數"]), "年化%": m["年化%"],
                             "平均每筆%": m["平均每筆%"], "中位數%": m["中位數%"],
                             "勝率%": m["勝率%"], "MDD%": m["MDD%"],
                             "平均持有日": m["平均持有日"]})
        d = show(rows, f"第二階段 · {nm}（隨機選股 30 次平均，已扣 0.6% 成本）")
        d.to_csv(f"output/stage4_exit_{nm}.csv", index=False, encoding="utf-8-sig")
