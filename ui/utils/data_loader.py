"""Data loading utilities — reads latest CSV and SQLite outputs."""
import glob
import os
import sqlite3
import pandas as pd
from datetime import date


DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "stock_data.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "output")


def _latest_file(pattern: str) -> str | None:
    files = sorted(glob.glob(os.path.join(OUTPUT_DIR, pattern)))
    return files[-1] if files else None


_EN_TO_ZH = {
    "grade": "族群等級",
    "ticker": "股票代號",
    "sector": "產業別",
    "status": "狀態",
    "total_score": "進場總分",
    "current_price": "收盤價",
    "chip_score": "籌碼分",
    "trust_streak": "法人連續買超天數",
    "main_product": "主要產品",
    "action_plan": "操作建議",
}


def load_scanner() -> pd.DataFrame:
    """Return the most recent scanner_YYYYMMDD.csv, normalising column names."""
    path = _latest_file("scanner_*.csv")
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    # Remap English column names used in older files
    df.rename(columns=_EN_TO_ZH, inplace=True)
    return df


def load_diag() -> pd.DataFrame:
    """Return the most recent diag_YYYYMMDD.csv."""
    path = _latest_file("diag_*.csv")
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    return df


def load_scan_results(days: int = 30) -> pd.DataFrame:
    """Return recent scan_results rows from SQLite."""
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        return pd.DataFrame()
    try:
        with sqlite3.connect(db) as conn:
            df = pd.read_sql_query(
                "SELECT * FROM scan_results ORDER BY scan_date DESC LIMIT ?",
                conn, params=(days * 200,)
            )
        return df
    except Exception:
        return pd.DataFrame()


def latest_scan_date() -> str:
    path = _latest_file("scanner_*.csv")
    if path is None:
        return str(date.today())
    return os.path.basename(path).replace("scanner_", "").replace(".csv", "")


def latest_market_score() -> float | None:
    df = load_scan_results(days=1)
    if df.empty or "market_score" not in df.columns:
        return None
    val = df["market_score"].dropna()
    return float(val.iloc[0]) if len(val) else None


def list_chart_pngs() -> dict[str, str]:
    """Return {ticker_stub: full_path} for chart PNGs in output/."""
    pngs = glob.glob(os.path.join(OUTPUT_DIR, "*.png"))
    result = {}
    for p in pngs:
        name = os.path.basename(p)
        result[name] = p
    return result
