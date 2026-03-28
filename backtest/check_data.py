import os
import pandas as pd

base = os.path.dirname(os.path.abspath(__file__))

yf_file = os.path.join(base, "data", "yfinance_history.csv")
pm_file = os.path.join(base, "data", "polymarket_history.csv")

print("=== Yahoo Finance Data ===")
if os.path.exists(yf_file):
    df = pd.read_csv(yf_file)
    print(f"Rows: {len(df)}")
    print(f"Unique symbols: {df['symbol'].nunique()}")
    print(f"Symbols: {sorted(df['symbol'].unique())}")
    print(f"Date range: {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(df.head(3))
else:
    print("NOT FOUND")

print("\n=== Polymarket Data ===")
if os.path.exists(pm_file):
    df = pd.read_csv(pm_file)
    print(f"Rows: {len(df)}")
    print(f"Unique markets: {df['market_id'].nunique()}")
    print(f"Date range: {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(df.head(3))
else:
    print("NOT FOUND - need to run download_polymarket.py")
