"""
research/fetch_revenue.py — 抓月營收（FinMind 免費層，可續跑）

規格見 docs/飆股解剖_特徵清單_v2_月營收.md。

實測結果:免費層不支援批次（不指定 data_id 會回 HTTP 400），
只能逐檔抓，平均 0.8 秒/檔，1,900 檔約 25 分鐘。
免費層有配額，所以本腳本設計成**可續跑**:
  - 已經抓過的標的直接跳過
  - 撞到配額時乾淨地停下來並說明，重跑即可接續

用法:
    python research/fetch_revenue.py             # 抓（可重複執行）
    python research/fetch_revenue.py --status    # 只看進度
    python research/fetch_revenue.py --limit 300 # 這次最多抓幾檔
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data.database as db_module

API = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockMonthRevenue"
START = "2019-01-01"          # 多抓一年，年增率才算得出探索期起點
SLEEP = 0.35
TIMEOUT = 25
UA = {"User-Agent": "stockfilter-research/1.0 (personal research)"}

DDL = """
CREATE TABLE IF NOT EXISTS monthly_revenue (
    ticker        TEXT NOT NULL,       -- 帶市場後綴，與 daily_prices 一致
    revenue_year  INTEGER NOT NULL,
    revenue_month INTEGER NOT NULL,
    revenue       REAL,
    publish_date  TEXT,                -- 估計可用日:次月 11 日
    fetched_at    TEXT,
    PRIMARY KEY (ticker, revenue_year, revenue_month)
);
CREATE TABLE IF NOT EXISTS monthly_revenue_progress (
    ticker     TEXT PRIMARY KEY,
    status     TEXT,                   -- ok / empty / error
    n_rows     INTEGER,
    fetched_at TEXT,
    note       TEXT
);
"""


def publish_date(year: int, month: int) -> str:
    """M 月營收的保守可用日 = M+1 月 11 日。"""
    y, m = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{y:04d}-{m:02d}-11"


def call(stock_id: str):
    url = API + "?" + urllib.parse.urlencode(
        {"dataset": DATASET, "data_id": stock_id, "start_date": START})
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read()), ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return e.code, None, body
    except Exception as e:
        return -1, None, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=100000)
    args = ap.parse_args()

    con = sqlite3.connect(db_module.DB_PATH)
    con.executescript(DDL)
    con.commit()

    tickers = [r[0] for r in con.execute(
        "SELECT DISTINCT ticker FROM daily_prices WHERE ticker NOT LIKE '^%' ORDER BY ticker")]
    done = {r[0] for r in con.execute(
        "SELECT ticker FROM monthly_revenue_progress WHERE status IN ('ok','empty')")}
    todo = [t for t in tickers if t not in done]

    # 固定種子打亂順序。免費層配額會讓抓取分成很多輪，
    # 若照代號順序抓，中途停下時拿到的會是「1xxx~2xxx 的傳產與金融」，
    # 完全沒有 3xxx 以上的電子與上櫃股 —— 那種偏斜的樣本不能拿來做研究。
    # 打亂之後，任何時點的部分資料都是全市場的隨機子集。
    random.Random(20260901).shuffle(todo)

    n_rows = con.execute("SELECT COUNT(*) FROM monthly_revenue").fetchone()[0]
    print(f"標的總數 {len(tickers):,} ｜ 已完成 {len(done):,} ｜ 待抓 {len(todo):,} "
          f"｜ 現有營收列數 {n_rows:,}")
    if args.status:
        rng = con.execute(
            "SELECT MIN(revenue_year||'-'||substr('0'||revenue_month,-2)), "
            "MAX(revenue_year||'-'||substr('0'||revenue_month,-2)) FROM monthly_revenue"
        ).fetchone()
        print(f"營收期間: {rng[0]} ~ {rng[1]}")
        return 0
    if not todo:
        print("已全部抓完。")
        return 2          # 給批次檔的迴圈當作結束訊號

    todo = todo[:args.limit]
    t0 = time.time()
    ok = empty = 0
    for i, tk in enumerate(todo, 1):
        sid = tk.split(".")[0]
        status, j, err = call(sid)

        if j is None:
            low = err.lower()
            quota = status in (400, 402, 429) and ("level" in low or "limit" in low
                                                   or "quota" in low or "requests" in low)
            print(f"\n[{i}/{len(todo)}] {tk} HTTP {status}: {err[:160]}")
            if quota:
                print("\n配額或速率上限。已抓到的都存好了 —— "
                      "稍後重跑同一個指令即可接續。")
                break
            con.execute("INSERT OR REPLACE INTO monthly_revenue_progress VALUES (?,?,?,?,?)",
                        (tk, "error", 0, datetime.now().isoformat(timespec="seconds"),
                         err[:200]))
            con.commit()
            time.sleep(SLEEP)
            continue

        rows = j.get("data", [])
        recs = []
        for d in rows:
            y, m = d.get("revenue_year"), d.get("revenue_month")
            if not y or not m:
                continue
            recs.append((tk, int(y), int(m), float(d.get("revenue") or 0),
                         publish_date(int(y), int(m)),
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        if recs:
            con.executemany(
                "INSERT OR REPLACE INTO monthly_revenue VALUES (?,?,?,?,?,?)", recs)
            ok += 1
        else:
            empty += 1
        con.execute("INSERT OR REPLACE INTO monthly_revenue_progress VALUES (?,?,?,?,?)",
                    (tk, "ok" if recs else "empty", len(recs),
                     datetime.now().isoformat(timespec="seconds"), ""))
        con.commit()

        if i % 50 == 0 or i == len(todo):
            el = time.time() - t0
            rate = el / i
            print(f"  {i:>5}/{len(todo)}  有資料 {ok}  無資料 {empty}  "
                  f"{el/60:.1f} 分  剩餘約 {rate*(len(todo)-i)/60:.0f} 分", flush=True)
        time.sleep(SLEEP)

    total = con.execute("SELECT COUNT(*) FROM monthly_revenue").fetchone()[0]
    n_tk = con.execute("SELECT COUNT(DISTINCT ticker) FROM monthly_revenue").fetchone()[0]
    rng = con.execute(
        "SELECT MIN(revenue_year), MAX(revenue_year) FROM monthly_revenue").fetchone()
    print(f"\n完成本輪。monthly_revenue 共 {total:,} 列 / {n_tk:,} 檔 / {rng[0]}~{rng[1]}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
