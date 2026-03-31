import yfinance as yf
import pandas as pd
import sys
import os

# Add project root to sys.path to import get_all_tickers
sys.path.append(os.getcwd())
from data.stock_list import get_all_tickers

def scan_gainers():
    print("Starting full market scan for high gainers (5y)...")
    tickers = get_all_tickers()
    tickers = [t for t in tickers if t.endswith('.TW') or t.endswith('.TWO')]
    
    results = []
    batch_size = 50
    # For speed and reliability, use 1wk interval for 5y span
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i+batch_size]
        print(f"Processing batch {i//batch_size + 1}/{(len(tickers)//batch_size)+1}...")
        try:
            # Download Close price for the batch
            # Note: Grouping helps with rate limits but might fail if one ticker is invalid
            data = yf.download(batch, period="5y", interval="1wk", progress=False)['Close']
            
            if isinstance(data, pd.Series):
                # Handle single ticker result
                ticker = batch[0]
                s = data.dropna()
                if not s.empty:
                    start_price = s.iloc[0]
                    max_price = s.max()
                    gain = max_price / start_price
                    if gain >= 3.0:
                        results.append({'ticker': ticker, 'gain': gain})
                continue

            for ticker in data.columns:
                s = data[ticker].dropna()
                if s.empty: continue
                
                start_price = s.iloc[0]
                max_price = s.max()
                if start_price == 0: continue
                
                gain = max_price / start_price
                if gain >= 3.0: # >200% Profit (3x scale)
                    results.append({
                        'ticker': ticker,
                        'gain': round(gain, 2),
                        'start_val': round(start_price, 2),
                        'max_val': round(max_price, 2)
                    })
        except Exception as e:
            print(f"Error in batch starting with {batch[0]}: {e}")
            
    df = pd.DataFrame(results)
    output_path = r'C:\Users\Terry\PycharmProjects\Stock\high_gainers_5y.csv'
    df.to_csv(output_path, index=False)
    print(f"\nDone! Found {len(df)} stocks with >2x gains.")
    print(f"Results saved to: {output_path}")

if __name__ == "__main__":
    scan_gainers()
