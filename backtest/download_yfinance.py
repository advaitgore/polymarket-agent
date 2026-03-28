import os
import yfinance as yf
import pandas as pd
import json

def get_unique_tickers(json_path):
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    tickers = set()
    for theme in data.get("themes", []):
        for t in theme.get("tickers", []):
            tickers.add(t)
    return list(tickers)

def download_data():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    
    # Path to correlations.json (assumes backtest is parallel to pipeline)
    corr_path = os.path.join(base_dir, "..", "pipeline", "correlations.json")
    
    try:
        tickers = get_unique_tickers(corr_path)
        print(f"Found {len(tickers)} unique tickers: {tickers}")
    except Exception as e:
        print(f"Error reading correlations.json: {e}")
        return

    # yfinance only allows 60 days of 15m data. This is perfect for the 30-day Polymarket window.
    print("Downloading 60 days of 15m interval data...")
    
    all_data = []
    
    for ticker in tickers:
        print(f"Fetching {ticker}...")
        try:
            # Download 60 days of 15-minute data
            df = yf.download(ticker, period="60d", interval="15m", progress=False)
            if df.empty:
                print(f"  Warning: No data for {ticker}")
                continue
            
            # Reset index to get Datetime as a column
            df = df.reset_index()
            # If the columns are a MultiIndex (which happens in newer yfinance versions), flatten them
            if isinstance(df.columns, pd.MultiIndex):
                # Usually it looks like (Price, Ticker) -> we just want Price (Close, Open, etc)
                df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

            # Rename Datetime column to 'timestamp'
            df = df.rename(columns={"Datetime": "timestamp", "Close": "close", "Open": "open", "High": "high", "Low": "low", "Volume": "volume"})
            
            # Convert timestamp to UTC ISO string for consistency
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Keep standard OHLCV columns for ATR and Volume filtering
            cols_to_keep = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            df = df[cols_to_keep].copy()
            df['symbol'] = ticker
            
            all_data.append(df)
        except Exception as e:
            print(f"  Error fetching {ticker}: {e}")
            
    if not all_data:
        print("No data fetched.")
        return
        
    final_df = pd.concat(all_data, ignore_index=True)
    
    # Sort by timestamp
    final_df = final_df.sort_values(by="timestamp")
    
    out_file = os.path.join(data_dir, "yfinance_history.csv")
    final_df.to_csv(out_file, index=False)
    print(f"\nSaved {len(final_df)} rows to {out_file}")

if __name__ == "__main__":
    download_data()
