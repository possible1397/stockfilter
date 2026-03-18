import mplfinance as mpf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
from datetime import datetime
from core.recognizer import TriangleResult
from scipy.signal import argrelextrema

class Visualizer:
    """
    使用 mplfinance 繪製 K 線圖，並疊加三角收斂的壓力線與支撐線。
    圖表將儲存在 output/ 資料夾。
    """

    def __init__(self, output_dir: str = 'output'):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def plot(self, ticker: str, df: pd.DataFrame, result: TriangleResult, 
             order: int = 5, show: bool = False):
        """
        繪製帶趨勢線的 K 線圖並存檔。
        ticker: 股票代號
        df: 包含 OHLCV 的 DataFrame (index 為 DatetimeIndex)
        result: PatternRecognizer 分析出的 TriangleResult
        order: 與 recognizer 一致的 argrelextrema order 參數
        show: True 則彈出視窗，False 則只存檔
        """
        if df.empty or not result.is_converging:
            print(f"[{ticker}] 條件不足，跳過繪圖")
            return

        # mplfinance 要求 DataFrame 的欄位名稱首字大寫且 index 為 DatetimeIndex
        df_plot = df.copy()
        df_plot.columns = [c.capitalize() for c in df_plot.columns]
        if not isinstance(df_plot.index, pd.DatetimeIndex):
            df_plot.index = pd.to_datetime(df_plot.index)

        n = len(df_plot)
        x_idx = np.arange(n)

        # 計算壓力線與支撐線在每個 bar 位置的價格
        # (slope 與 intercept 是基於原始 row 索引計算，此處直接對應)
        high_col = 'High'
        low_col  = 'Low'
        peaks   = argrelextrema(df_plot[high_col].values, np.greater, order=order)[0]
        valleys = argrelextrema(df_plot[low_col].values,  np.less,    order=order)[0]

        recent_peaks   = peaks[-5:]   if len(peaks)   >= 3 else peaks
        recent_valleys = valleys[-5:] if len(valleys) >= 3 else valleys

        if len(recent_peaks) < 2 or len(recent_valleys) < 2:
            print(f"[{ticker}] 極值點不足，跳過繪圖")
            return

        peak_prices   = df_plot[high_col].values[recent_peaks]
        valley_prices = df_plot[low_col].values[recent_valleys]

        slope_up, intercept_up   = np.polyfit(recent_peaks.astype(float),   peak_prices, 1)
        slope_down, intercept_down = np.polyfit(recent_valleys.astype(float), valley_prices, 1)

        # 計算趨勢線在每個 bar 上的端點
        resist_line = slope_up   * x_idx + intercept_up
        support_line = slope_down * x_idx + intercept_down

        # 只繪製從第一個高低點到最後一根 K 的範圍 (其餘設為 NaN 避免遮擋)
        start_x = int(min(recent_peaks[0], recent_valleys[0]))
        resist_line[:start_x]  = np.nan
        support_line[:start_x] = np.nan

        # 建立標題 (使用 ASCII，避免中文字型依賴)
        title = (f"{ticker}  Triangle Convergence | "
                 f"slope_up={result.slope_up:.4f}  slope_dn={result.slope_down:.4f}  "
                 f"conv={result.convergence_ratio:.1%}  span={result.bar_span}bars")

        # 存檔路徑
        today = datetime.today().strftime('%Y%m%d')
        safe_ticker = ticker.replace('.', '_')
        save_path = os.path.join(self.output_dir, f"{today}_{safe_ticker}.png")

        # 繪製 K 線主圖 (不用 addplot 傳趨勢線，避免 linewidth 相容性問題)
        fig, axes = mpf.plot(
            df_plot,
            type='candle',
            style='charles',
            title=title,
            ylabel='Price',
            volume=True,
            figscale=1.5,
            returnfig=True,
        )

        # 取主圖 axes，手動疊加趨勢線
        ax_main = axes[0]
        x_all = np.arange(n)

        valid_r = ~np.isnan(resist_line)
        valid_s = ~np.isnan(support_line)
        if valid_r.any():
            ax_main.plot(x_all[valid_r], resist_line[valid_r],
                         'r--', linewidth=2.0,
                         label=f'Resistance slope={result.slope_up:.4f}')
        if valid_s.any():
            ax_main.plot(x_all[valid_s], support_line[valid_s],
                         'g--', linewidth=2.0,
                         label=f'Support slope={result.slope_down:.4f}')
        ax_main.legend(loc='upper left', fontsize=9)

        fig.savefig(save_path, dpi=100, bbox_inches='tight')
        if show:
            plt.show()
        plt.close(fig)

        print(f"  [{ticker}] Chart saved: {save_path}")

    def cleanup_old_charts(self, days: int = 14):
        """
        自動刪除 output/ 資料夾中超過 N 天的舊圖表
        """
        from datetime import timedelta
        cutoff = datetime.today() - timedelta(days=days)
        deleted = 0
        for fname in os.listdir(self.output_dir):
            if not fname.endswith('.png'):
                continue
            fpath = os.path.join(self.output_dir, fname)
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
            if mtime < cutoff:
                os.remove(fpath)
                deleted += 1
        if deleted:
            print(f"[Visualizer] 自動清理 {deleted} 張超過 {days} 天的舊圖表")
