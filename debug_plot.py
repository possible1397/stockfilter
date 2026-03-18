"""
偵錯視覺化腳本 - 純 matplotlib 實作標準蠟燭圖 + 極值散點 + 趨勢線
不依賴 mplfinance，避免版本相容問題
執行: python debug_plot.py
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
from scipy.signal import argrelextrema

import data.database as db_module

# ===== 設定 =====
TICKER = "2330.TW"
ORDER  = 5   # 極值偵測範圍，可調整 (5~10)
# ================

# --- 中文字型 ---
for font_name in ['Microsoft JhengHei', 'Microsoft YaHei', 'SimHei']:
    try:
        fm.findfont(fm.FontProperties(family=font_name), fallback_to_default=False)
        plt.rcParams['font.family'] = font_name
        break
    except Exception:
        continue
plt.rcParams['axes.unicode_minus'] = False


def draw_candlestick(ax, df: pd.DataFrame):
    """在 ax 上繪製蠟燭圖"""
    for i, (idx, row) in enumerate(df.iterrows()):
        o, h, l, c = row['Open'], row['High'], row['Low'], row['Close']
        color = '#EF5350' if c >= o else '#26A69A'   # 紅漲綠跌 (台灣習慣)
        # 上下影線
        ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=1)
        # 實體蠟燭
        body_bottom = min(o, c)
        body_height = abs(c - o) if abs(c - o) > 0 else 0.5
        rect = mpatches.FancyBboxPatch(
            (i - 0.35, body_bottom), 0.7, body_height,
            boxstyle="square,pad=0",
            facecolor=color, edgecolor=color, linewidth=0, zorder=2
        )
        ax.add_patch(rect)


def debug_plot(ticker: str, order: int):
    print(f"[偵錯] 載入 {ticker} 資料...")
    db_module.init_db()
    df = db_module.load_dataframe(ticker)

    if df.empty:
        print("資料為空，請先執行 main.py 下載資料")
        return

    n = len(df)
    x = np.arange(n)
    dates = df.index
    print(f"  K 線筆數: {n}，區間: {dates[0].date()} ~ {dates[-1].date()}")

    high_arr  = df['High'].values
    low_arr   = df['Low'].values
    close_arr = df['Close'].values
    vol_arr   = df['Volume'].values / 1000  # 張

    # --- 找極值 ---
    peak_idx   = argrelextrema(high_arr, np.greater, order=order)[0]
    valley_idx = argrelextrema(low_arr,  np.less,    order=order)[0]
    print(f"  高點: {len(peak_idx)} 個，低點: {len(valley_idx)} 個 (order={order})")

    min_pts = 3
    recent_peaks   = peak_idx[-5:]   if len(peak_idx)   >= min_pts else peak_idx
    recent_valleys = valley_idx[-5:] if len(valley_idx) >= min_pts else valley_idx

    # --- 線性迴歸 ---
    slope_up = slope_down = None
    resist_line = np.full(n, np.nan)
    support_line = np.full(n, np.nan)

    if len(recent_peaks) >= 2:
        coef = np.polyfit(recent_peaks.astype(float), high_arr[recent_peaks], 1)
        slope_up = coef[0]
        full = np.polyval(coef, x.astype(float))
        resist_line[recent_peaks[0]:] = full[recent_peaks[0]:]

    if len(recent_valleys) >= 2:
        coef = np.polyfit(recent_valleys.astype(float), low_arr[recent_valleys], 1)
        slope_down = coef[0]
        full = np.polyval(coef, x.astype(float))
        support_line[recent_valleys[0]:] = full[recent_valleys[0]:]

    # --- 判斷三角收斂條件 ---
    cond_ok = (slope_up is not None and slope_down is not None
               and slope_up < 0 and slope_down > 0)

    verdict = "[符合三角收斂斜率條件 OK]" if cond_ok else "[不符合三角收斂 NG]"
    slope_info = ""
    if slope_up is not None:
        slope_info += f"  壓力線 slope={slope_up:+.4f} {'(OK)' if slope_up<0 else '(NG 需<0)'}"
    if slope_down is not None:
        slope_info += f"  |  支撐線 slope={slope_down:+.4f} {'(OK)' if slope_down>0 else '(NG 需>0)'}"
    print(f"\n  結果: {verdict}")
    print(f"  {slope_info}\n")

    # ===== 繪圖 =====
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(18, 9),
        gridspec_kw={'height_ratios': [3, 1]},
        sharex=True
    )
    color_verdict = '#1B5E20' if cond_ok else '#B71C1C'
    fig.suptitle(
        f"{ticker}  K線偵錯視覺化  (order={order})\n"
        f"{verdict}{slope_info}",
        fontsize=12, color=color_verdict
    )

    # -- 蠟燭圖 --
    draw_candlestick(ax1, df)

    # -- 高低點散點 --
    ax1.scatter(peak_idx,   high_arr[peak_idx] * 1.006,  marker='v', s=70,  color='royalblue',  zorder=5, label=f'高點 ({len(peak_idx)}個)')
    ax1.scatter(recent_peaks, high_arr[recent_peaks]*1.013, marker='o', s=120, color='darkblue', zorder=6, label=f'壓力趨勢高點 ({len(recent_peaks)}個)')
    ax1.scatter(valley_idx, low_arr[valley_idx]  * 0.994, marker='^', s=70,  color='orange',     zorder=5, label=f'低點 ({len(valley_idx)}個)')
    ax1.scatter(recent_valleys, low_arr[recent_valleys]*0.987, marker='o', s=120, color='darkorange', zorder=6, label=f'支撐趨勢低點 ({len(recent_valleys)}個)')

    # -- 趨勢線 --
    valid_r = ~np.isnan(resist_line)
    valid_s = ~np.isnan(support_line)
    if valid_r.any():
        lbl = f'壓力線 slope={slope_up:+.4f} {"(OK)" if slope_up<0 else "(NG)"}'
        ax1.plot(x[valid_r], resist_line[valid_r], 'r--', linewidth=2.0, label=lbl, zorder=4)
    if valid_s.any():
        lbl = f'支撐線 slope={slope_down:+.4f} {"(OK)" if slope_down>0 else "(NG)"}'
        ax1.plot(x[valid_s], support_line[valid_s], 'g--', linewidth=2.0, label=lbl, zorder=4)

    ax1.set_ylabel('股價 (TWD)')
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(alpha=0.2)

    # -- 成交量 --
    bar_colors = ['#EF5350' if c >= o else '#26A69A'
                  for c, o in zip(close_arr, df['Open'].values)]
    ax2.bar(x, vol_arr, color=bar_colors, alpha=0.8)
    ax2.set_ylabel('成交量 (張)')
    ax2.grid(alpha=0.2)

    # X 軸標籤 (每 20 根顯示日期)
    step = 20
    ticks = x[::step]
    labels = [dates[i].strftime('%y/%m/%d') for i in ticks]
    ax2.set_xticks(ticks)
    ax2.set_xticklabels(labels, rotation=30, fontsize=8)

    plt.tight_layout()

    save_path = f"output/debug_{ticker.replace('.','_')}_order{order}.png"
    fig.savefig(save_path, dpi=120, bbox_inches='tight')
    print(f"[偵錯] 圖表已儲存至: {save_path}")
    plt.show()
    plt.close(fig)


if __name__ == "__main__":
    debug_plot(TICKER, ORDER)
