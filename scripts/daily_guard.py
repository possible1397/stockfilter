"""
scripts/daily_guard.py — 每日例行的補跑守門員

由 job_runner 每 2 分鐘呼叫一次。判斷方式是「資料落後了沒」，不是「現在幾點」。

## 為什麼改成看資料

第一版的條件是「週一~週五、已過 15:30、daily_run.log 不是今天的」。
這個條件有兩個錯:

  1. 只在「當天 15:30 之後」才補。如果你只在早上開機（或整個週末開機），
     那就永遠補不到 —— 2026-09-28 就是這樣:電腦 9/23 早上之後關到 9/28 早上，
     9/23、9/24、9/25 三天的資料全部沒補，而 9/28 早上開機時因為還沒到 15:30，
     守門員直接放棄。資料就這樣落後了三個交易日。

  2. already_ran_today() 讀的是 daily_run.log 的**第一行**，
     而那個 log 是往後附加的 —— 第一行永遠是最舊的那次。
     所以那個檢查實際上幾乎永遠回傳 False，形同沒有作用。

改成直接比對「資料庫最新價格日」與「現在應該要有的最新交易日」:
落後就補，不管今天星期幾、現在幾點。

## 節流

遇到國定假日時，「應該要有的交易日」永遠等不到資料（那天本來就沒開盤），
所以加上節流:同一個目標日期最多嘗試 3 次，且兩次之間至少隔 90 分鐘。
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "stock_data.db")
BAT = os.path.join(ROOT, "run_update.bat")
STAMP = os.path.join(ROOT, "output", "jobs", ".daily_attempt")
LOG = os.path.join(ROOT, "output", "daily_run.log")

CUTOFF_HOUR = 15          # 台股 13:30 收盤，15:00 後當天資料才算可取
MAX_TRIES = 3             # 同一個目標日期最多試幾次（避開國定假日空轉）
MIN_GAP_MIN = 90          # 兩次嘗試至少間隔幾分鐘


def log(msg):
    line = f"[daily_guard] {datetime.now():%Y-%m-%d %H:%M} {msg}"
    print(line)


def expected_last_trading_day(now: datetime):
    """現在「應該」已經拿得到的最新交易日（不含國定假日判斷）。"""
    d = now.date()
    if now.hour < CUTOFF_HOUR:
        d -= timedelta(days=1)          # 今天的收盤資料還沒穩定
    while d.weekday() >= 5:             # 往前退到最近的平日
        d -= timedelta(days=1)
    return d


def db_last_date():
    if not os.path.exists(DB):
        return None
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            r = con.execute("SELECT MAX(date) FROM daily_prices").fetchone()[0]
        finally:
            con.close()
        return datetime.strptime(r, "%Y-%m-%d").date() if r else None
    except Exception as e:
        log(f"讀不到資料庫:{e}")
        return None


def read_stamp():
    try:
        target, tries, last = open(STAMP, encoding="utf-8").read().split(",")
        return target, int(tries), datetime.fromisoformat(last)
    except Exception:
        return None, 0, None


def write_stamp(target, tries):
    os.makedirs(os.path.dirname(STAMP), exist_ok=True)
    with open(STAMP, "w", encoding="utf-8") as f:
        f.write(f"{target},{tries},{datetime.now().isoformat(timespec='seconds')}")


def main():
    now = datetime.now()
    want = expected_last_trading_day(now)
    have = db_last_date()
    if have is None:
        return 0
    if have >= want:
        if os.path.exists(STAMP):
            try:
                os.remove(STAMP)        # 已經追上，重置節流
            except OSError:
                pass
        return 0

    target = want.isoformat()
    prev_target, tries, last_try = read_stamp()
    if prev_target != target:
        tries, last_try = 0, None
    if tries >= MAX_TRIES:
        return 0                        # 大概是國定假日，安靜放棄
    if last_try and (now - last_try) < timedelta(minutes=MIN_GAP_MIN):
        return 0                        # 節流中

    log(f"資料落後（有 {have}，應有 {want}），補跑每日例行"
        f"（第 {tries + 1}/{MAX_TRIES} 次嘗試）...")
    write_stamp(target, tries + 1)
    subprocess.run([BAT], cwd=ROOT, shell=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
