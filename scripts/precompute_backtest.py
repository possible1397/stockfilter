"""
scripts/precompute_backtest.py
逐日預計算完整策略訊號並存入 SQLite，供回測引擎直接查表使用。

修正記錄 (2026-03-28):
  - market_score 改用 MarketRegimeDetector 實際計算各日市場分數，不再 hardcode 60
  - PatternRecognizer 改從 config.yaml 讀取參數，與即時選股條件保持一致
"""
import os
import sys
import yaml
import pandas as pd
import yfinance as yf
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data.database as db_module
from core.recognizer import PatternRecognizer
from core.chip_analysis import ChipAnalyzer
from core.scorer import StrategyScorer
from core.convergence_scanner import ConvergenceScanner
from core.sector_rotation import SectorRotationEngine
from core.supply_chain import SupplyChainEngine
from utils.logger import get_logger

logger = get_logger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"找不到 {_CONFIG_PATH}，使用預設值")
        return {}


from utils.regime import regime_score_at


class Precomputer:
    def __init__(self, db_path: str = db_module.DB_PATH):
        self.db_path = db_path
        cfg = _load_config()
        pattern_cfg = cfg.get("pattern", {})
        self.top_sector_count = int(cfg.get("scanner", {}).get("top_sector_count", 10))

        # PatternRecognizer — 從 config.yaml 讀取，與即時選股保持一致
        self.recognizer = PatternRecognizer(
            order=pattern_cfg.get("order", 5),
            min_points=pattern_cfg.get("min_points", 3),
            min_bars=pattern_cfg.get("min_bars", 20),
            convergence_threshold=pattern_cfg.get("convergence_threshold", 0.3),
            long_only=pattern_cfg.get("long_only", True),
            trend_filter=pattern_cfg.get("trend_filter", True),
            volume_dryup=pattern_cfg.get("volume_dryup", True),
            max_slope_pct=pattern_cfg.get("max_slope_pct", 0.1),
        )
        self.chip_analyzer = ChipAnalyzer(config=cfg)
        self.scorer = StrategyScorer()
        self.sector_eng = SectorRotationEngine()
        self.supply_eng = SupplyChainEngine()
        self.conv_scan = ConvergenceScanner(
            sector_engine=self.sector_eng,
            supply_chain_engine=self.supply_eng,
        )

        # 預載入指數歷史資料，用於計算各日市場分數 (優先讀 DB，離線可跑)
        logger.info("[Precomputer] 載入指數歷史資料 (^TWII, ^TWOII)...")
        self._tse_data = db_module.load_dataframe('^TWII', self.db_path)
        self._otc_data = db_module.load_dataframe('^TWOII', self.db_path)
        if self._tse_data.empty or self._otc_data.empty:
            logger.info("[Precomputer] DB 無指數資料，改由 yfinance 下載...")
            self._tse_data = yf.download('^TWII', period="7y", progress=False)
            self._otc_data = yf.download('^TWOII', period="7y", progress=False)
        logger.info("[Precomputer] 指數資料載入完成")

    def _get_market_score(self, date_str: str) -> float:
        """計算指定日期的綜合市場分數 (TSE 60% + OTC 40%，共用 utils.regime)"""
        ts = pd.Timestamp(date_str)
        tse = regime_score_at(self._tse_data, ts)
        otc = regime_score_at(self._otc_data, ts)
        return round(tse * 0.6 + otc * 0.4, 1)

    def get_trading_dates(self, start_date: str, end_date: str):
        """從資料庫取得所有交易日"""
        with db_module.get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM daily_prices WHERE date BETWEEN ? AND ? ORDER BY date ASC",
                (start_date, end_date)
            ).fetchall()
            return [r[0] for r in rows]

    def precompute_day(self, date_str: str, tickers, price_cache: dict, chip_cache: dict):
        """預計算單日掃描結果 (完整策略邏輯，使用記憶體快取)"""
        # 實際市場分數 (修正：不再 hardcode 60)
        market_score = self._get_market_score(date_str)

        target_ts = pd.Timestamp(date_str)

        # 第一階段：型態分析
        converging_results = []
        valid_data = {}
        for t in tickers:
            df = price_cache.get(t)
            if df is None or df.empty:
                continue
            df_up_to = df[df.index <= target_ts]
            if len(df_up_to) < 40:
                continue
            valid_data[t] = df_up_to
            res = self.recognizer.analyze(t, df_up_to)
            if res.is_converging:
                converging_results.append(res)

        if not converging_results:
            db_module.save_scan_results(self.db_path, date_str, [], None, {})
            return

        # 第二階段：族群密度與機會分級
        # 修正：補上「該日」的族群動能排行 (top_sectors_df)。
        # 舊版傳空 DataFrame → sector_score 永遠是常數、A 級永遠不可能出現，
        # 導致回測驗證的評分邏輯與實盤 (main.py) 不一致。
        sector_densities = self.conv_scan.calculate_sector_density(
            converging_results, list(valid_data.keys()))
        top_sectors_df = self.sector_eng.get_top_sectors(
            valid_data, top_n=self.top_sector_count)
        opportunities = self.conv_scan.classify_opportunities(
            converging_results, sector_densities, valid_data, top_sectors_df)
        opp_map = {o.ticker: o for o in opportunities}

        # 第三階段：綜合評分
        score_results = []
        for res in converging_results:
            ticker = res.ticker
            opp = opp_map.get(ticker)
            s_score = self.sector_eng.get_sector_score(ticker, top_sectors_df)
            d_score = opp.density_score if opp else 0
            c_score = self.supply_eng.get_chain_score(ticker, valid_data)

            df_chip = chip_cache.get(ticker, pd.DataFrame())
            df_chip_up_to = df_chip[df_chip.index <= target_ts] if not df_chip.empty else pd.DataFrame()
            chip_res = self.chip_analyzer.analyze(ticker, valid_data[ticker], df_chip_up_to)
            chip_score_val = chip_res['score'] if chip_res else 0.0

            grade = opp.grade if opp else 'C'

            final_res = self.scorer.calculate_total_score(
                ticker, market_score, s_score, res.pattern_score, res.breakout_score,
                density_score=d_score, chain_score=c_score, chip_score=chip_score_val,
                opportunity_grade=grade,
                details={'pattern': res, 'sector': self.sector_eng.get_sector(ticker)},
            )
            score_results.append(final_res)

        if score_results:
            db_module.save_scan_results(self.db_path, date_str, score_results, None, {})
            logger.info(f"  [{date_str}] market={market_score:.0f} | 儲存 {len(score_results)} 筆")

    def run(self, start_date: str, end_date: str, ticker_limit: int = 1700):
        """執行預計算主迴圈"""
        dates = self.get_trading_dates(start_date, end_date)
        if not dates:
            logger.error("查無交易日，請先執行 main.py 下載資料")
            return

        # 取得活躍標的
        with db_module.get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT ticker, COUNT(*) as bars FROM daily_prices "
                "GROUP BY ticker HAVING bars >= 200 ORDER BY bars DESC LIMIT ?",
                (ticker_limit,)
            ).fetchall()
            tickers = [r[0] for r in rows]

        logger.info(f"[Precompute] 載入 {len(tickers)} 檔股票資料到記憶體...")
        price_cache = {}
        chip_cache = {}
        for t in tickers:
            price_cache[t] = db_module.load_dataframe(t, self.db_path)
            chip_cache[t] = db_module.load_chip_data(t, self.db_path)

        logger.info(f"[Precompute] 開始計算 {len(dates)} 天的掃描結果...")
        skipped = 0
        for date_str in dates:
            if self._check_exists(date_str):
                skipped += 1
                continue
            self.precompute_day(date_str, tickers, price_cache, chip_cache)

        logger.info(f"[Precompute] 完成！跳過已存在: {skipped} 天")

    def _check_exists(self, date_str: str) -> bool:
        with db_module.get_connection(self.db_path) as conn:
            res = conn.execute(
                "SELECT COUNT(*) FROM scan_results WHERE scan_date = ?", (date_str,)
            ).fetchone()
            return res[0] > 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="預計算回測掃描結果")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-03-26")
    parser.add_argument("--limit", type=int, default=1700, help="最多處理幾檔股票")
    args = parser.parse_args()

    computer = Precomputer()
    computer.run(args.start, args.end, ticker_limit=args.limit)
