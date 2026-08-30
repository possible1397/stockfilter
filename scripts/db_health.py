"""
scripts/db_health.py — 資料健檢

每個階段開始前先跑一次。研究的結論只會跟底下的資料一樣好，
而資料的破洞多半是沉默的：欄位全空、某段日期沒抓到、某些股票只有半年。

用法:
    python scripts/db_health.py                 # 摘要
    python scripts/db_health.py --detail        # 加上缺漏交易日與問題股票清單
    python scripts/db_health.py --csv           # 另存 output/db_health_YYYYMMDD.csv
"""
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data.database as db_module

DETAIL = "--detail" in sys.argv
CSV = "--csv" in sys.argv
BAR = "─" * 66


def h(title):
    print(f"\n{BAR}\n  {title}\n{BAR}")


def pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


def main():
    db_path = db_module.DB_PATH
    if not os.path.exists(db_path):
        print(f"找不到資料庫: {db_path}")
        return
    size_gb = os.path.getsize(db_path) / 1024 ** 3
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows_out = []

    print(f"\n資料健檢 · {datetime.now():%Y-%m-%d %H:%M}")
    print(f"資料庫: {db_path}  ({size_gb:.2f} GB)")

    # ── 價格 ──────────────────────────────────────────────────────
    h("daily_prices")
    n, nt, d0, d1 = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM daily_prices"
    ).fetchone()
    print(f"  {n:,} 列 · {nt:,} 檔 · {d0} ~ {d1}")
    rows_out += [("daily_prices", "rows", n), ("daily_prices", "tickers", nt),
                 ("daily_prices", "last_date", d1)]

    # 交易日 = 大盤指數有資料的日子
    tdays = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_prices WHERE ticker='^TWII' ORDER BY date")]
    print(f"  交易日基準 (^TWII): {len(tdays):,} 天")

    lag = conn.execute(
        "SELECT COUNT(*) FROM (SELECT ticker, MAX(date) m FROM daily_prices "
        "WHERE ticker NOT LIKE '^%' GROUP BY ticker) WHERE m < ?", (d1,)).fetchone()[0]
    print(f"  最新日 {d1} 沒有資料的股票: {lag:,} 檔 ({pct(lag, nt)})")
    rows_out.append(("daily_prices", "stale_tickers", lag))

    short = conn.execute(
        "SELECT COUNT(*) FROM (SELECT ticker, COUNT(*) c FROM daily_prices "
        "WHERE ticker NOT LIKE '^%' GROUP BY ticker HAVING c < 250)").fetchone()[0]
    print(f"  歷史不足一年 (<250 根) 的股票: {short:,} 檔")
    rows_out.append(("daily_prices", "short_history_tickers", short))

    # ── 籌碼 ──────────────────────────────────────────────────────
    h("chip_data")
    cn, ct, c0, c1 = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM chip_data"
    ).fetchone()
    print(f"  {cn:,} 列 · {ct:,} 檔 · {c0} ~ {c1}")
    for col in ("trust_net_buy", "foreign_net_buy", "dealer_net_buy",
                "big_player_400", "big_player_1000"):
        nn = conn.execute(f"SELECT COUNT({col}) FROM chip_data").fetchone()[0]
        flag = "  ← 幾乎全空" if cn and nn / cn < 0.05 else ""
        print(f"  {col:<18} 有值 {nn:>12,}  空值率 {pct(cn - nn, cn):>7}{flag}")
        rows_out.append(("chip_data", f"{col}_null_pct", round(100.0 * (cn - nn) / cn, 2) if cn else None))

    # ── 掃描結果 ──────────────────────────────────────────────────
    h("scan_results / scan_runs")
    sn, sd, s0, s1 = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT scan_date), MIN(scan_date), MAX(scan_date) FROM scan_results"
    ).fetchone()
    print(f"  {sn:,} 筆訊號 · {sd:,} 個掃描日 · {s0} ~ {s1}")
    dup = conn.execute(
        "SELECT COUNT(*) FROM (SELECT scan_date, ticker FROM scan_results "
        "GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0]
    print(f"  重複 (日期, 股票): {dup} 組" + ("  ← 需要跑 migrate_scan_results.py" if dup else "  ✓"))
    rows_out.append(("scan_results", "duplicate_groups", dup))

    try:
        stamped = conn.execute(
            "SELECT COUNT(*) FROM scan_results WHERE config_hash IS NOT NULL").fetchone()[0]
        print(f"  有參數戳記的訊號: {stamped:,} / {sn:,} ({pct(stamped, sn)})")
        for ch, gc, c in conn.execute(
                "SELECT COALESCE(config_hash,'(無)'), COALESCE(git_commit,'(無)'), COUNT(*) "
                "FROM scan_results GROUP BY 1,2 ORDER BY 3 DESC LIMIT 5"):
            print(f"      config={ch:<12} git={gc:<12} {c:>7,} 筆")
    except sqlite3.OperationalError:
        print("  尚未加上 config_hash 欄位 —— 請先跑 scripts/migrate_scan_results.py")

    # 掃描覆蓋率：訊號日 vs 交易日
    try:
        runs = set(r[0] for r in conn.execute("SELECT scan_date FROM scan_runs"))
    except sqlite3.OperationalError:
        runs = set(r[0] for r in conn.execute("SELECT DISTINCT scan_date FROM scan_results"))
    if s0:
        window = [d for d in tdays if s0 <= d <= d1]
        gaps = [d for d in window if d not in runs]
        print(f"  掃描覆蓋: {len(window) - len(gaps):,} / {len(window):,} 個交易日"
              f"  缺 {len(gaps):,} 天")
        rows_out.append(("scan_runs", "missing_days", len(gaps)))
        if gaps:
            print(f"  最近的缺漏: {', '.join(gaps[-8:])}")
            if DETAIL:
                print(f"  全部缺漏 ({len(gaps)}):")
                for i in range(0, len(gaps), 8):
                    print("      " + ", ".join(gaps[i:i + 8]))
            print("  → forward_validate.py 會自動補算凍結期起的缺漏日")

    # ── 紙上交易 ──────────────────────────────────────────────────
    h("paper_trades")
    try:
        pt = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        print(f"  {pt} 筆" + ("  ← 模組從未實際跑出紀錄" if pt == 0 else ""))
        rows_out.append(("paper_trades", "rows", pt))
    except sqlite3.OperationalError:
        print("  表不存在")

    if CSV:
        import csv as _csv
        os.makedirs("output", exist_ok=True)
        path = os.path.join("output", f"db_health_{datetime.now():%Y%m%d}.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            w.writerow(["table", "metric", "value"])
            w.writerows(rows_out)
        print(f"\n已輸出: {path}")

    conn.close()
    print(f"\n{BAR}\n")


if __name__ == "__main__":
    main()
