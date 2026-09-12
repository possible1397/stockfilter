"""
scripts/r1_paper.py — R1 策略的每日模擬單（兩種出場方式並行）

為什麼要有這支:
  階段 3 已經驗證訊號規則 R1 有效，但階段 4 的出場參數（移動停利 -15%）
  是從 16 組候選裡挑出來的，沒有經過外部驗證，而保留期已經用完。
  唯一誠實的驗證方式是用**前瞻資料**:從今天開始記錄，時間到了再看。

  所以這支同時跑兩個投資組合，用完全相同的訊號:
    A_hold60   固定持有 60 個交易日（零擬合參數，R1 本身已驗證）
    B_trail15  移動停利 -15%（階段 4 選出來的，待驗證）
  幾個月後比較兩者，就知道移動停利是真的還是雜訊。

規格凍結於 docs/階段4_結果.md 第 8 節。本檔不得擅自修改參數。

用法:
  python scripts/r1_paper.py              # 每日執行（由 job runner 呼叫）
  python scripts/r1_paper.py --report     # 只看現況，不推進
"""
import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "stock_data.db")
LOG = os.path.join(ROOT, "output", "r1_paper.log")

# ── 凍結參數（見 docs/階段4_結果.md 第 8 節）────────────────────
SLOTS = 20
MIN_TURNOVER = 5_000_000      # 20 日均成交金額
TOP_PCT = 0.90                # 橫斷面前 10%
GAP = 5                       # 事件化:同一檔 <= 5 交易日視為同一事件
MAXHOLD = 60                  # 最長持有交易日
TRAIL = -0.15                 # B 的移動停利
COST = 0.006                  # 來回成本（僅用於報表，不影響進出場）
VARIANTS = {"A_hold60": None, "B_trail15": TRAIL}

DDL = """
CREATE TABLE IF NOT EXISTS r1_signals (
    date TEXT, ticker TEXT, close REAL,
    pct_from_52w_high REAL, rev_yoy REAL,
    PRIMARY KEY (date, ticker)
);
CREATE TABLE IF NOT EXISTS r1_positions (
    variant TEXT, ticker TEXT, entry_date TEXT,
    entry_px REAL, peak REAL, bars INTEGER,
    exit_date TEXT, exit_px REAL, gross REAL, net REAL,
    exit_reason TEXT, status TEXT,
    PRIMARY KEY (variant, ticker, entry_date)
);
CREATE TABLE IF NOT EXISTS r1_paper_runs (
    run_date TEXT PRIMARY KEY, last_price_date TEXT,
    n_signals INTEGER, n_opened INTEGER, n_closed INTEGER, note TEXT
);
"""


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = f"[r1_paper] {pd.Timestamp.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def trading_days(con, n=400):
    d = pd.read_sql("SELECT DISTINCT date FROM daily_prices "
                    "ORDER BY date DESC LIMIT ?", con, params=(n,))
    return sorted(d["date"].tolist())


def compute_signals(con, days):
    """回傳最後一個交易日的 R1 訊號（已做流動性過濾與事件化）。"""
    today = days[-1]
    start = days[max(0, len(days) - 260)]          # 52 週 + 緩衝
    px = pd.read_sql(
        "SELECT ticker,date,high,low,close,volume FROM daily_prices "
        "WHERE date >= ? AND ticker NOT LIKE '^%'", con, params=(start,))
    px = px.sort_values(["ticker", "date"])
    g = px.groupby("ticker", sort=False)
    px["turnover20"] = (px["close"] * px["volume"]).groupby(px["ticker"]).transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    px["high52"] = g["high"].transform(lambda s: s.rolling(250, min_periods=200).max())
    cur = px[px["date"] == today].copy()
    cur = cur[(cur["turnover20"] >= MIN_TURNOVER) & cur["high52"].notna()]
    cur["pct_from_52w_high"] = cur["close"] / cur["high52"] - 1

    rev = pd.read_sql("SELECT ticker,revenue_year,revenue_month,revenue,publish_date "
                      "FROM monthly_revenue", con)
    rev = rev.sort_values(["ticker", "revenue_year", "revenue_month"])
    rev["rev_yoy"] = rev.groupby("ticker")["revenue"].transform(
        lambda s: s.astype(float) / s.astype(float).shift(12) - 1)
    # point-in-time:只用今天之前已公布的
    rev = rev[(rev["publish_date"] <= today) & rev["rev_yoy"].notna()]
    latest = rev.sort_values("publish_date").groupby("ticker").tail(1)
    cur = cur.merge(latest[["ticker", "rev_yoy"]], on="ticker", how="inner")
    if cur.empty:
        return today, cur

    cur["hi_r"] = cur["pct_from_52w_high"].rank(pct=True)
    cur["rev_r"] = cur["rev_yoy"].rank(pct=True)
    sig = cur[(cur["hi_r"] >= TOP_PCT) & (cur["rev_r"] >= TOP_PCT)].copy()

    # 事件化:過去 GAP 個交易日內已經觸發過的，不算新事件
    prev = days[max(0, len(days) - 1 - GAP):-1]
    if prev:
        q = ("SELECT DISTINCT ticker FROM r1_signals WHERE date IN (%s)"
             % ",".join("?" * len(prev)))
        recent = {r[0] for r in con.execute(q, prev)}
        sig = sig[~sig["ticker"].isin(recent)]
    return today, sig


