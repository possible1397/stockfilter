"""
台股三角收斂自動篩選器 - 主程式進入點
執行方式 (從專案根目錄執行):
    python main.py            # 使用全台股清單掃描
    python main.py --sample   # 僅用 5 檔樣本測試 (開發時使用)
"""

import csv
import os
import sys
from datetime import datetime

from data.fetcher import StockFetcher
from data.cleaner import DataCleaner
from data.stock_list import get_all_tickers
from core.recognizer import PatternRecognizer
from utils.visualizer import Visualizer

# ==================== 設定區 ====================
DB_PATH      = "stock_data.db"
OUTPUT_DIR   = "output"
RESULTS_CSV  = os.path.join(OUTPUT_DIR, f"scanner_{datetime.today().strftime('%Y%m%d')}.csv")

# 多執行緒下載的工作數量
# 建議值: 5~10。台股 2000 檔，每批 10 條線，約 10~20 分鐘完成
DOWNLOAD_WORKERS = 8

# 樣本模式 (執行 python main.py --sample 時只掃 5 檔)
SAMPLE_TICKERS = [
    "2330.TW",  # 台積電
    "2317.TW",  # 鴻海
    "2454.TW",  # 聯發科
    "2308.TW",  # 台達電
    "2382.TW",  # 廣達
]
# ================================================

def run_scanner(tickers: list):
    print(f"=== 台股三角收斂篩選器啟動 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"[*] 掃描標的數: {len(tickers)}")

    # 初始化各模組
    fetcher    = StockFetcher(db_path=DB_PATH)
    cleaner    = DataCleaner(min_vol_lots=500, min_bars=60)
    recognizer = PatternRecognizer(order=5, min_points=3, min_bars=20, convergence_threshold=0.3)
    visualizer = Visualizer(output_dir=OUTPUT_DIR)

    # 清理 14 天以上的舊圖表 (節省磁碟空間)
    visualizer.cleanup_old_charts(days=14)

    # Step 1: 批次下載 K 線資料
    print(f"\n[Step 1] 批次下載 K 線資料 (使用 {DOWNLOAD_WORKERS} 執行緒)...")
    stock_data = fetcher.fetch_batch_data(tickers, max_workers=DOWNLOAD_WORKERS)

    # Step 2: 資料清洗
    print("\n[Step 2] 資料清洗 (過濾低流動性標的)...")
    valid_data = cleaner.filter_batch(stock_data)

    # Step 3: 三角收斂分析
    print(f"\n[Step 3] 執行三角收斂與多頭濾網分析 (共 {len(valid_data)} 檔)...")
    hits = []
    for ticker, df in valid_data.items():
        result = recognizer.analyze(ticker, df)
        if result.is_converging:
            status_tag = "[🚀 真突破!]" if getattr(result, 'is_breakout', False) else "[== 潛伏中 ==]"
            print(f"  {status_tag} {ticker} | 股價={result.current_price:.1f} | "
                  f"壓力={result.resistance_price:.1f} | 支撐={result.support_price:.1f} | "
                  f"收斂率={result.convergence_ratio:.1%} | 區間={result.bar_span}根K")
            hits.append(result)
            # 繪製圖表存檔
            visualizer.plot(ticker, df, result, show=False)

    # Step 4: 輸出報表
    print(f"\n[Step 4] 輸出結果報表...")
    if hits:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(RESULTS_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['ticker', 'status', 'current_price', 'resistance_price', 'support_price',
                          'slope_up', 'slope_down', 'convergence_ratio', 'bar_span']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in hits:
                status = "Breakout" if getattr(r, 'is_breakout', False) else "Converging"
                writer.writerow({
                    'ticker':            r.ticker,
                    'status':            status,
                    'current_price':     r.current_price,
                    'resistance_price':  r.resistance_price,
                    'support_price':     r.support_price,
                    'slope_up':          r.slope_up,
                    'slope_down':        r.slope_down,
                    'convergence_ratio': r.convergence_ratio,
                    'bar_span':          r.bar_span,
                })
        print(f"  報表已輸出至: {RESULTS_CSV}")
    else:
        print("  本次掃描未找到符合三角收斂條件的標的。")

    print(f"\n=== 掃描完成！{len(tickers)} 檔 -> {len(valid_data)} 檔合格 -> {len(hits)} 檔命中三角收斂 ===")
    fetcher.close()
    return hits


if __name__ == "__main__":
    # 判斷執行模式
    sample_mode = '--sample' in sys.argv

    if sample_mode:
        print("[模式] 樣本測試模式 (5 檔)")
        tickers = SAMPLE_TICKERS
    else:
        print("[模式] 全台股掃描模式，正在取得清單...")
        tickers = get_all_tickers(use_cache=True)
        if not tickers:
            print("清單取得失敗，切換為樣本模式")
            tickers = SAMPLE_TICKERS

    results = run_scanner(tickers)
