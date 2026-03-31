"""
台股三角收斂自動篩選器 - 主程式進入點 v2
策略核心: 反轉邏輯 — 用收斂密度預測族群輪動

執行方式 (從專案根目錄執行):
    python main.py            # 使用全台股清單掃描
    python main.py --sample   # 僅用 5 檔樣本測試 (開發時使用)
"""

import csv
import os
import sys
from datetime import datetime

import yaml
import numpy as np
import pandas as pd

from utils.logger import setup_logging, get_logger
from data.fetcher import StockFetcher
from data.cleaner import DataCleaner
from data.stock_list import get_all_tickers
from core.recognizer import PatternRecognizer
from core.market_regime import MarketRegimeDetector
from core.sector_rotation import SectorRotationEngine
from core.scorer import StrategyScorer, StrategyScore
from core.supply_chain import SupplyChainEngine
from core.convergence_scanner import ConvergenceScanner
from core.chip_analysis import ChipAnalyzer
from utils.visualizer import Visualizer
import data.database as db_module

# ==================== 載入設定 ====================
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config(path: str = _CONFIG_FILE) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


CFG = load_config()
_scanner = CFG.get("scanner", {})
_pattern = CFG.get("pattern", {})
_cleaner = CFG.get("cleaner", {})
_logging = CFG.get("logging", {})

# 初始化 logging
setup_logging(
    log_dir=_logging.get("log_dir", "logs"),
    level=_logging.get("level", "INFO"),
    max_bytes=_logging.get("max_bytes", 10 * 1024 * 1024),
    backup_count=_logging.get("backup_count", 5),
)
logger = get_logger(__name__)

# ==================== 設定區 ====================
DB_PATH      = _scanner.get("db_path", "stock_data.db")
OUTPUT_DIR   = _scanner.get("output_dir", "output")
RESULTS_CSV  = os.path.join(OUTPUT_DIR, f"scanner_{datetime.today().strftime('%Y%m%d')}.csv")

FORCE_SAFE_TRADE       = _scanner.get("force_safe_trade", True)
MARKET_SCORE_THRESHOLD = _scanner.get("market_score_threshold", 40)
TOP_SECTOR_COUNT       = _scanner.get("top_sector_count", 10)
DOWNLOAD_WORKERS       = _scanner.get("download_workers", 8)
CHART_CLEANUP_DAYS     = _scanner.get("chart_cleanup_days", 14)

# 樣本模式
SAMPLE_TICKERS = [
    "2330.TW",  # 台積電
    "2317.TW",  # 鴻海
    "2454.TW",  # 聯發科
    "2308.TW",  # 台達電
    "2382.TW",  # 廣達
]

# ==================== 診斷常數 ====================
STAGE_LABEL = {
    "TREND":    "趨勢濾網",
    "VOLUME":   "量縮濾網VCP",
    "PIVOTS":   "樞紐點不足",
    "SLOPE":    "斜率條件",
    "RATIO":    "收斂比>0.3",
    "SPAN":     "週期<20根",
    "BREAKOUT": "突破量不足",
    "":         "通過",
}
STAGE_SORT = {"": 0, "BREAKOUT": 1, "SPAN": 2, "RATIO": 3,
              "SLOPE": 4, "PIVOTS": 5, "VOLUME": 6, "TREND": 7}
# ================================================


def _print_funnel_report(all_results):
    from collections import Counter
    counts = Counter(r.failure_stage for r in all_results)
    passed = counts.get("", 0)
    logger.info("─" * 55)
    logger.info("  [診斷] 篩選漏斗報告")
    logger.info("─" * 55)
    for stage in ["TREND","VOLUME","PIVOTS","SLOPE","RATIO","SPAN","BREAKOUT"]:
        n = counts.get(stage, 0)
        if n:
            logger.info(f"  [-] {STAGE_LABEL[stage]:<20} 淘汰 {n:4d} 檔")
    logger.info(f"  [+] 通過全部條件          {passed:4d} 檔")
    logger.info("─" * 55)
    # 當無標的通過時，顯示最接近通過的前 10 檔
    if passed == 0:
        late = [r for r in all_results
                if r.failure_stage in ("RATIO", "SPAN", "BREAKOUT")]
        late.sort(key=lambda r: r.convergence_ratio if r.convergence_ratio < 1 else 99)
        if late:
            logger.info("  [接近通過] 最末關才被淘汰的前 10 檔：")
            for r in late[:10]:
                logger.info(f"    {r.ticker:10} | {STAGE_LABEL[r.failure_stage]:12} | {r.reason}")


