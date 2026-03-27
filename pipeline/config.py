"""
Central configuration for the Polymarket Trading System.

All data files live entirely inside the Perplexity Computer sandbox filesystem:
  data/markets.db  — SQLite (local to sandbox, NOT on the user's machine)
  data/signals.csv — signal feed
  data/trades.csv  — simulated paper trades (no broker connectivity)

No external API keys or secrets are required to run the pipeline.

IMPORTANT: This system runs entirely in the Perplexity Computer sandbox.
There is NO connection to IBKR, TWS, IB Gateway, or any broker API.
All trade execution is local simulation only.
"""
import os

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
DB_PATH          = os.path.join(BASE_DIR, "data", "markets.db")
SIGNALS_CSV      = os.path.join(BASE_DIR, "data", "signals.csv")
TRADES_CSV       = os.path.join(BASE_DIR, "data", "trades.csv")
CORRELATIONS_JSON= os.path.join(BASE_DIR, "correlations.json")
LOG_DIR          = os.path.join(BASE_DIR, "logs")
LOG_FILE         = os.path.join(LOG_DIR, "system.log")

# ── Polymarket (public REST API — no auth) ────────────────────────────────────
POLYMARKET_BASE       = "https://gamma-api.polymarket.com"   # market metadata + prices
POLYMARKET_CLOB       = "https://clob.polymarket.com"        # order-book prices
FETCH_LIMIT           = 100     # markets per page
PRICE_MOVE_THRESHOLD  = 5.0     # pp absolute move to flag as signal

# ── Simulated trade execution (no broker) ────────────────────────────────────
# All trades are local simulations.  No network connection to any broker.
# Fills use the most recent price from markets.db (outcomes.current_price).
SIM_EXECUTION_VENUE = "simulated"
SIM_BROKER          = "none"

# Legacy alias — kept for compatibility
SIM_TRADE_SIZE    = 5
IBKR_TRADE_SIZE   = SIM_TRADE_SIZE
IBKR_ENABLED      = False   # permanently disabled; left as False sentinel only

# ── Virtual account & risk parameters ────────────────────────────────────────
# Starting equity (USD).  Grows/shrinks with realized P&L each cycle.
ACCOUNT_EQUITY_USD      = 1_000.0

# Risk 1% of current equity per trade (≈ $10 on a $1 000 account).
# Dollar amount risked = RISK_PCT * current_equity
RISK_PCT                = 0.01

# Minimum risk‑reward ratio required before taking a trade.
# Take‑profit must be ≥ RR_MIN × stop‑loss distance.
RR_MIN                  = 2.0

# Maximum number of simultaneously open positions.
# Each trade risks RISK_PCT * equity ($10 on a $1,000 account).
# With MAX_OPEN_POSITIONS = 10, total dollars-at-risk never exceeds $100.
# The old notional-based cap (30% of equity) is wrong for high-priced stocks
# like SPY/QQQ where one position already exceeds $300 notional.
MAX_OPEN_POSITIONS      = 10
MAX_EXPOSURE_PCT        = 0.30  # kept for legacy reference, not used for gating

# Maximum number of trading days a position may stay open before forced close.
MAX_HOLD_TRADING_DAYS   = 10

# Stop‑loss distance expressed as a fraction of the entry price.
# e.g. 0.05 → stop is 5 % away from entry (used as a fallback when recent
# price history is too thin to estimate volatility).
DEFAULT_SL_FRACTION    = 0.05

# ── Scheduling ────────────────────────────────────────────────────────────────
# Weekdays 07:30–18:00 Eastern → MARKET_HOURS_INTERVAL_MINUTES
# All other times              → OFF_HOURS_INTERVAL_MINUTES (or skip if no new data)
MARKET_HOURS_INTERVAL_MINUTES = 15
OFF_HOURS_INTERVAL_MINUTES    = 60
POLL_INTERVAL_MINUTES         = MARKET_HOURS_INTERVAL_MINUTES  # legacy alias

# ── News checker ─────────────────────────────────────────────────────────────
# Uses public, unauthenticated sources only (DuckDuckGo API, public RSS feeds).
# No API key is needed or used.  When no source is reachable, signals are tagged
# as "unverified (no news check)" rather than failing silently.
NEWS_CHECK_MAX_PER_CYCLE = 50   # cap per cycle to keep runtime reasonable

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
