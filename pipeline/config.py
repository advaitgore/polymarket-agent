"""
Central configuration for the Polymarket Trading System.

All data files live inside the repository checkout on the host:
  data/markets.db  — SQLite snapshot of the current market state
  data/signals.csv — signal feed
  data/trades.csv  — simulated paper trades (no broker connectivity)

No external API keys or secrets are required to run the pipeline.

IMPORTANT: This system is designed to run on a host like Hetzner.
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
PRICE_MOVE_THRESHOLD  = 3.0     # pp absolute move to flag as signal (Optimized for 15m)
# Guard against cold-start false positives when the DB has very fresh history.
MIN_HISTORY_HOURS_FOR_SIGNAL = 12.0

# Skip near-resolved contracts where probability is already at terminal extremes.
NEAR_RESOLVED_PROB_LOW = 0.10
NEAR_RESOLVED_PROB_HIGH = 0.90

# Cooldown to avoid repeatedly trading the same market-side signal every cycle.
MARKET_SIGNAL_COOLDOWN_HOURS = 24.0

# Cooldown to avoid immediate flip trades on the same symbol after a close.
# Applied regardless of market_id or side.
SYMBOL_REENTRY_COOLDOWN_HOURS = 24.0

# Factor bucket cap: allow at most one open position per bucket.
FACTOR_BUCKETS = {
  "equity_index": ["SPY", "QQQ", "IWM"],
  "energy":       ["XLE", "XOM", "CVX", "USO"],
  "defense":      ["LMT", "NOC", "RTX"],
  "crypto":       ["IBIT", "ETHA"],
  "tech_single":  ["NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMZN", "TSLA"],
}

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

# Risk 2% of current equity per trade (≈ $20 on a $1 000 account).
# Dollar amount risked = RISK_PCT * current_equity
RISK_PCT                = 0.02

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

# ── Breakeven + trailing stop ────────────────────────────────────────────────
# Once an open position moves into profit past TRAIL_BREAKEVEN_TRIGGER_PCT of
# its take-profit distance, the stop is ratcheted to breakeven (entry). Once
# past TRAIL_STOP_TRIGGER_PCT, the stop trails behind the mark at
# TRAIL_STOP_DISTANCE_PCT of the TP distance. The stop is never widened
# (ratchet only). TIME_EXIT at MAX_HOLD_TRADING_DAYS remains the backstop for
# stagnant positions that never reach the breakeven trigger.
TRAIL_BREAKEVEN_TRIGGER_PCT = 0.50
TRAIL_STOP_TRIGGER_PCT      = 0.75
TRAIL_STOP_DISTANCE_PCT     = 0.40

# Stop‑loss distance expressed as a fraction of the entry price.
# e.g. 0.05 → stop is 5 % away from entry (used as a fallback when recent
# price history is too thin to estimate volatility).
DEFAULT_SL_FRACTION    = 0.05

# ATR-based stop parameters (daily closes).
ATR_LOOKBACK_DAYS       = 14
ATR_MULTIPLIER          = 1.5

# Minimum hold time in market hours before allowing normal SL checks.
MIN_HOLD_MARKET_HOURS   = 4

# ── Realized-vol gate ──────────────────────────────────────────────────────
# Suppress signals where correlated instrument 1-day realized vol exceeds
# this multiple of its recent 30-day average.
# Set VOL_GATE_ENABLED=False (or multiplier <= 0) to disable.
VOL_GATE_ENABLED       = True
VOL_GATE_MULTIPLIER    = 2.0

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
NEWS_CHECK_ENABLED = False
NEWS_CHECK_MAX_PER_CYCLE = 50   # cap per cycle to keep runtime reasonable

# ── Adaptive mapper controls ────────────────────────────────────────────────
# Disable adaptive routing while hardening live-vs-backtest parity.
ADAPTIVE_MAPPING_ENABLED = True

# ── energy_geopolitics theme gate ────────────────────────────────────────────
# XLE is the loosest proxy in the factor map (25% historical win rate).
# Require a high edge score before entering, and block case_by_case direction
# logic entirely. Threshold auto-relaxes once the theme's rolling win rate
# recovers above ENERGY_GEO_WINRATE_RELAX_TRIGGER.
ENERGY_GEO_EDGE_THRESHOLD_DEFAULT = 4.5
ENERGY_GEO_EDGE_THRESHOLD_RELAXED = 3.5
ENERGY_GEO_WINRATE_RELAX_TRIGGER  = 0.40
ENERGY_GEO_MIN_TRADES_FOR_GATE    = 2

# ── Edge score instrument-quality adjustment ────────────────────────────────
# The edge score is multiplied by an instrument_quality factor that blends a
# static per-theme correlation_quality (from correlations.json) with the
# theme's rolling historical win rate. This penalizes loose proxy mappings
# (e.g. XLE for a Hormuz question) and themes with poor track records.
#   history_factor     = max(0.1, rolling_win_rate)   (1.0 if insufficient data)
#   instrument_quality = BASE_WEIGHT*correlation_quality + HISTORY_WEIGHT*history_factor
# Signals whose adjusted edge falls below MIN_EDGE_SCORE are not trade-eligible.
MIN_EDGE_SCORE                    = 2.0
INSTRUMENT_QUALITY_HISTORY_WEIGHT = 0.6
INSTRUMENT_QUALITY_BASE_WEIGHT    = 0.4

# Require a minimum number of closed trades for each (theme, ticker)
# before applying adaptive weight updates.
ADAPTIVE_MIN_CLOSED_TRADES_PER_THEME_TICKER = 2

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