def advance(con, days):
    """用最新一天的價格推進所有未平倉部位。"""
    today = days[-1]
    bar = {d: i for i, d in enumerate(days)}
    open_pos = pd.read_sql("SELECT * FROM r1_positions WHERE status='open'", con)
    if open_pos.empty:
        return 0
    tks = sorted(open_pos["ticker"].unique())
    q = ("SELECT ticker,date,high,low,close FROM daily_prices "
         "WHERE date=? AND ticker IN (%s)" % ",".join("?" * len(tks)))
    px = pd.read_sql(q, con, params=[today] + tks).set_index("ticker")
    closed = 0
    for r in open_pos.itertuples():
        if r.ticker not in px.index:
            continue
        row = px.loc[r.ticker]
        peak = max(r.peak, float(row["high"]))
        bars = bar.get(today, 0) - bar.get(r.entry_date, 0)
        trail = VARIANTS[r.variant]
        exit_px = reason = None
        if trail is not None and peak > r.entry_px and row["low"] <= peak * (1 + trail):
            exit_px = max(peak * (1 + trail), float(row["low"]))
            reason = "移動停利"
        elif bars >= MAXHOLD:
            exit_px = float(row["close"])
            reason = "持有到期"
        if exit_px is None:
            con.execute("UPDATE r1_positions SET peak=?,bars=? "
                        "WHERE variant=? AND ticker=? AND entry_date=?",
                        (peak, bars, r.variant, r.ticker, r.entry_date))
        else:
            gross = exit_px / r.entry_px - 1
            con.execute(
                "UPDATE r1_positions SET peak=?,bars=?,exit_date=?,exit_px=?,"
                "gross=?,net=?,exit_reason=?,status='closed' "
                "WHERE variant=? AND ticker=? AND entry_date=?",
                (peak, bars, today, exit_px, gross, gross - COST, reason,
                 r.variant, r.ticker, r.entry_date))
            closed += 1
    return closed


def open_pending(con, days):
    """把「昨天的訊號」用今天的開盤價建倉（與研究一致:訊號次日開盤進場）。"""
    today = days[-1]
    if len(days) < 2:
        return 0
    yday = days[-2]
    sig = pd.read_sql("SELECT ticker FROM r1_signals WHERE date=?", con, params=(yday,))
    if sig.empty:
        return 0
    tks = sig["ticker"].tolist()
    q = ("SELECT ticker,open FROM daily_prices WHERE date=? AND ticker IN (%s)"
         % ",".join("?" * len(tks)))
    op = pd.read_sql(q, con, params=[today] + tks).set_index("ticker")["open"]
    opened = 0
    # 固定種子:同一天的排序可重現（排序規則已判定無效，故為隨機）
    rng = np.random.default_rng(int(today.replace("-", "")))
    order = rng.permutation(tks)
    for variant in VARIANTS:
        n_open = con.execute("SELECT COUNT(*) FROM r1_positions "
                             "WHERE variant=? AND status='open'",
                             (variant,)).fetchone()[0]
        room = SLOTS - n_open
        held = {r[0] for r in con.execute(
            "SELECT ticker FROM r1_positions WHERE variant=? AND status='open'",
            (variant,))}
        for tk in order:
            if room <= 0:
                break
            if tk in held or tk not in op.index:
                continue
            px = float(op[tk])
            if not np.isfinite(px) or px <= 0:
                continue
            con.execute("INSERT OR IGNORE INTO r1_positions VALUES "
                        "(?,?,?,?,?,0,NULL,NULL,NULL,NULL,NULL,'open')",
                        (variant, tk, today, px, px))
            room -= 1
            opened += 1
    return opened


def report(con):
    print("\n" + "=" * 88)
    print("  R1 模擬單現況")
    print("=" * 88)
    for variant in VARIANTS:
        o = pd.read_sql("SELECT * FROM r1_positions WHERE variant=? AND status='open'",
                        con, params=(variant,))
        c = pd.read_sql("SELECT * FROM r1_positions WHERE variant=? AND status='closed'",
                        con, params=(variant,))
        line = f"  {variant:<12} 持倉 {len(o):>2}/{SLOTS}  已平倉 {len(c):>4} 筆"
        if len(c):
            line += (f"  平均 {100*c.net.mean():+6.2f}%  中位數 {100*c.net.median():+6.2f}%"
                     f"  勝率 {100*(c.net>0).mean():5.1f}%"
                     f"  平均持有 {c.bars.mean():.0f} 天")
        print(line)
    n = con.execute("SELECT COUNT(*) FROM r1_signals").fetchone()[0]
    d = con.execute("SELECT MIN(date),MAX(date) FROM r1_signals").fetchone()
    print(f"\n  累積訊號 {n} 筆（{d[0]} ~ {d[1]}）")
    print("  ⚠ B_trail15 的 -15% 是階段 4 從 16 組裡挑出來的，尚未經外部驗證。")
    print("     要等兩邊各累積約 100 筆已平倉交易才有比較意義。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    if args.report:
        report(con)
        return 0
    days = trading_days(con)
    if len(days) < 2:
        log("交易日不足")
        return 1
    today = days[-1]
    done = con.execute("SELECT 1 FROM r1_paper_runs WHERE last_price_date=?",
                       (today,)).fetchone()
    if done:
        log(f"{today} 已處理過，略過。")
        con.close()
        return 0

    closed = advance(con, days)
    opened = open_pending(con, days)
    _, sig = compute_signals(con, days)
    for r in sig.itertuples():
        con.execute("INSERT OR REPLACE INTO r1_signals VALUES (?,?,?,?,?)",
                    (today, r.ticker, float(r.close),
                     float(r.pct_from_52w_high), float(r.rev_yoy)))
    con.execute("INSERT OR REPLACE INTO r1_paper_runs VALUES (?,?,?,?,?,?)",
                (str(pd.Timestamp.now().date()), today, len(sig), opened, closed, ""))
    con.commit()
    log(f"價格日 {today}｜新訊號 {len(sig)}｜建倉 {opened}｜平倉 {closed}")
    report(con)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