def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """計算 14 日 ATR（與回測引擎同邏輯）"""
    if df is None or len(df) < period + 1:
        close = float(df['Close'].iloc[-1]) if df is not None and not df.empty else 0
        return close * 0.03
    highs      = df['High'].values[-period:]
    lows       = df['Low'].values[-period:]
    closes     = df['Close'].values[-period:]
    prev_close = np.roll(closes, 1)
    tr = np.maximum(highs - lows,
                    np.maximum(np.abs(highs - prev_close),
                               np.abs(lows - prev_close)))
    return float(np.mean(tr[1:]))


def _build_action_plan(result, atr: float, cfg: dict) -> tuple:
    """
    依回測參數建立操作建議，回傳 (entry_ref, stop_price, target_price, action_str)
    與 BacktestConfig 邏輯一致:
      - 停損: entry * (1 - stop_loss%)
      - 停利: entry * (1 + take_profit%)
      - ATR 追蹤停損: 浮盈 >= atr_activate_pct% 後啟動，trailing = highest - ATR * atr_stop_mult
    """
    bt = cfg.get("backtest", {})
    stop_loss_pct    = float(bt.get("stop_loss",    0.07))   # e.g. 0.07
    take_profit_pct  = float(bt.get("take_profit",  0.20))
    atr_mult         = float(bt.get("atr_stop_mult",  3.0))  # BacktestConfig 預設
    atr_activate_pct = float(bt.get("atr_activate_pct", 5.0)) / 100

    if result.is_breakout:
        entry_ref = result.current_price
        status_str = "突破進場"
    else:
        entry_ref = result.resistance_price
        status_str = "突破後進場"

    stop_price   = round(entry_ref * (1 - stop_loss_pct), 2)
    target_price = round(entry_ref * (1 + take_profit_pct), 2)
    atr_activates_at = round(entry_ref * (1 + atr_activate_pct), 2)
    atr_trail_stop   = round(entry_ref - atr * atr_mult, 2)

    if result.is_breakout:
        action = (f"【{status_str}】次日開盤介入 "
                  f"| 硬停損:{stop_price} ({stop_loss_pct*100:.0f}%) "
                  f"| 停利:{target_price} ({take_profit_pct*100:.0f}%) "
                  f"| ATR追蹤:浮盈>{atr_activate_pct*100:.0f}%後啟動 "
                  f"(初始線:{atr_trail_stop})")
    else:
        action = (f"【{status_str}】預掛觸價:{result.resistance_price:.2f} "
                  f"| 硬停損:{stop_price} ({stop_loss_pct*100:.0f}%) "
                  f"| 停利:{target_price} ({take_profit_pct*100:.0f}%) "
                  f"| ATR追蹤:進場後浮盈>{atr_activate_pct*100:.0f}%啟動")

    return entry_ref, stop_price, target_price, action


