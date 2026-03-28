"""Diagnose: what probability changes occur in the data and which ones match themes."""
import os, re, json
import pandas as pd
from datetime import timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
CORR = os.path.join(BASE, "..", "pipeline", "correlations.json")
POLY = os.path.join(BASE, "data", "polymarket_history.csv")
STOCK = os.path.join(BASE, "data", "yfinance_history.csv")

# Load correlations
with open(CORR) as f:
    data = json.load(f)
themes = data["themes"]

COMPANY_OVERRIDES = {
    "nvidia": "NVDA", "microsoft": "MSFT", "apple": "AAPL",
    "google": "GOOGL", "meta ": "META", "amazon": "AMZN",
    "tesla": "TSLA", "bitcoin": "IBIT", "ethereum": "ETHA",
    "lockheed": "LMT", "exxon": "XOM", "chevron": "CVX",
}

def classify(q):
    q_l = q.lower()
    best_theme, best_score, best_entry = "none", 0, None
    for entry in themes:
        tid = entry.get("theme", "none")
        kws = entry.get("keywords", [])
        score = sum(1 for kw in kws if kw.lower() in q_l)
        if score > best_score:
            best_theme, best_score, best_entry = tid, score, entry
    
    if best_score == 0 or best_entry is None:
        return "none", "NONE", 0
    
    for kw, ticker in COMPANY_OVERRIDES.items():
        if kw in q_l:
            return best_theme, ticker, best_score
    
    return best_theme, best_entry.get("primary_ticker", "NONE"), best_score

# Load data
df_poly = pd.read_csv(POLY)
df_poly["timestamp"] = pd.to_datetime(df_poly["timestamp"], utc=True)
df_stock = pd.read_csv(STOCK)
df_stock["timestamp"] = pd.to_datetime(df_stock["timestamp"], utc=True)

stock_symbols = set(df_stock["symbol"].unique())
stock_ts = sorted(df_stock["timestamp"].unique())

# Find the overlapping time window
poly_min = df_poly["timestamp"].min()
poly_max = df_poly["timestamp"].max()
stock_min = df_stock["timestamp"].min()
stock_max = df_stock["timestamp"].max()
overlap_start = max(poly_min, stock_min)
overlap_end = min(poly_max, stock_max)
print(f"Polymarket range: {poly_min} -> {poly_max}")
print(f"Stock range:      {stock_min} -> {stock_max}")
print(f"Overlap:          {overlap_start} -> {overlap_end}")

# For each market, compute max 24h delta
print(f"\n=== Max 24h probability deltas per market ===")
all_signals = []

for mid, grp in df_poly.groupby("market_id"):
    grp = grp.sort_values("timestamp")
    question = grp.iloc[0]["question"]
    
    max_delta = 0
    max_delta_ts = None
    
    for idx in range(len(grp)):
        ts_now = grp.iloc[idx]["timestamp"]
        p_now = grp.iloc[idx]["probability"]
        
        # Find data point ~24h ago
        lookback = ts_now - timedelta(hours=24)
        older = grp[grp["timestamp"] <= lookback]
        if older.empty:
            continue
        p_old = older.iloc[-1]["probability"]
        
        delta = abs(p_now - p_old) * 100
        if delta > max_delta:
            max_delta = delta
            max_delta_ts = ts_now
    
    theme, ticker, score = classify(question)
    in_overlap = (grp["timestamp"] >= overlap_start).any() and (grp["timestamp"] <= overlap_end).any()
    has_stock = ticker in stock_symbols
    
    if max_delta > 2:  # Show anything > 2pp
        all_signals.append({
            "market_id": mid,
            "question": question[:60],
            "max_delta": max_delta,
            "theme": theme,
            "ticker": ticker,
            "score": score,
            "in_overlap": in_overlap,
            "has_stock": has_stock,
            "data_pts": len(grp),
        })

all_signals.sort(key=lambda x: x["max_delta"], reverse=True)

print(f"\nFound {len(all_signals)} markets with >2pp 24h movement:\n")
for s in all_signals[:30]:
    tradable = "[OK]" if s["theme"] != "none" and s["has_stock"] and s["in_overlap"] else "[--]"
    print(f"  {tradable} Δ={s['max_delta']:5.1f}pp | [{s['theme']:20s}] {s['ticker']:5s} (score={s['score']}) | {s['question']}")

print(f"\nMarkets where signal would be tradable:")
tradable = [s for s in all_signals if s["theme"] != "none" and s["has_stock"] and s["in_overlap"]]
print(f"  {len(tradable)} tradable signals out of {len(all_signals)}")
for s in tradable:
    print(f"  D={s['max_delta']:5.1f}pp | [{s['theme']:20s}] {s['ticker']:5s} | {s['question']}")

# Write full report to file
with open(os.path.join(BASE, "data", "signal_report.txt"), "w", encoding="utf-8") as f:
    f.write(f"Polymarket range: {poly_min} -> {poly_max}\n")
    f.write(f"Stock range:      {stock_min} -> {stock_max}\n")
    f.write(f"Overlap:          {overlap_start} -> {overlap_end}\n\n")
    f.write(f"Markets with >2pp 24h movement: {len(all_signals)}\n\n")
    for s in all_signals:
        tag = "[OK]" if s["theme"] != "none" and s["has_stock"] and s["in_overlap"] else "[--]"
        f.write(f"  {tag} D={s['max_delta']:5.1f}pp [{s['theme']:20s}] {s['ticker']:5s} (score={s['score']}) pts={s['data_pts']:4d} overlap={s['in_overlap']} | {s['question']}\n")
    f.write(f"\nTradable: {len(tradable)}\n")
    for s in tradable:
        f.write(f"  D={s['max_delta']:5.1f}pp [{s['theme']:20s}] {s['ticker']:5s} | {s['question']}\n")
print(f"\nFull report written to backtest/data/signal_report.txt")
