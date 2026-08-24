import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.database import get_connection, DB_PATH, load_precomputed_results, load_dataframe, get_open_paper_trades, init_db
from core.paper_trader import PaperTrader
from utils.logger import get_logger

logger = get_logger(__name__)

def get_recent_trade_dates(limit=2):
    with get_connection(DB_PATH) as conn:
        cursor = conn.execute("SELECT DISTINCT scan_date FROM scan_results ORDER BY scan_date DESC LIMIT ?", (limit,))
        dates = [r[0] for r in cursor.fetchall()]
        return dates

def main():
    init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--date", help="Today's date (YYYY-MM-DD). Default is the latest scan_date.")
    args = parser.parse_args()
    
    dates = get_recent_trade_dates(limit=10)
    if len(dates) < 2:
        print("Not enough scan history to run paper trading.")
        return
        
    today_date = args.date if args.date else dates[0]
    
    yesterday_date = None
    for d in dates:
        if d < today_date:
            yesterday_date = d
            break
            
    if not yesterday_date:
        print("Could not find previous trading date.")
        return
        
    print(f"=== Paper Trading Simulator ===")
    print(f"Today: {today_date}, Using Signals from: {yesterday_date}")
    
    # PaperTrader 直接吃 config.yaml 原始 dict，確保與回測/向前驗證同一組參數
    import yaml
    with open(args.config, 'r', encoding='utf-8') as f:
        raw_config = yaml.safe_load(f)
        
    trader = PaperTrader(config=raw_config, db_path=DB_PATH)
    
    signals = load_precomputed_results(DB_PATH, yesterday_date, yesterday_date).get(yesterday_date, [])
    
    active_trades = get_open_paper_trades(DB_PATH)
    tickers_to_load = set(t['ticker'] for t in active_trades)
    for sig in signals:
        if sig['is_breakout']:
            tickers_to_load.add(sig['ticker'])
            
    print(f"Loading data for {len(tickers_to_load)} tickers...")
    stock_data = {}
    for ticker in tickers_to_load:
        df = load_dataframe(ticker, DB_PATH)
        stock_data[ticker] = df
        
    print(f"\n[1] Processing Exits & Stops...")
    exit_actions = trader.process_exits(today_date, stock_data)
    for a in exit_actions:
        print("  -", a)
    if not exit_actions:
        print("  - No exit actions today.")
        
    print(f"\n[2] Processing Entries (from {yesterday_date} signals)...")
    entry_actions = trader.process_entries(today_date, signals, stock_data)
    for a in entry_actions:
        print("  -", a)
    if not entry_actions:
        print("  - No new entries today.")
        
    print(f"\n[3] Portfolio Summary")
    open_trades = get_open_paper_trades(DB_PATH)
    if open_trades:
        for t in open_trades:
            print(f"  - {t['ticker']} | {t['shares']}股 | 買均價: {t['buy_price']:.2f} | 停損: {t['stop_loss']} | 狀態: {t['remaining_ratio']*100}%")
    else:
        print("  - 尚無持倉")
        
if __name__ == "__main__":
    main()