def _calc_watchlist_score(r) -> int:
    """
    觀察分數 (0~100)：數字越高代表越接近進場條件，越值得觀察。
    各維度滿分合計 100：
      趨勢  30 — close/sma50/sma200 相對位置
      量縮  25 — vol_ratio 越低越好
      收斂  25 — convergence_ratio 越低越好
      型態  20 — 高低點數量 + bar_span
    """
    score = 0

    # 趨勢分 (0~30)
    close  = r.diag_close  or 0
    sma50  = r.diag_sma50  or 0
    sma200 = r.diag_sma200 or 0
    if close > 0 and sma50 > 0 and sma200 > 0:
        if close > sma50 > sma200:
            score += 30
        elif close > sma200:
            score += 15
        elif close > sma50:
            score += 10

    # 量縮分 (0~25)：vol_ratio < 0.75 滿分，越高扣越多
    vr = r.diag_vol_ratio or 1.0
    if vr <= 0.5:
        score += 25
    elif vr <= 0.75:
        score += int(25 * (0.75 - vr) / 0.25 + 12)
    elif vr <= 1.0:
        score += int(12 * (1.0 - vr) / 0.25)

    # 收斂分 (0~25)：ratio < 0.3 滿分，未達則按比例
    cr = r.convergence_ratio if (r.convergence_ratio and 0 < r.convergence_ratio < 1) else 1.0
    if cr <= 0.3:
        score += 25
    elif cr <= 0.6:
        score += int(25 * (0.6 - cr) / 0.3)

    # 型態分 (0~20)：高低點各3個以上 + bar_span >= 20
    pk = r.diag_peak_count   or 0
    vk = r.diag_valley_count or 0
    bs = r.bar_span          or 0
    if pk >= 3 and vk >= 3:
        score += 10
    elif pk >= 2 and vk >= 2:
        score += 5
    if bs >= 20:
        score += 10
    elif bs >= 10:
        score += 5

    return min(score, 100)


def _save_diag_csv(all_results, supply_eng, path):
    import csv as _csv
    from data.stock_list import get_stock_name
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 只保留最後幾關被淘汰的（最值得觀察），早期淘汰不寫入
    WATCH_STAGES = {'RATIO', 'SPAN', 'BREAKOUT', ''}  # '' = 通過但未進入評分
    rows = sorted(
        [r for r in all_results if r.failure_stage in WATCH_STAGES],
        key=lambda r: -_calc_watchlist_score(r)
    )
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        fieldnames = [
            'watchlist_score', 'failure_stage', 'ticker', 'name',
            'vol_ratio', 'convergence_ratio', 'bar_span',
            'peak_count', 'valley_count', 'reason'
        ]
        w = _csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({
                'watchlist_score':   _calc_watchlist_score(r),
                'failure_stage':     STAGE_LABEL.get(r.failure_stage, r.failure_stage),
                'ticker':            r.ticker,
                'name':              get_stock_name(r.ticker),
                'vol_ratio':         round(r.diag_vol_ratio, 3),
                'convergence_ratio': round(r.convergence_ratio, 3) if r.convergence_ratio and r.convergence_ratio < 1 else '',
                'bar_span':          r.bar_span or '',
                'peak_count':        r.diag_peak_count,
                'valley_count':      r.diag_valley_count,
                'reason':            r.reason,
            })
    logger.info(f"  診斷 CSV 已輸出至: {path}")


