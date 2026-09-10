"""
scripts/revenue_guard.py — 月營收補抓的守門員（每次只跑一輪）

為什麼要這樣改:
  舊做法是一個 .bat 裡面 `ping -n 3901` 等 65 分鐘再抓下一輪。
  2026-09-08 19:46 抓到配額上限後進入等待，電腦隨後休眠/關機，
  那個 cmd 行程被殺掉 —— 而它持有的 output/jobs/.lock 沒有被釋放。
  結果 job runner 連續 24 小時每 2 分鐘都撞到那個鎖然後跳過，
  整套自動化靜悄悄地死了一整天（連每日例行也一起停擺）。

教訓:**任何超過幾分鐘的等待都不能放在行程裡**，
要拆成「每次呼叫做一小步」，讓排程本身當計時器。

本檔由 job_runner 每 2 分鐘呼叫一次，每次只做一件小事:
  - 已經抓完 → 立刻結束（幾毫秒）
  - 距離上次抓取不到 COOLDOWN 分鐘 → 立刻結束
  - 否則跑一輪 fetch（約 7 分鐘，撞到配額就自己停）
  - 全部抓完的那一次，順便重建特徵並重跑分析，然後寫下完成標記
"""
import os
import subprocess
import sqlite3
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
STAMP = os.path.join(ROOT, "output", "jobs", ".revenue_last_round")
DONE = os.path.join(ROOT, "output", "jobs", ".revenue_complete")
STALL = os.path.join(ROOT, "output", "jobs", ".revenue_stall")
LOG = os.path.join(ROOT, "output", "phase2_fill.log")
COOLDOWN = 62          # 分鐘。FinMind 免費層約每小時重置


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[guard] {datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def remaining():
    db = os.path.join(ROOT, "stock_data.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tot = con.execute("SELECT COUNT(DISTINCT ticker) FROM daily_prices "
                          "WHERE ticker NOT LIKE '^%'").fetchone()[0]
        done = con.execute("SELECT COUNT(*) FROM monthly_revenue_progress "
                           "WHERE status IN ('ok','empty')").fetchone()[0]
        return tot - done, tot, done
    finally:
        con.close()


def finish(done, tot, why):
    log(f"{why}:{done}/{tot}，重建特徵並重跑分析...")
    for script in ("features_revenue.py", "episodes.py", "incremental.py"):
        subprocess.run([PY, os.path.join("research", script)],
                       cwd=ROOT, stdout=open(LOG, "a", encoding="utf-8"),
                       stderr=subprocess.STDOUT)
    subprocess.run([PY, os.path.join("research", "lift.py"), "--v2"],
                   cwd=ROOT, stdout=open(LOG, "a", encoding="utf-8"),
                   stderr=subprocess.STDOUT)
    with open(DONE, "w", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}  {why}  "
                f"覆蓋 {done}/{tot}")
    log("完成:特徵已重建，分析已重跑。")


def main():
    if os.path.exists(DONE):
        return 0
    try:
        left, tot, done = remaining()
    except Exception as e:
        log(f"讀取進度失敗: {e}")
        return 0

    if left <= 0:
        finish(done, tot, "全部抓完")
        return 0

    # 停滯偵測:連續 3 輪都沒有新增任何一檔，就認定剩下的抓不到了。
    # （2026-09-10 的教訓:83 檔卡在 DNS 失敗，remaining 永遠不會歸零，
    #   守門員會無限期空轉，整個階段 2 就這樣卡了好幾天。）
    prev = None
    if os.path.exists(STALL):
        try:
            prev_done, prev_n = open(STALL, encoding="utf-8").read().split(",")
            prev, n_stall = int(prev_done), int(prev_n)
        except Exception:
            prev, n_stall = None, 0
    else:
        n_stall = 0
    n_stall = n_stall + 1 if prev == done else 0
    with open(STALL, "w", encoding="utf-8") as f:
        f.write(f"{done},{n_stall}")
    if n_stall >= 3:
        log(f"連續 {n_stall} 輪沒有進展，剩下 {left} 檔判定抓不到，"
            f"以 {done}/{tot} 的覆蓋率結案。")
        finish(done, tot, f"停滯結案，未取得 {left} 檔")
        return 0

    if os.path.exists(STAMP):
        last = datetime.fromtimestamp(os.path.getmtime(STAMP))
        if datetime.now() - last < timedelta(minutes=COOLDOWN):
            return 0          # 配額冷卻中，安靜離開

    log(f"開始一輪（還有 {left} 檔未抓）")
    with open(STAMP, "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat(timespec="seconds"))
    subprocess.run([PY, os.path.join("research", "fetch_revenue.py")],
                   cwd=ROOT, stdout=open(LOG, "a", encoding="utf-8"),
                   stderr=subprocess.STDOUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
