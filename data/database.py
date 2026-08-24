"""data/database.py — SQLite 存取層"""
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

            CREATE TABLE IF NOT EXISTS chip_data (
                ticker          TEXT,
                date            TEXT,
                trust_net_buy   INTEGER,
                foreign_net_buy INTEGER,
                dealer_net_buy  INTEGER,
                big_player_400  REAL,
                big_player_1000 REAL,
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
                chip_score      REAL,
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

            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                status TEXT NOT NULL,
                buy_date TEXT NOT NULL,
                buy_price REAL NOT NULL,
                buy_reason TEXT,
                shares INTEGER NOT NULL,
                remaining_ratio REAL DEFAULT 1.0,
                partial_sell_date TEXT,
                partial_sell_price REAL,
                sell_date TEXT,
                sell_price REAL,
                sell_reason TEXT,
                highest_price REAL,
                stop_loss REAL,
                take_profit REAL,
                pnl_pct REAL,
                notes TEXT
            );
        ''')
    logger.debug(f"SQLite DB 已初始化: {db_path}")


def save_dataframe(ticker: str, df: pd.DataFrame, db_path: str = DB_PATH,
                   replace: bool = False):
    """
    將 yfinance 回傳的 DataFrame 寫入 SQLite

    replace=False (預設): INSERT OR IGNORE，已存在的日期不覆蓋，
      確保同一天重複執行時掃描結果穩定。
    replace=True: INSERT OR REPLACE，用於「偵測到除權息/分割調整」後的
      全量重抓 — yfinance 的還原股價在每次配息後整條序列都會改變，
      若永不更新舊 K 線，新舊調整基準混雜會在序列上留下假跳空，
      汙染 pivot 與突破判斷。
    """
    if df is None or df.empty:
        return

    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        # 取第一層索引 (例如 'Close')，忽略第二層 (例如 '2330.TW')
        df.columns = df.columns.get_level_values(0)
    df.reset_index(inplace=True)

    rename_map = {str(col): str(col).lower().strip() for col in df.columns}
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
            verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
            sql = f"{verb} INTO daily_prices ({col_names}) VALUES ({placeholders})"
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


def save_chip_data(ticker: str, df: pd.DataFrame, db_path: str = DB_PATH):
    """將法人籌碼與大戶持股資料寫入 SQLite 的 chip_data 表"""
    if df is None or df.empty:
        return

    df = df.copy()
    if 'date' not in df.columns and df.index.name == 'date':
        df.reset_index(inplace=True)
    elif 'date' not in df.columns and 'Datetime' in df.columns:
        df.rename(columns={'Datetime': 'date'}, inplace=True)
        
    rename_map = {col: col.lower().strip() for col in df.columns}
    df.rename(columns=rename_map, inplace=True)

    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

    df['ticker'] = ticker

    cols = ['ticker', 'date', 'trust_net_buy', 'foreign_net_buy', 'dealer_net_buy', 'big_player_400', 'big_player_1000']
    cols = [c for c in cols if c in df.columns]
    df = df[cols].dropna(subset=['date'])

    try:
        with get_connection(db_path) as conn:
            placeholders = ', '.join('?' * len(cols))
            col_names    = ', '.join(cols)
            # Use REPLACE to update latest big_player info if it comes later
            sql = f"INSERT OR REPLACE INTO chip_data ({col_names}) VALUES ({placeholders})"
            rows = [tuple(row) for row in df[cols].itertuples(index=False)]
            conn.executemany(sql, rows)
    except Exception as e:
        logger.error(f"[{ticker}] SQLite chip_data 寫入錯誤: {e}")


def save_bulk_chip_data(df: pd.DataFrame, db_path: str = DB_PATH):
    """批次將全市場某日的籌碼資料寫入 SQLite (包含法人與大戶)"""
    if df is None or df.empty:
        return
        
    df = df.copy()
    # 自動識別存在於 DataFrame 中的欄位
    available_cols = ['ticker', 'date', 'trust_net_buy', 'foreign_net_buy', 
                      'dealer_net_buy', 'big_player_400', 'big_player_1000']
    cols = [c for c in available_cols if c in df.columns]
    
    if 'ticker' not in cols or 'date' not in cols:
        logger.error("Bulk save 缺少必要欄位: ticker 或 date")
        return

    try:
        with get_connection(db_path) as conn:
            placeholders = ', '.join('?' * len(cols))
            col_names    = ', '.join(cols)
            
            # 使用 ON CONFLICT 更新現有記錄中的非空欄位
            # 這能確保若我們先存了法人籌碼，後存大戶資料時，不會互相覆蓋成空值
            update_set = ', '.join([f"{c}=excluded.{c}" for c in cols if c not in ['ticker', 'date']])
            
            upsert_sql = f"""
                INSERT INTO chip_data ({col_names}) VALUES ({placeholders})
                ON CONFLICT(ticker, date) DO UPDATE SET {update_set}
            """
            
            rows = [tuple(row) for row in df[cols].itertuples(index=False)]
            conn.executemany(upsert_sql, rows)
    except Exception as e:
        logger.error(f"SQLite chip_data 批次寫入錯誤: {e}")


def load_chip_data(ticker: str, db_path: str = DB_PATH) -> pd.DataFrame:
    """從 SQLite 讀取特定股票的籌碼資料，回傳 DataFrame (index 為 DatetimeIndex)"""
    try:
        with get_connection(db_path) as conn:
            query = ("SELECT date, trust_net_buy, foreign_net_buy, dealer_net_buy, big_player_400, big_player_1000 "
                     "FROM chip_data WHERE ticker = ? ORDER BY date ASC")
            df = pd.read_sql_query(query, conn, params=(ticker,))
    except Exception as e:
        logger.error(f"[{ticker}] SQLite chip_data 讀取錯誤: {e}")
        return pd.DataFrame()

    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
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
            r.chip_score,
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
                     chip_score, pattern_score, breakout_score, sector, 
                     chain_position, status, current_price, convergence_ratio, bar_span)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows)
        logger.debug(f"已儲存 {len(rows)} 筆掃描結果至 scan_results ({scan_date})")
    except Exception as e:
        logger.error(f"寫入 scan_results 失敗: {e}")

def load_precomputed_results(db_path: str, start_date: str, end_date: str) -> dict:
    """從 scan_results 載入特定區間的掃描結果，回傳 {date: [signal, ...]}"""
    results_by_date = {}
    try:
        with get_connection(db_path) as conn:
            query = """
                SELECT scan_date, ticker, total_score, grade, status,
                       density_score, chain_score, chip_score,
                       pattern_score, breakout_score
                FROM scan_results
                WHERE scan_date BETWEEN ? AND ?
                ORDER BY scan_date ASC, total_score DESC
            """
            cursor = conn.execute(query, (start_date, end_date))
            for row in cursor.fetchall():
                d_str, ticker, score, grade, status, \
                    density, chain, chip, pattern, breakout = row
                if d_str not in results_by_date:
                    results_by_date[d_str] = []
                results_by_date[d_str].append({
                    'ticker':        ticker,
                    'score':         score,        # 備用 (若無法即時合成時使用)
                    'grade':         grade,
                    'is_breakout':   (status == 'Breakout'),
                    # 子分數 — 供 engine 用當日實際 market_score 即時合成總分
                    'density_score': density  or 0.0,
                    'chain_score':   chain    or 0.0,
                    'chip_score':    chip     or 0.0,
                    'pattern_score': pattern  or 0.0,
                    'breakout_score':breakout or 0.0,
                })
        return results_by_date
    except Exception as e:
        logger.error(f"讀取 precomputed_results 失敗: {e}")
        return {}


def get_open_paper_trades(db_path: str = DB_PATH) -> list:
    """取得所有 status='OPEN' 的紙上交易紀錄，回傳 dict 列表"""
    for attempt in (1, 2):
        try:
            with get_connection(db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM paper_trades WHERE status = 'OPEN'")
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError as e:
            # 舊資料庫尚未建立 paper_trades 表時自動補建，只重試一次
            if attempt == 1 and "no such table" in str(e):
                logger.info("paper_trades 表不存在，自動初始化資料庫...")
                init_db(db_path)
                continue
            logger.error(f"讀取 open paper_trades 失敗: {e}")
            return []
        except Exception as e:
            logger.error(f"讀取 open paper_trades 失敗: {e}")
            return []
    return []

def upsert_paper_trade(trade: dict, db_path: str = DB_PATH) -> int:
    """
    新增或更新紙上交易。若 trade dict 中有 'id' 則為更新，無則新增。
    回傳該筆資料的 id。
    """
    try:
        with get_connection(db_path) as conn:
            if 'id' in trade and trade['id']:
                # Update
                set_clause = ', '.join([f"{k}=?" for k in trade.keys() if k != 'id'])
                values = [trade[k] for k in trade.keys() if k != 'id'] + [trade['id']]
                sql = f"UPDATE paper_trades SET {set_clause} WHERE id=?"
                conn.execute(sql, values)
                return trade['id']
            else:
                # Insert
                keys = list(trade.keys())
                placeholders = ', '.join(['?'] * len(keys))
                col_names = ', '.join(keys)
                values = [trade[k] for k in keys]
                sql = f"INSERT INTO paper_trades ({col_names}) VALUES ({placeholders})"
                cursor = conn.execute(sql, values)
                return cursor.lastrowid
    except Exception as e:
        logger.error(f"寫入 paper_trade 失敗: {e}")
        return -1

# ===== 測試區塊 =====
if __name__ == "__main__":
    init_db()
    logger.info(f"SQLite DB 已初始化: {DB_PATH}")