def run_scanner(tickers: list):
    logger.info(f"=== 台股精準全自動評分篩選器 v2 (反轉邏輯) | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # ============================================================
    # Step 0: 大盤環境偵測 (Market Regime)
    # ============================================================
    regime = MarketRegimeDetector()
    logger.info("[Step 0] 執行大盤環境偵測器 (連續評分)...")
    m_score = regime.get_market_score()
    logger.info(f" [+] 市場環境分數: {m_score:.1f}/100")

    market_status = regime.get_market_status()
    for name, info in market_status.items():
        if 'detail' in info:
            d = info['detail']
            logger.info(f"     {name}: 位置={d['position']:.0f} 斜率={d['slope']:.0f} 動能={d['momentum']:.0f}")

    if FORCE_SAFE_TRADE and m_score < MARKET_SCORE_THRESHOLD:
        logger.warning(
            f" [!] 市場環境目前不適合做多 (分數 {m_score:.1f} < {MARKET_SCORE_THRESHOLD})，程式終止以降低風險。"
        )
        return []

    # ============================================================
    # Step 1: 初始化各模組
    # ============================================================
    fetcher = StockFetcher(db_path=DB_PATH)
    cleaner = DataCleaner(
        min_vol_lots=_cleaner.get("min_vol_lots", 500),
        min_bars=_cleaner.get("min_bars", 60),
    )
    recognizer = PatternRecognizer(
        order=_pattern.get("order", 5),
        min_points=_pattern.get("min_points", 3),
        min_bars=_pattern.get("min_bars", 20),
        convergence_threshold=_pattern.get("convergence_threshold", 0.3),
        long_only=_pattern.get("long_only", True),
        trend_filter=_pattern.get("trend_filter", True),
        volume_dryup=_pattern.get("volume_dryup", True),
    )
    visualizer = Visualizer(output_dir=OUTPUT_DIR)
    sector_eng = SectorRotationEngine()
    supply_eng = SupplyChainEngine()
    chip_eng   = ChipAnalyzer(config=CFG)
    conv_scan  = ConvergenceScanner(sector_engine=sector_eng, supply_chain_engine=supply_eng)
    scorer     = StrategyScorer()

    visualizer.cleanup_old_charts(days=CHART_CLEANUP_DAYS)

    # ============================================================
    # Step 1: 批次下載 K 線與籌碼資料
    # ============================================================
    logger.info("[Step 1.1] 批次更新全市場籌碼資料 (抓取近 15 天)...")
    fetcher.update_market_chip_data(days=15)
    
    logger.info(f"[Step 1.2] 批次下載 K 線資料 (標的數: {len(tickers)})...")
    stock_data = fetcher.fetch_batch_data(tickers, max_workers=DOWNLOAD_WORKERS)

    # ============================================================
    # Step 2: 資料清洗
    # ============================================================
    logger.info("[Step 2] 資料清洗 (過濾低流動性與樣本不足標的)...")
    valid_data = cleaner.filter_batch(stock_data)
    valid_tickers = list(valid_data.keys())

    # ============================================================
    # Step 3: 全市場型態掃描 (不過濾族群！ — 反轉邏輯的核心)
    # ============================================================
    logger.info(f"[Step 3] 全市場三角收斂掃描 (共 {len(valid_data)} 檔，不過濾族群)...")
    all_scan_results = []     # 收集所有結果（含被淘汰的）
    converging_results = []
    for ticker, df in valid_data.items():
        result = recognizer.analyze(ticker, df)
        all_scan_results.append(result)
        if result.is_converging:
            converging_results.append(result)

    _print_funnel_report(all_scan_results)

    logger.info(f"  [+] 發現 {len(converging_results)} 檔收斂標的")
    if not converging_results:
        logger.info("  本次掃描未找到任何收斂標的。")
        # 仍輸出空的 scanner CSV（只有表頭）
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(RESULTS_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=[
                '族群等級', '股票代號', '產業別', '狀態',
                '進場總分', '收盤價', '停損價', 'ATR',
                '主要產品', '籌碼分', '法人連續買超天數', '目標價', '操作建議',
            ])
            writer.writeheader()
        logger.info(f"  報表已輸出至: {RESULTS_CSV} (0 筆)")
        DIAG_CSV = os.path.join(OUTPUT_DIR, f"diag_{datetime.today().strftime('%Y%m%d')}.csv")
        _save_diag_csv(all_scan_results, supply_eng, DIAG_CSV)
        fetcher.close()
        return []

    # ============================================================
    # Step 4: 收斂密度聚合 — 預測即將輪動的族群
    # ============================================================
    logger.info("[Step 4] 收斂密度聚合分析 (反轉邏輯: 密度高 → 族群即將輪動)...")
    sector_densities = conv_scan.calculate_sector_density(converging_results, valid_tickers)
    conv_scan.print_density_report(sector_densities)

    top_sectors_df = sector_eng.get_top_sectors(valid_data, top_n=TOP_SECTOR_COUNT)
    if not top_sectors_df.empty:
        logger.info("  族群動能排行 (參考):")
        display_cols = ['sector', 'momentum', 'breadth', 'acceleration', 'composite']
        available_cols = [c for c in display_cols if c in top_sectors_df.columns]
        logger.info("\n" + top_sectors_df[available_cols].to_string(index=False))

    # ============================================================
    # Step 5: A/B/C 機會分級 (含 Leader-Laggard 偵測)
    # ============================================================
    logger.info("[Step 5] 機會分級 + Leader-Laggard 偵測...")
    opportunities = conv_scan.classify_opportunities(converging_results, sector_densities, valid_data)
    conv_scan.print_opportunity_report(opportunities)

    opp_map = {o.ticker: o for o in opportunities}

    # ============================================================
    # Step 6: 綜合評分
    # ============================================================
    logger.info("[Step 6] 綜合量化評分...")
    score_results = []
    for result in converging_results:
        opp = opp_map.get(result.ticker)
        s_score = sector_eng.get_sector_score(result.ticker, top_sectors_df)
        p_score = result.pattern_score
        b_score = result.breakout_score
        d_score = opp.density_score if opp else 0
        c_score = supply_eng.get_chain_score(result.ticker, valid_data)
        
        chip_df = db_module.load_chip_data(result.ticker, DB_PATH)
        chip_res = chip_eng.analyze(result.ticker, valid_data[result.ticker], chip_df)
        chip_score_val = chip_res['score'] if chip_res else 0.0
        chip_details = chip_res.get('details', {}) if chip_res else {}
        
        grade   = opp.grade if opp else 'C'

        final_res = scorer.calculate_total_score(
            result.ticker, m_score, s_score, p_score, b_score,
            density_score=d_score, chain_score=c_score, chip_score=chip_score_val,
            opportunity_grade=grade,
            details={
                'sector': sector_eng.get_sector(result.ticker),
                'pattern': result,
                'chain_position': supply_eng.get_position(result.ticker),
                'opportunity': opp,
                'chip': chip_details,
            }
        )

        df_ticker = valid_data.get(result.ticker, pd.DataFrame())
        atr_val = _calculate_atr(df_ticker)
        _, stop_p, target_p, _ = _build_action_plan(result, atr_val, CFG)
        bk_tag = " [突破]" if result.is_breakout else ""
        logger.info(
            f"  [{grade}] {result.ticker:8} | 總分: {final_res.total_score:5.1f} | "
            f"型態: {p_score:4.1f} | 密度: {d_score:4.1f} | "
            f"鏈: {c_score:4.1f} | 籌: {chip_score_val:4.1f} | 收斂: {result.convergence_ratio:5.1%} | "
            f"停損:{stop_p} 目標:{target_p}{bk_tag}"
        )

        score_results.append(final_res)
        visualizer.plot(result.ticker, valid_data[result.ticker], result, show=False)

    score_results = scorer.rank_results(score_results)

    # ============================================================
    # Step 7: 輸出報表 (CSV + SQLite)
    # ============================================================
    logger.info("[Step 7] 輸出精選評分報表...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    scan_date = datetime.today().strftime('%Y-%m-%d')

    # 7a. CSV 輸出 (0筆時仍輸出表頭，方便確認格式)
    with open(RESULTS_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        fieldnames = [
            '族群等級', '股票代號', '產業別', '狀態',
            '進場總分', '收盤價', '停損價', 'ATR',
            '主要產品', '籌碼分', '法人連續買超天數', '目標價', '操作建議',
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in score_results:
            pattern_detail = r.details.get('pattern')
            status = "突破進場" if r.breakout_score > 0 else "收斂觀察"

            if pattern_detail:
                df_ticker = valid_data.get(r.ticker, pd.DataFrame())
                atr_val = _calculate_atr(df_ticker)
                entry_ref, stop_p, target_p, action = _build_action_plan(
                    pattern_detail, atr_val, CFG)
                current_p = pattern_detail.current_price
            else:
                atr_val, stop_p, target_p, action = 0, 0, 0, ""
                current_p = 0

            writer.writerow({
                '族群等級':       r.opportunity_grade,
                '股票代號':       r.ticker,
                '產業別':         r.details.get('sector', ''),
                '狀態':           status,
                '進場總分':       round(r.total_score, 2),
                '收盤價':         round(current_p, 2),
                '停損價':         stop_p,
                'ATR':            float(f'{atr_val:.2f}'),
                '主要產品':       supply_eng.get_product_description(r.ticker),
                '籌碼分':         round(r.chip_score, 2),
                '法人連續買超天數': r.details.get('chip', {}).get('trust_streak', 0),
                '目標價':         target_p,
                '操作建議':       action,
            })
    logger.info(f"  報表已輸出至: {RESULTS_CSV}")

    if score_results:
        # 7b. SQLite 持久化
        db_module.save_scan_results(DB_PATH, scan_date, score_results, supply_eng, opp_map)
        logger.info(f"  掃描結果已寫入 SQLite: {DB_PATH} (表: scan_results)")
    else:
        logger.info("  本次掃描未找到符合條件的標的。")

    # 診斷 CSV（不論有無通過標的都輸出）
    DIAG_CSV = os.path.join(OUTPUT_DIR, f"diag_{datetime.today().strftime('%Y%m%d')}.csv")
    _save_diag_csv(all_scan_results, supply_eng, DIAG_CSV)

    a_count = sum(1 for r in score_results if r.opportunity_grade == 'A')
    b_count = sum(1 for r in score_results if r.opportunity_grade == 'B')
    c_count = sum(1 for r in score_results if r.opportunity_grade == 'C')
    logger.info(
        f"=== 掃描完成！{len(tickers)} 檔 -> {len(valid_data)} 檔合格 "
        f"-> {len(converging_results)} 檔收斂 (A:{a_count} B:{b_count} C:{c_count}) ==="
    )
    fetcher.close()
    return score_results


def check_and_update_chips(force: bool = False):
    """檢查大戶資料是否過期或缺失，自動啟動 TDCC 爬蟲"""
    from data.chip_crawler import TDCCCrawler
    from data.database import get_connection
    
    should_update = force
    if not should_update:
        # 檢查指標股 2330 的最新大戶資料日期
        try:
            with get_connection() as conn:
                cursor = conn.execute(
                    "SELECT MAX(date) FROM chip_data WHERE ticker='2330.TW' AND big_player_1000 IS NOT NULL"
                )
                res = cursor.fetchone()
                if not res or not res[0]:
                    should_update = True
                else:
                    last_update = datetime.strptime(res[0], '%Y-%m-%d')
                    # 超過 7 天視為過期 (集保週資料通常週五/週末更新)
                    if (datetime.today() - last_update).days >= 7:
                        should_update = True
        except Exception:
            should_update = True
            
    if should_update:
        logger.info("[Step 0] 籌碼資料過期或缺失，啟動自動補齊...")
        try:
            crawler = TDCCCrawler()
            crawler.run_update()
        except Exception as e:
            logger.error(f"自動更新籌碼失敗: {e}")
    else:
        logger.info("[Step 0] 籌碼資料尚在有效期限內，跳過自動更新")


if __name__ == "__main__":
    sample_mode = '--sample' in sys.argv
    force_update = '--update-chip' in sys.argv

    # 執行 Step 0: 自動籌碼補齊
    check_and_update_chips(force=force_update)

    if sample_mode:
        logger.info("[模式] 樣本測試模式 (5 檔)")
        tickers = SAMPLE_TICKERS
    else:
        logger.info("[模式] 全台股掃描模式，正在取得清單...")
        tickers = get_all_tickers(use_cache=True)
        if not tickers:
            logger.warning("清單取得失敗，切換為樣本模式")
            tickers = SAMPLE_TICKERS

    results = run_scanner(tickers)
