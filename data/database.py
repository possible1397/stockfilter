import sqlite3
import pandas as pd
import os
from contextlib import contextmanager
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stock_data.db')


@contextmanager
def get_connection(db_path: str = DB_PATH):
    """
    Context manager：自動開啟並關閉 SQLite 連線，支援 WAL 模式提升多執行緒效能。
    使用方式:
        with get_connection(db_path) as conn:
            conn.execute(...)
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = DB_PATH):
    """初始化資料庫，建立所有資料表 (若尚未存在)"""
    with get_connection(db_path) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS daily_prices (
                ticker  TEXT,
                date    TEXT,
                open    REAL,
                high    REAL,
                low     REAL,
                close   REAL,
                volume  INTEGER,
                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS scan_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date       TEXT    NOT NULL,
                ticker          TEXT    NOT NULL,
                grade           TEXT,
                total_score     REAL,
                market_score    REAL,
                sector_score    REAL,
                density_score   REAL,
                chain_score     REAL,
                pattern_score   REAL,
                breakout_score  REAL,
                sector          TEXT,
                chain_position  TEXT,
                status          TEXT,
                current_price   REAL,
                convergence_ratio REAL,
                bar_span        INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_scan_results_date
                ON scan_results (scan_date);
            CREATE INDEX IF NOT EXISTS idx_scan_results_ticker
                ON scan_results (ticker);
        ''')
    logger.debug(f"SQLite DB 已初始化: {db_path}")


def save_dataframe(ticker: str, df: pd.DataFrame, db_path: str = DB_PATH):
    """
    將 yfinance 回傳的 DataFrame 寫入 SQLite
    使用 INSERT OR REPLACE 避免主鍵衝突
    """
    if df is None or df.empty:
        return

    df = df.copy()
    df.reset_index(inplace=True)

    rename_map = {col: col.lower().strip() for col in df.columns}
    df.rename(columns=rename_map, inplace=True)

    if 'datetime' in df.columns:
        df.rename(columns={'datetime': 'date'}, inplace=True)

    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

    df['ticker'] = ticker

    cols = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
    cols = [c for c in cols if c in df.columns]
    df = df[cols].dropna(subset=['date'])

    try:
        with get_connection(db_path) as conn:
            placeholders = ', '.join('?' * len(cols))
            col_names    = ', '.join(cols)
            sql = f"INSERT OR REPLACE INTO daily_prices ({col_names}) VALUES ({placeholders})"
            rows = [tuple(row) for row in df[cols].itertuples(index=False)]
            conn.executemany(sql, rows)
    except Exception as e:
        logger.error(f"[{ticker}] SQLite 寫入錯誤: {e}")


def load_dataframe(ticker: str, db_path: str = DB_PATH) -> pd.DataFrame:
    """從 SQLite 讀取特定股票的所有資料，回傳 DataFrame (index 為 DatetimeIndex)"""
    try:
        with get_connection(db_path) as conn:
            query = ("SELECT date, open, high, low, close, volume "
                     "FROM daily_prices WHERE ticker = ? ORDER BY date ASC")
            df = pd.read_sql_query(query, conn, params=(ticker,))
    except Exception as e:
        logger.error(f"[{ticker}] SQLite 讀取錯誤: {e}")
        return pd.DataFrame()

    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.columns = [c.capitalize() for c in df.columns]
    return df


def get_latest_date(ticker: str, db_path: str = DB_PATH) -> Optional[str]:
    """取得資料庫中該股票最後一筆日期 (YYYY-MM-DD)，若無則回傳 None"""
    try:
        with get_connection(db_path) as conn:
            cursor = conn.execute(
                "SELECT MAX(date) FROM daily_prices WHERE ticker = ?", (ticker,)
            )
            result = cursor.fetchone()
            return result[0] if result and result[0] else None
    except Exception as e:
        logger.error(f"[{ticker}] 查詢最新日期失敗: {e}")
        return None


def save_scan_results(db_path: str, scan_date: str, score_results: list,
                      supply_eng, opp_map: dict):
    """將本次掃描結果寫入 scan_results 表，供日後歷史查詢。"""
    if not score_results:
        return
    rows = []
    for r in score_results:
        pattern = r.details.get('pattern')
        rows.append((
            scan_date,
            r.ticker,
            r.opportunity_grade,
            r.total_score,
            r.market_score,
            r.sector_score,
            r.density_score,
            r.chain_score,
            r.pattern_score,
            r.breakout_score,
            r.details.get('sector', ''),
            r.details.get('chain_position', ''),
            "Breakout" if r.breakout_score > 0 else "Converging",
            pattern.current_price     if pattern else None,
            pattern.convergence_ratio if pattern else None,
            pattern.bar_span          if pattern else None,
        ))
    try:
        with get_connection(db_path) as conn:
            conn.executemany("""
                INSERT INTO scan_results
                    (scan_date, ticker, grade, total_score,
                     market_score, sector_score, density_score, chain_score,
                     pattern_score, breakout_score, sector, chain_position,
                     status, current_price, convergence_ratio, bar_span)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows)
        logger.debug(f"已儲存 {len(rows)} 筆掃描結果至 scan_results ({scan_date})")
    except Exception as e:
        logger.error(f"寫入 scan_results 失敗: {e}")


# ===== 測試區塊 =====
if __name__ == "__main__":
    init_db()
    logger.info(f"SQLite DB 已初始化: {DB_PATH}")
