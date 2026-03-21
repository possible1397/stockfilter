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
# ================================================


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
    conv_scan  = ConvergenceScanner(sector_engine=sector_eng, supply_chain_engine=supply_eng)
    scorer     = StrategyScorer()

    visualizer.cleanup_old_charts(days=CHART_CLEANUP_DAYS)

    # ============================================================
    # Step 1: 批次下載 K 線資料
    # ============================================================
    logger.info(f"[Step 1] 批次下載 K 線資料 (標的數: {len(tickers)})...")
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
    converging_results = []
    for ticker, df in valid_data.items():
        result = recognizer.analyze(ticker, df)
        if result.is_converging:
            converging_results.append(result)

    logger.info(f"  [+] 發現 {len(converging_results)} 檔收斂標的")
    if not converging_results:
        logger.info("  本次掃描未找到任何收斂標的。")
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
        grade   = opp.grade if opp else 'C'

        final_res = scorer.calculate_total_score(
            result.ticker, m_score, s_score, p_score, b_score,
            density_score=d_score, chain_score=c_score,
            opportunity_grade=grade,
            details={
                'sector': sector_eng.get_sector(result.ticker),
                'pattern': result,
                'chain_position': supply_eng.get_position(result.ticker),
                'opportunity': opp,
            }
        )

        bk_tag = " [突破]" if result.is_breakout else ""
        logger.info(
            f"  [{grade}] {result.ticker:8} | 總分: {final_res.total_score:5.1f} | "
            f"型態: {p_score:4.1f} | 密度: {d_score:4.1f} | "
            f"鏈: {c_score:4.1f} | 收斂: {result.convergence_ratio:5.1%}{bk_tag}"
        )

        score_results.append(final_res)
        visualizer.plot(result.ticker, valid_data[result.ticker], result, show=False)

    score_results = scorer.rank_results(score_results)

    # ============================================================
    # Step 7: 輸出報表 (CSV + SQLite)
    # ============================================================
    logger.info("[Step 7] 輸出精選評分報表...")
    if score_results:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        scan_date = datetime.today().strftime('%Y-%m-%d')

        # 7a. CSV 輸出
        with open(RESULTS_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = [
                'grade', 'ticker', 'sector', 'main_product', 'chain_position', 'status',
                'total_score', 'market_score', 'sector_score',
                'density_score', 'chain_score',
                'pattern_score', 'breakout_score',
                'current_price', 'resistance_price', 'support_price',
                'convergence_ratio', 'bar_span', 'converging_peers',
                'all_chain_peers',
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in score_results:
                pattern_detail = r.details.get('pattern')
                opp = opp_map.get(r.ticker)
                status = "Breakout" if r.breakout_score > 0 else "Converging"
                writer.writerow({
                    'grade':             r.opportunity_grade,
                    'ticker':            r.ticker,
                    'sector':            r.details.get('sector', ''),
                    'main_product':      supply_eng.get_product_description(r.ticker),
                    'chain_position':    r.details.get('chain_position', ''),
                    'status':            status,
                    'total_score':       r.total_score,
                    'market_score':      r.market_score,
                    'sector_score':      r.sector_score,
                    'density_score':     r.density_score,
                    'chain_score':       r.chain_score,
                    'pattern_score':     r.pattern_score,
                    'breakout_score':    r.breakout_score,
                    'current_price':     pattern_detail.current_price    if pattern_detail else '',
                    'resistance_price':  pattern_detail.resistance_price if pattern_detail else '',
                    'support_price':     pattern_detail.support_price    if pattern_detail else '',
                    'convergence_ratio': pattern_detail.convergence_ratio if pattern_detail else '',
                    'bar_span':          pattern_detail.bar_span          if pattern_detail else '',
                    'converging_peers':  ', '.join(opp.converging_peers) if opp and hasattr(opp, 'converging_peers') else '',
                    'all_chain_peers':   " | ".join([
                        f"[{k}] {','.join(v[:15])}{'等' if len(v) > 15 else ''}"
                        for k, v in supply_eng.get_related_tickers(r.ticker).items() if v
                    ]),
                })
        logger.info(f"  報表已輸出至: {RESULTS_CSV}")

        # 7b. SQLite 持久化
        db_module.save_scan_results(DB_PATH, scan_date, score_results, supply_eng, opp_map)
        logger.info(f"  掃描結果已寫入 SQLite: {DB_PATH} (表: scan_results)")

    else:
        logger.info("  本次掃描未找到符合條件的標的。")

    a_count = sum(1 for r in score_results if r.opportunity_grade == 'A')
    b_count = sum(1 for r in score_results if r.opportunity_grade == 'B')
    c_count = sum(1 for r in score_results if r.opportunity_grade == 'C')
    logger.info(
        f"=== 掃描完成！{len(tickers)} 檔 -> {len(valid_data)} 檔合格 "
        f"-> {len(converging_results)} 檔收斂 (A:{a_count} B:{b_count} C:{c_count}) ==="
    )
    fetcher.close()
    return score_results


if __name__ == "__main__":
    sample_mode = '--sample' in sys.argv

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
