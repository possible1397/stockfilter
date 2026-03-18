import sqlite3
import pandas as pd
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stock_data.db')


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """
    每次呼叫都建立一個新的 SQLite 連線 (Thread-Safe 模式)
    多執行緒環境下每個 thread 應使用自己的 connection，而非共用。
    """
    conn = sqlite3.connect(db_path)
    return conn


def init_db(db_path: str = DB_PATH):
    """初始化資料庫，建立 daily_prices 資料表 (若尚未存在)"""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_prices (
            ticker  TEXT,
            date    TEXT,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            PRIMARY KEY (ticker, date)
        )
    ''')
    conn.commit()
    conn.close()


def save_dataframe(ticker: str, df: pd.DataFrame, db_path: str = DB_PATH):
    """
    將 yfinance 回傳的 DataFrame 寫入 SQLite (每次呼叫獨立開關 connection)
    使用 INSERT OR REPLACE 避免主鍵衝突
    """
    if df is None or df.empty:
        return

    df = df.copy()
    # yfinance history() 回傳的 index 是 DatetimeIndex (Date)
    df.reset_index(inplace=True)

    # 相容 yfinance 舊版 (Price, Open...) 與新版 (Date, Open...)
    # 統一欄位名稱
    rename_map = {}
    for col in df.columns:
        rename_map[col] = col.lower().strip()
    df.rename(columns=rename_map, inplace=True)

    # 確保 date 欄位存在 (有時 yfinance 回傳 'price' 作為 index)
    if 'datetime' in df.columns:
        df.rename(columns={'datetime': 'date'}, inplace=True)

    # 若 date 欄是 datetime 型態，轉成字串
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

    df['ticker'] = ticker

    cols = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
    # 只保留存在的欄位
    cols = [c for c in cols if c in df.columns]
    df = df[cols].dropna(subset=['date'])

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        placeholders = ', '.join('?' * len(cols))
        col_names    = ', '.join(cols)
        sql = f"INSERT OR REPLACE INTO daily_prices ({col_names}) VALUES ({placeholders})"
        rows = [tuple(row) for row in df[cols].itertuples(index=False)]
        cursor.executemany(sql, rows)
        conn.commit()
    except Exception as e:
        print(f"  [{ticker}] SQLite 寫入錯誤: {e}")
    finally:
        conn.close()


def load_dataframe(ticker: str, db_path: str = DB_PATH) -> pd.DataFrame:
    """從 SQLite 讀取特定股票的所有資料，回傳 DataFrame (index 為 DatetimeIndex)"""
    conn = get_connection(db_path)
    try:
        query = ("SELECT date, open, high, low, close, volume "
                 "FROM daily_prices WHERE ticker = ? ORDER BY date ASC")
        df = pd.read_sql_query(query, conn, params=(ticker,))
    except Exception as e:
        print(f"  [{ticker}] SQLite 讀取錯誤: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()

    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        # 欄位首字大寫 (與 yfinance 一致)
        df.columns = [c.capitalize() for c in df.columns]
    return df


def get_latest_date(ticker: str, db_path: str = DB_PATH) -> str | None:
    """取得資料庫中該股票最後一筆日期 (YYYY-MM-DD)，若無則回傳 None"""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM daily_prices WHERE ticker = ?", (ticker,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result and result[0] else None


# ===== 測試區塊 =====
if __name__ == "__main__":
    init_db()
    print(f"SQLite DB 已初始化: {DB_PATH}")
