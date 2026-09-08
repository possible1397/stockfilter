"""
scripts/daily_guard.py — 每日例行的補跑守門員

Windows 排程建立的 weekly 工作，如果到了 15:30 電腦沒開，那一次就直接跳過，
不會補跑（2026-09-08 就是這樣:daily_run.log 停在 8/24）。

job_runner 每 2 分鐘會呼叫這支。它只做一件事:
若今天是週一~週五、現在已過 15:30、而 daily_run.log 不是今天的，
就把每日例行補跑起來。這樣「錯過就跳過」的問題就沒了。

檢查成本極低（讀一行 log），不需要補跑時立刻結束。
"""
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "output", "daily_run.log")
BAT = os.path.join(ROOT, "run_update.bat")


def already_ran_today() -> bool:
    if not os.path.exists(LOG):
        return False
    today = datetime.now().strftime("%Y/%m/%d")
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
        return today in first
    except Exception:
        return False


def main():
    now = datetime.now()
    if now.weekday() >= 5:                       # 週六日不跑
        return 0
    if (now.hour, now.minute) < (15, 30):        # 收盤後才跑
        return 0
    if already_ran_today():
        return 0
    print(f"[daily_guard] {now:%Y-%m-%d %H:%M} 今天的每日例行還沒跑，補跑中...")
    subprocess.run([BAT], cwd=ROOT, shell=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
