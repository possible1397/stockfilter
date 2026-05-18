"""
strategies/momentum/daily_runner.py
RS Breakout + Sector Resonance 每日掃描執行器

執行方式 (從專案根目錄):
    python strategies/momentum/daily_runner.py
    python strategies/momentum/daily_runner.py --rs-threshold 70
    python strategies/momentum/daily_runner.py --workers 4 --output-dir output
"""
import argparse
import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.sector_rotation import THEME_SECTOR_MAP
from data.fetcher import StockFetcher
from data.cleaner import DataCleaner
from strategies.momentum.scanner import MomentumScanner, MomentumSignal
from utils.logger import setup_logging, get_logger

setup_logging(log_dir="logs", level="INFO")
logger = get_logger(__name__)

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="RS Breakout + Sector Resonance 動能掃描")
parser.add_argument("--rs-threshold", type=float, default=80.0,
                    help="RS 百分位門檻 (預設 80)")
parser.add_argument("--high-pct",     type=float, default=0.97,
                    help="近 52 週高點比率門檻 (預設 0.97 = 3%%以內)")
parser.add_argument("--min-resonance",type=int,   default=2,
                    help="族群共振最少檔數 (預設 2)")
parser.add_argument("--stop-loss",    type=float, default=7.0,
                    help="停損 %% (預設 7)")
parser.add_argument("--take-profit",  type=float, default=15.0,
                    help="目標停利 %% (預設 15)")
parser.add_argument("--workers",      type=int,   default=6,
                    help="下載執行緒數 (預設 6)")
parser.add_argument("--db",           default="stock_data.db")
parser.add_argument("--output-dir",   default="output")
args = parser.parse_args()

TODAY = datetime.today().strftime("%Y%m%d")
OUTPUT_CSV = os.path.join(args.output_dir, f"momentum_{TODAY}.csv")


# ── 取得掃描標的 ───────────────────────────────────────────────────────────────

def get_universe() -> list[str]:
    """
    掃描標的 = THEME_SECTOR_MAP 的所有股票 (加上 .TW / .TWO suffix)
    這 161 檔是 AI/半導體/電子主題股的精選宇宙
    """
    # 先全給 .TW，_resolve_tickers_from_db 會從 DB 修正成正確 suffix (.TW 或 .TWO)
    tickers = [sc + ".TW" for sc in THEME_SECTOR_MAP]
    return _resolve_tickers_from_db(tickers)


def _resolve_tickers_from_db(raw_tickers: list[str]) -> list[str]:
    """
    從 DB 確認每個 short code 對應的正確 ticker (帶正確 suffix)
    若 DB 無記錄，保留原格式。
    """
    import sqlite3
    import pandas as pd

    try:
        with sqlite3.connect(args.db) as conn:
            db_tickers = set(
                pd.read_sql("SELECT DISTINCT ticker FROM daily_prices", conn)["ticker"].tolist()
            )
    except Exception:
        return raw_tickers

    resolved = []
    for t in raw_tickers:
        sc = t.split(".")[0]
        # 優先找 .TW，其次 .TWO
        if sc + ".TW" in db_tickers:
            resolved.append(sc + ".TW")
        elif sc + ".TWO" in db_tickers:
            resolved.append(sc + ".TWO")
        else:
            resolved.append(sc + ".TW")  # 預設，等 fetcher 去下載
    return list(dict.fromkeys(resolved))  # 去重、保序


# ── 下載 / 更新資料 ────────────────────────────────────────────────────────────

def update_data(tickers: list[str]) -> dict:
    """增量更新 tickers 的 K 線資料，回傳 {ticker: DataFrame}"""
    logger.info(f"[Runner] 更新 {len(tickers)} 檔資料...")
    fetcher = StockFetcher(db_path=args.db)
    stock_data = fetcher.fetch_batch_data(tickers, max_workers=args.workers)

    cleaner = DataCleaner(min_bars=60, min_vol_lots=0)  # 動能股不限制量能門檻
    valid = cleaner.filter_batch(stock_data)
    logger.info(f"[Runner] 有效資料: {len(valid)} 檔")
    return valid


# ── 輸出 ──────────────────────────────────────────────────────────────────────

def save_csv(signals: list[MomentumSignal], path: str) -> None:
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    fieldnames = [
        "ticker", "rs_score", "high_ratio", "is_new_high",
        "current_price", "stop_price", "target_price",
        "themes", "resonant_peers", "score",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in signals:
            writer.writerow({
                "ticker":         s.ticker,
                "rs_score":       s.rs_score,
                "high_ratio":     f"{s.high_ratio:.4f}",
                "is_new_high":    s.is_new_high,
                "current_price":  s.current_price,
                "stop_price":     s.stop_price,
                "target_price":   s.target_price,
                "themes":         "/".join(s.themes),
                "resonant_peers": ",".join(s.resonant_peers),
                "score":          s.score,
            })
    logger.info(f"[Runner] 結果已存至: {path}")


def print_report(signals: list[MomentumSignal], date_str: str) -> None:
    logger.info("=" * 65)
    logger.info(f"  RS 動能掃描  {date_str}  (共振條件: >={args.min_resonance} 檔同族群)")
    logger.info(f"  RS >= {args.rs_threshold}%  |  近高 >= {args.high_pct:.0%}  |  停損 -{args.stop_loss}%  |  目標 +{args.take_profit}%")
    logger.info("=" * 65)

    if not signals:
        logger.info("  今日無符合條件的動能訊號")
        logger.info("=" * 65)
        return

    logger.info(f"  {'代碼':<10} {'RS':>6} {'近高':>7} {'現價':>8} {'停損':>8} {'目標':>8}  主題")
    logger.info("-" * 65)
    for s in signals:
        new_high_mark = " ★" if s.is_new_high else "  "
        logger.info(
            f"  {s.ticker:<10} {s.rs_score:>5.1f}% {s.high_ratio:>6.1%} "
            f"{s.current_price:>8.2f} {s.stop_price:>8.2f} {s.target_price:>8.2f}"
            f"{new_high_mark} {'/'.join(s.themes[:2])}"
        )
        if s.resonant_peers:
            logger.info(f"  {'':10}  共振同伴: {', '.join(s.resonant_peers[:4])}")

    logger.info("=" * 65)
    logger.info(f"  共 {len(signals)} 個訊號  |  ★ = 創 52 週新高")
    logger.info("=" * 65)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info(f"[Runner] RS Breakout + Sector Resonance 動能掃描  {TODAY}")

    tickers = get_universe()
    logger.info(f"[Runner] 掃描宇宙: {len(tickers)} 檔 (THEME_SECTOR_MAP)")

    stock_data = update_data(tickers)
    if not stock_data:
        logger.error("[Runner] 無有效資料，結束")
        return

    scanner = MomentumScanner(
        rs_threshold=args.rs_threshold,
        high_pct=args.high_pct,
        min_resonance=args.min_resonance,
        stop_loss_pct=args.stop_loss / 100,
        take_profit_pct=args.take_profit / 100,
    )

    signals = scanner.scan(stock_data)

    date_str = datetime.today().strftime("%Y-%m-%d")
    print_report(signals, date_str)
    save_csv(signals, OUTPUT_CSV)

    logger.info(f"[Runner] 完成。VCP 策略請執行: python main.py")


if __name__ == "__main__":
    main()
