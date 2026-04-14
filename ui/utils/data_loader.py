"""Data loading utilities — reads latest CSV, SQLite, and position outputs."""
import glob
import json
import os
import sqlite3
import pandas as pd
from datetime import date


DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "stock_data.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "output")
POSITIONS_JSON = os.path.join(os.path.dirname(__file__), "..", "..", "my_positions.json")
CONFIG_YAML = os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")


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


def load_scanner(scan_date: str | None = None) -> pd.DataFrame:
    """Return scanner CSV for a specific date, or the latest."""
    if scan_date:
        path = os.path.join(OUTPUT_DIR, f"scanner_{scan_date.replace('-', '')}.csv")
        if not os.path.exists(path):
            path = _latest_file("scanner_*.csv")
    else:
        path = _latest_file("scanner_*.csv")
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    df.rename(columns=_EN_TO_ZH, inplace=True)
    return df


def load_diag(scan_date: str | None = None) -> pd.DataFrame:
    """Return diag CSV for a specific date, or the latest."""
    if scan_date:
        path = os.path.join(OUTPUT_DIR, f"diag_{scan_date.replace('-', '')}.csv")
        if not os.path.exists(path):
            path = _latest_file("diag_*.csv")
    else:
        path = _latest_file("diag_*.csv")
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    return df


def load_positions_report(scan_date: str | None = None) -> pd.DataFrame:
    """Return the positions monitoring CSV."""
    if scan_date:
        path = os.path.join(OUTPUT_DIR, f"positions_{scan_date}.csv")
        if not os.path.exists(path):
            path = _latest_file("positions_*.csv")
    else:
        path = _latest_file("positions_*.csv")
    if path is None:
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    return df


def load_positions_config() -> list[dict]:
    """Read my_positions.json for raw position data."""
    path = os.path.abspath(POSITIONS_JSON)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Support both list format and dict-with-positions format
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("positions", [])
        return []
    except Exception:
        return []


def load_app_config() -> dict:
    """Load config.yaml for display in sidebar."""
    path = os.path.abspath(CONFIG_YAML)
    if not os.path.exists(path):
        return {}
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


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
    raw = os.path.basename(path).replace("scanner_", "").replace(".csv", "")
    # Convert YYYYMMDD to YYYY-MM-DD
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


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


def list_available_dates() -> list[str]:
    """Return all available scan dates from output directory."""
    files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "scanner_*.csv")))
    dates = []
    for f in files:
        raw = os.path.basename(f).replace("scanner_", "").replace(".csv", "")
        if len(raw) == 8 and raw.isdigit():
            dates.append(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
        else:
            dates.append(raw)
    return dates
