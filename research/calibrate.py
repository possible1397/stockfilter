"""
research/calibrate.py — 校準測試（階段 1 的完成條件）

用新框架重算一個已知答案:五年回測報告的
「Breakout 訊號 60 日超額報酬 +2.92%（n=925, p=0.0006）」。

必須落在 +2.42% ~ +3.42%。對不上就是框架有錯（最可能是日期對齊或前推視窗
差一天），在修好之前不看任何新結果。

用法: python research/calibrate.py
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data.database as db_module

LABELS = os.path.join("research", "data", "labels.parquet")
TARGET, TOL = 0.0292, 0.005
ORIGINAL_END = "2026-03-26"     # 原報告的 precompute 截止日


def report(name, s):
    n = len(s)
    if n == 0:
        print(f"  {name:<28} 無樣本")
        return None
    m, sd = s.mean(), s.std(ddof=1)
    se = sd / np.sqrt(n)
    t = m / se if se > 0 else np.nan
    # 常態近似的雙尾 p
    from math import erfc, sqrt
    p = erfc(abs(t) / sqrt(2)) if np.isfinite(t) else np.nan
    print(f"  {name:<28} n={n:>6,}  平均 {100*m:+.2f}%  "
          f"95%CI [{100*(m-1.96*se):+.2f}%, {100*(m+1.96*se):+.2f}%]  p={p:.2e}")
    return m


def main():
    if not os.path.exists(LABELS):
        print(f"找不到 {LABELS}，請先跑 python research/labels.py")
        return 1
    lab = pd.read_parquet(LABELS, columns=["ticker", "date", "fwd_ret_60", "excess_60"])

    con = sqlite3.connect(f"file:{db_module.DB_PATH}?mode=ro", uri=True)
    scans = pd.read_sql(
        "SELECT scan_date AS date, ticker, status FROM scan_results", con)
    con.close()

    df = scans.merge(lab, on=["ticker", "date"], how="left")
    bo = df[df["status"] == "Breakout"].dropna(subset=["excess_60"])
    cv = df[df["status"] == "Converging"].dropna(subset=["excess_60"])

    print("=" * 72)
    print("  校準測試 · Breakout 訊號的 60 日超額報酬")
    print("=" * 72)
    print(f"\n目標: +2.92%（原報告 n=925），允收區間 "
          f"+{100*(TARGET-TOL):.2f}% ~ +{100*(TARGET+TOL):.2f}%\n")

    print("全部可用訊號:")
    report("Breakout", bo["excess_60"])
    report("Converging", cv["excess_60"])

    print(f"\n限制在原報告區間（<= {ORIGINAL_END}）:")
    bo_o = bo[bo["date"] <= ORIGINAL_END]
    cv_o = cv[cv["date"] <= ORIGINAL_END]
    m = report("Breakout", bo_o["excess_60"])
    report("Converging", cv_o["excess_60"])

    print("\n" + "-" * 72)
    if m is None:
        print("  ✗ 無法計算 —— 檢查 labels 與 scan_results 的日期是否對得上")
        return 1
    if abs(m - TARGET) <= TOL:
        print(f"  ✓ 通過:{100*m:+.2f}% 落在允收區間內。框架可信，可以往下走。")
        return 0
    print(f"  ✗ 未通過:{100*m:+.2f}%，偏離目標 {100*(m-TARGET):+.2f}pp。")
    print("    在修好之前不要看任何新結果。優先檢查:")
    print("      1. 前推視窗是否差一天（fwd_ret_60 應為 Close[t+60]/Close[t]-1）")
    print("      2. excess 的基準是否為同日全市場等權平均")
    print("      3. scan_results 的 scan_date 是否等於訊號當日收盤日")
    print("      4. 價格是否因 yfinance 回溯調整而與當初計算時不同")
    return 1


if __name__ == "__main__":
    sys.exit(main())
