"""Read and display the backtest results."""
import os
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(BASE, "data", "backtest_trades.csv")

if not os.path.exists(CSV):
    print("No backtest_trades.csv found. Run backtest_engine.py first.")
    exit()

df = pd.read_csv(CSV)
EQUITY_START = 1000.0

print(f"\n{'='*70}")
print(f"  BACKTEST RESULTS")
print(f"{'='*70}")
print(f"  Total Trades:         {len(df)}")

if df.empty:
    print("  No trades were generated.")
    exit()

total_pnl = df["realized_pnl"].sum()
final_equity = EQUITY_START + total_pnl
pct = (total_pnl / EQUITY_START) * 100

print(f"  Starting Equity:      ${EQUITY_START:,.2f}")
print(f"  Final Equity:         ${final_equity:,.2f}")
print(f"  Total P&L:            ${total_pnl:+,.2f}  ({pct:+.1f}%)")

wins = df[df["realized_pnl"] > 0]
losses = df[df["realized_pnl"] <= 0]
print(f"  Winners:              {len(wins)}")
print(f"  Losers:               {len(losses)}")
print(f"  Win Rate:             {len(wins)/len(df)*100:.1f}%")

if not wins.empty:
    print(f"  Avg Win:              ${wins['realized_pnl'].mean():+.2f}")
if not losses.empty:
    print(f"  Avg Loss:             ${losses['realized_pnl'].mean():+.2f}")

# Max drawdown
cumulative = df["realized_pnl"].cumsum() + EQUITY_START
peak = cumulative.expanding().max()
drawdowns = (cumulative - peak)
max_dd = drawdowns.min()
print(f"  Max Drawdown:         ${max_dd:.2f}")

# Close reasons
print(f"\n  Close Reasons:")
for reason, cnt in df["close_reason"].value_counts().items():
    print(f"    {reason:<15s} {cnt:>5d}")

# Sides
print(f"\n  Side Distribution:")
for side, cnt in df["side"].value_counts().items():
    print(f"    {side:<6s} {cnt:>5d}")

# Performance by theme
print(f"\n  {'Theme':<25s} {'Trades':>7s} {'WinRate':>8s} {'TotalPnL':>10s}")
print(f"  {'-'*25} {'-'*7} {'-'*8} {'-'*10}")
for theme, grp in df.groupby("theme"):
    tw = len(grp[grp["realized_pnl"] > 0])
    wr = tw / len(grp) * 100 if len(grp) > 0 else 0
    tp = grp["realized_pnl"].sum()
    print(f"  {theme:<25s} {len(grp):>7d} {wr:>7.1f}% ${tp:>+9.2f}")

# Performance by instrument
print(f"\n  {'Symbol':<10s} {'Trades':>7s} {'WinRate':>8s} {'TotalPnL':>10s}")
print(f"  {'-'*10} {'-'*7} {'-'*8} {'-'*10}")
for sym, grp in df.groupby("symbol"):
    tw = len(grp[grp["realized_pnl"] > 0])
    wr = tw / len(grp) * 100 if len(grp) > 0 else 0
    tp = grp["realized_pnl"].sum()
    print(f"  {sym:<10s} {len(grp):>7d} {wr:>7.1f}% ${tp:>+9.2f}")

# Sample trades
print(f"\n  Top 5 Winning Trades:")
top_wins = df.nlargest(5, "realized_pnl")
for _, t in top_wins.iterrows():
    print(f"    {t['side']:4s} {t['symbol']:5s} ${t['realized_pnl']:+.2f}  {t['close_reason']:10s}  {str(t.get('market_question',''))[:50]}")

print(f"\n  Top 5 Losing Trades:")
top_losses = df.nsmallest(5, "realized_pnl")
for _, t in top_losses.iterrows():
    print(f"    {t['side']:4s} {t['symbol']:5s} ${t['realized_pnl']:+.2f}  {t['close_reason']:10s}  {str(t.get('market_question',''))[:50]}")

print(f"{'='*70}")
