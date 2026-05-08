"""
Polymarket Backtest Engine
==========================
Replays historical Polymarket probability data against historical stock prices
to simulate the exact same trading strategy used in the live pipeline.

Usage:
    python backtest/backtest_engine.py

Reads:
    backtest/data/polymarket_history.csv
    backtest/data/yfinance_history.csv
    pipeline/correlations.json

Writes:
    backtest/data/backtest_trades.csv
"""
import os
import sys
import re
import json
import uuid
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backtest")

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT  = os.path.dirname(BASE_DIR)
DATA_DIR      = os.path.join(BASE_DIR, "data")
POLY_CSV      = os.path.join(DATA_DIR, "polymarket_history.csv")
STOCK_CSV     = os.path.join(DATA_DIR, "yfinance_history.csv")
CORR_JSON     = os.path.join(PROJECT_ROOT, "pipeline", "correlations.json")
OUTPUT_CSV    = os.path.join(DATA_DIR, "backtest_trades.csv")

# ── Strategy Parameters (mirrors pipeline/config.py) ────────────────────────
ACCOUNT_EQUITY_START = 1_000.0
RISK_PCT             = 0.02       # 2% of equity per trade (Optimized Moderate Profile)
RR_MIN               = 2.0        # min reward:risk ratio
MAX_OPEN_POSITIONS   = 10
SIGNAL_THRESHOLD_PP  = 3.0        # minimum probability change in percentage points
MAX_HOLD_BARS        = 10 * 26    # ~10 trading days × 26 fifteen-minute bars per day
MIN_HISTORY_HOURS_FOR_SIGNAL = 12.0
MARKET_SIGNAL_COOLDOWN_HOURS = 24.0
SYMBOL_REENTRY_COOLDOWN_HOURS = 24.0
NEAR_RESOLVED_PROB_LOW = 0.10
NEAR_RESOLVED_PROB_HIGH = 0.90


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Vol gate can be toggled per run without editing code.
VOL_GATE_ENABLED = _env_flag("VOL_GATE_ENABLED", False)
VOL_GATE_MULTIPLIER = _env_float("VOL_GATE_MULTIPLIER", 2.0)
VOL_GATE_DIAGNOSTICS_CSV = os.getenv(
    "VOL_GATE_DIAGNOSTICS_CSV",
    os.path.join(DATA_DIR, "vol_gate_diagnostics.csv"),
)

VOL_OVERRIDES = {
    "IBIT": 0.06, "ETHA": 0.07, "MSTR": 0.10, "NVDA": 0.05,
    "TSLA": 0.07, "QQQ": 0.03, "SPY": 0.02, "TLT": 0.02,
    "IEF": 0.015, "GLD": 0.02, "XLE": 0.03, "LMT": 0.025,
    "NOC": 0.025, "RTX": 0.025, "ITA": 0.03, "XOP": 0.04,
    "USO": 0.04, "XOM": 0.03, "CVX": 0.03, "MSFT": 0.03,
    "AAPL": 0.03, "GOOGL": 0.04, "META": 0.04, "AMZN": 0.04,
    "XLF": 0.025, "XLI": 0.025, "IWM": 0.03,
}

COMPANY_OVERRIDES = {
    "nvidia": "NVDA", "nvda": "NVDA",
    "microsoft": "MSFT", "msft": "MSFT", "azure": "MSFT",
    "apple": "AAPL", "aapl": "AAPL",
    "alphabet": "GOOGL", "google": "GOOGL", "googl": "GOOGL",
    "meta ": "META", "facebook": "META", "instagram": "META",
    "amazon": "AMZN", "amzn": "AMZN", "aws": "AMZN",
    "tesla": "TSLA", "tsla": "TSLA",
    "lockheed": "LMT", "lmt": "LMT",
    "northrop": "NOC", "noc": "NOC",
    "raytheon": "RTX", "rtx": "RTX",
    "exxon": "XOM", "xom": "XOM",
    "chevron": "CVX", "cvx": "CVX",
    "bitcoin": "IBIT", "btc ": "IBIT",
    "ethereum": "ETHA", "eth ": "ETHA",
}


# ─────────────────────────────────────────────────────────────────────────────
# Classification (inlined from pipeline/detect_signals.py to avoid import issues)
# ─────────────────────────────────────────────────────────────────────────────

def load_correlations():
    with open(CORR_JSON) as f:
        data = json.load(f)
    return data.get("themes", []), data.get("non_tradable_patterns", {})

THEMES, NON_TRADABLE = None, None

def get_themes():
    global THEMES, NON_TRADABLE
    if THEMES is None:
        THEMES, NON_TRADABLE = load_correlations()
    return THEMES, NON_TRADABLE


# Additional noise patterns that slip through the correlations.json non-tradable check
# (e.g. individual sports matches, weather, esports)
EXTRA_NOISE_PATTERNS = [
    r"\bvs\.?\b",           # "Team A vs Team B" or "Player vs Player"
    r"\bo/u\b",             # Over/Under betting lines
    r"\bspread:\b",         # Point spreads
    r"\btop \d+\b",         # "Top 10 at the 2025 Masters"
    r"\bup or down\b",      # "Bitcoin Up or Down" 5-minute candle markets
    r"\bgoalscorer\b",      # Soccer goalscorer markets
    r"\bset \d+ games\b",   # Tennis set markets
    r"\bmap \d+ winner\b",  # Esports map winner
    r"\btemperature\b",     # Weather markets
    r"\btweets?\s+from\b",  # Tweet count markets (Elon Musk tweets)
    r"\bfdv above\b",       # Crypto FDV launch markets (not tradable)
    r"\bairdrop\b",         # Airdrop markets
    r"\bcounter-strike\b",  # CS:GO esports
    r"\bbo[1-5]\b",         # Best-of-N esports matches
    r"\bquadra kill\b",     # LoL esports
    r"\bgta\b",              # GTA VI references (not economically relevant)
    r"\balbum\b",            # Music album releases
    r"\bnhl\b",              # NHL hockey
    r"\bnba\b",              # NBA basketball
    r"\bnfl\b",              # NFL football
    r"\bmlb\b",              # MLB baseball
    r"\bstanley cup\b",      # NHL Stanley Cup
    r"\bworld cup\b",        # FIFA World Cup
    r"\bpublic sale\b",     # Token public sale markets
    r"\blaunch a token\b",  # Will X launch a token
    r"\bdip to\b",          # "Will X dip to $Y" price prediction (too noisy)
    r"\bhit\b.*\$\d+",     # Price-target binary markets (e.g. "hit $130")
    r"\bbelow\s+\$\d+",    # Price-threshold binary markets (e.g. "below $90")
    r"\babove\s+\$\d+",    # Price-threshold binary markets (e.g. "above $100")
]


def is_non_tradable(question: str) -> bool:
    _, patterns = get_themes()
    q = question.lower()

    # Check correlations.json patterns first
    for category, pat_list in patterns.items():
        if category == "description":
            continue
        for pat in pat_list:
            try:
                if re.search(pat.lower(), q):
                    return True
            except re.error:
                if pat.lower() in q:
                    return True

    # Check additional noise patterns
    for pat in EXTRA_NOISE_PATTERNS:
        if re.search(pat, q):
            return True

    return False


# Minimum keyword overlap score required for a valid classification.
# Score=1 is too noisy (a single keyword like "bitcoin" in "Bitcoin Up or Down"
# can match).  Require at least 2 keyword hits for confidence.
MIN_KEYWORD_SCORE = 1


def classify_question(question: str) -> Tuple[str, str]:
    """
    Returns (theme, ticker) for a market question.
    theme='none' and ticker='NONE' if non-tradable.
    """
    themes, _ = get_themes()

    if is_non_tradable(question):
        return "none", "NONE"

    q = question.lower()
    best_theme, best_score, best_entry = "none", 0, None
    for entry in themes:
        tid = entry.get("theme", "none")
        keywords = entry.get("keywords", [])
        score = sum(1 for kw in keywords if kw.lower() in q)
        if score > best_score:
            best_theme, best_score, best_entry = tid, score, entry

    # Require at least MIN_KEYWORD_SCORE keyword matches for confidence
    if best_score < MIN_KEYWORD_SCORE or best_entry is None:
        return "none", "NONE"

    # Company-specific override
    for kw, ticker in COMPANY_OVERRIDES.items():
        if kw in q:
            return best_theme, ticker

    return best_theme, best_entry.get("primary_ticker", "NONE")


def direction_logic_for_theme(theme_id: str) -> str:
    """Return configured direction_logic for the classified theme."""
    if not theme_id or theme_id == "none":
        return "higher_prob_positive_outcome_means_long"

    themes, _ = get_themes()
    for entry in themes:
        if entry.get("theme") == theme_id:
            return entry.get("direction_logic", "higher_prob_positive_outcome_means_long")

    return "higher_prob_positive_outcome_means_long"


POSITIVE_OUTCOME_LABELS = {
    "yes", "y", "true", "up", "higher", "increase", "bull", "long", "pass", "win"
}

NEGATIVE_OUTCOME_LABELS = {
    "no", "n", "false", "down", "lower", "decrease", "bear", "short", "fail", "lose"
}


DIRECTION_HINTS = {
    "higher_prob_supply_shock_or_escalation_means_long": {
        "bullish": [
            r"supply shock", r"disruption", r"strait of hormuz", r"escalat", r"attack", r"strike",
            r"conflict", r"war", r"sanction", r"blockade",
        ],
        "bearish": [
            r"ceasefire", r"peace", r"de-escalat", r"truce", r"normal", r"resume", r"open shipping",
            r"end of military operations",
        ],
    },
    "higher_prob_escalation_means_long": {
        "bullish": [
            r"escalat", r"attack", r"strike", r"missile", r"conflict", r"war", r"military operation",
            r"sanction",
        ],
        "bearish": [
            r"ceasefire", r"peace", r"de-escalat", r"truce", r"diplom", r"deal", r"withdraw",
            r"end of military operations",
        ],
    },
    "higher_prob_rate_cut_or_soft_landing_means_long_tlt": {
        "bullish": [
            r"rate cut", r"cut rates", r"fed cut", r"dovish", r"soft landing", r"disinflation",
            r"cooling inflation", r"lower yields", r"easing",
        ],
        "bearish": [
            r"rate hike", r"hike rates", r"hawkish", r"higher yields", r"inflation spike",
            r"sticky inflation", r"no rate cut",
        ],
    },
    "higher_prob_permissive_regulation_or_positive_earnings_means_long": {
        "bullish": [
            r"approve", r"approval", r"permissive", r"deregulat", r"positive earnings", r"earnings beat",
            r"beat estimates", r"guidance raise",
        ],
        "bearish": [
            r"ban", r"crackdown", r"restrict", r"antitrust", r"fine", r"earnings miss", r"guidance cut",
            r"lawsuit",
        ],
    },
    "higher_prob_positive_crypto_outcome_means_long": {
        "bullish": [
            r"approve", r"approval", r"adoption", r"institutional", r"inflows", r"all-time high",
            r"bull market",
        ],
        "bearish": [
            r"ban", r"crackdown", r"exploit", r"hack", r"lawsuit", r"rejection", r"delist",
        ],
    },
}

CASE_BY_CASE_BULLISH_HINTS = [
    r"ceasefire", r"peace", r"de-escalat", r"truce", r"deal", r"end of military operations",
    r"budget deal", r"avoid shutdown", r"stimulus", r"tax cut", r"deregulat",
]

CASE_BY_CASE_BEARISH_HINTS = [
    r"escalat", r"attack", r"strike", r"war", r"shutdown", r"default", r"tariff", r"sanction",
    r"recession", r"hard landing", r"impeach", r"crisis",
]


def _count_pattern_hits(question: str, patterns: List[str]) -> int:
    q = question.lower()
    hits = 0
    for pattern in patterns:
        try:
            if re.search(pattern, q):
                hits += 1
        except re.error:
            if pattern in q:
                hits += 1
    return hits


def _outcome_is_affirmative(outcome_name: str) -> Optional[bool]:
    label = str(outcome_name).strip().lower()
    if label in POSITIVE_OUTCOME_LABELS:
        return True
    if label in NEGATIVE_OUTCOME_LABELS:
        return False
    return None


def _resolve_tie_from_outcome(
    market_question: str,
    outcome_name: str,
    direction_logic: str,
) -> Optional[bool]:
    """
    When keyword matching ties, use outcome name to resolve direction.

    Returns True if event is bullish for the instrument, False if bearish,
    or None if ambiguous.
    """
    hints = DIRECTION_HINTS.get(str(direction_logic or "").strip().lower(), {})
    if not hints:
        return None

    bearish_present = _count_pattern_hits(market_question, hints.get("bearish", [])) > 0
    bullish_present = _count_pattern_hits(market_question, hints.get("bullish", [])) > 0

    if bearish_present and not bullish_present:
        event_is_bearish = True
    elif bullish_present and not bearish_present:
        event_is_bearish = False
    else:
        return None

    outcome_affirmative = _outcome_is_affirmative(outcome_name)
    if outcome_affirmative is None:
        return None

    if event_is_bearish:
        return False if outcome_affirmative else True
    return True if outcome_affirmative else False


def _event_is_bullish_for_symbol(
    question: str,
    direction_logic: str,
    outcome_name: str = "",
) -> Optional[bool]:
    logic = str(direction_logic or "").strip().lower()

    if logic == "higher_prob_positive_outcome_means_long":
        return True

    if logic == "case_by_case":
        bullish_hits = _count_pattern_hits(question, CASE_BY_CASE_BULLISH_HINTS)
        bearish_hits = _count_pattern_hits(question, CASE_BY_CASE_BEARISH_HINTS)
    else:
        hints = DIRECTION_HINTS.get(logic)
        if hints is None:
            return True if "means_long" in logic else None

        bullish_hits = _count_pattern_hits(question, hints["bullish"])
        bearish_hits = _count_pattern_hits(question, hints["bearish"])

    if bullish_hits == bearish_hits:
        return _resolve_tie_from_outcome(question, outcome_name, direction_logic)
    return bullish_hits > bearish_hits


def infer_outcome_sentiment(
    market_question: str,
    outcome_name: str,
    direction_logic: str,
) -> str:
    """
    Returns how a rise in this outcome's probability should map to the symbol:
      - bullish: prob-up implies BUY bias
      - bearish: prob-up implies SELL bias
    """
    event_bullish = _event_is_bullish_for_symbol(market_question, direction_logic, outcome_name)
    outcome_affirmative = _outcome_is_affirmative(outcome_name)

    if event_bullish is None:
        return "neutral"

    if outcome_affirmative is None:
        prob_up_is_bullish = event_bullish
    else:
        prob_up_is_bullish = event_bullish if outcome_affirmative else (not event_bullish)

    return "bullish" if prob_up_is_bullish else "bearish"


def determine_side(delta_pp: float, outcome_sentiment: str) -> str:
    """Determine trade side from move direction + inferred sentiment."""
    sentiment = str(outcome_sentiment).strip().lower()
    if sentiment not in {"bullish", "bearish"}:
        return "BUY" if delta_pp > 0 else "SELL"

    prob_up_is_buy = sentiment == "bullish"
    if delta_pp > 0:
        return "BUY" if prob_up_is_buy else "SELL"
    return "SELL" if prob_up_is_buy else "BUY"


def compute_edge_score(
    prob_change_pp: float,
    hours_since_move: float = 12.0,
    explained: bool = False,
    direction_logic: str = "",
) -> float:
    base = abs(prob_change_pp) / 5.0
    recency = 1.5 if hours_since_move <= 6 else (1.2 if hours_since_move <= 12 else 1.0)
    confidence = 0.5 if str(direction_logic).strip().lower() == "case_by_case" else 1.0
    return round(base * recency * confidence, 3)


def get_24h_delta_with_anchor(
    mdf: pd.DataFrame,
    ts: pd.Timestamp,
) -> Tuple[Optional[float], Optional[float], Optional[pd.Timestamp]]:
    """
    Mirror pipeline get_24h_delta behavior:
      1) Use true 24h anchor when available.
      2) If unavailable, use earliest sample only if history is mature enough.
    """
    lookback = ts - timedelta(hours=24)

    mask_now = mdf["timestamp"] <= ts
    if not mask_now.any():
        return None, None, None
    row_now = mdf.loc[mask_now].iloc[-1]
    current_price = float(row_now["probability"])

    mask_old = mdf["timestamp"] <= lookback
    if mask_old.any():
        row_old = mdf.loc[mask_old].iloc[-1]
        return float(row_old["probability"]), current_price, row_old["timestamp"]

    snap = mdf.loc[mask_now].iloc[0]
    snap_ts = snap["timestamp"]
    history_age_hours = (ts - snap_ts).total_seconds() / 3600.0
    if history_age_hours < MIN_HISTORY_HOURS_FOR_SIGNAL:
        return None, None, None

    return float(snap["probability"]), current_price, snap_ts


def estimate_hours_since_move(
    mdf: pd.DataFrame,
    ts: pd.Timestamp,
    old_price: float,
) -> float:
    """
    Estimate recency by first threshold-cross in the last 24h window.
    """
    start = ts - timedelta(hours=24)
    rows = mdf.loc[(mdf["timestamp"] >= start) & (mdf["timestamp"] <= ts)]
    if rows.empty:
        return 24.0

    first_cross_ts = None
    for _, row in rows.iterrows():
        price = float(row["probability"])
        if abs((price - old_price) * 100.0) >= SIGNAL_THRESHOLD_PP:
            first_cross_ts = row["timestamp"]
            break

    if first_cross_ts is None:
        latest_ts = rows.iloc[-1]["timestamp"]
        return max(0.0, (ts - latest_ts).total_seconds() / 3600.0)

    return max(0.0, (ts - first_cross_ts).total_seconds() / 3600.0)


def compute_realized_vol_ratio(symbol: str, signal_date, price_history_df: pd.DataFrame) -> float:
    """
    Returns ratio of 1-day realized vol to 30-day average realized vol.
    price_history_df must have columns: date, symbol, close.
    Returns 1.0 (neutral) if insufficient data.
    """
    if price_history_df.empty:
        return 1.0

    if isinstance(signal_date, pd.Timestamp):
        signal_day = signal_date.date()
    else:
        signal_day = pd.to_datetime(signal_date, utc=True).date()

    df = price_history_df[price_history_df["symbol"] == symbol].copy()
    if df.empty:
        return 1.0

    df = df.sort_values("date")
    df["returns"] = df["close"].pct_change().abs()

    df_at_or_before = df[df["date"] <= signal_day]
    if len(df_at_or_before) < 31:
        return 1.0

    one_day_vol = df_at_or_before.iloc[-1]["returns"]
    if pd.isna(one_day_vol):
        return 1.0

    thirty_day_avg = df_at_or_before.iloc[-31:-1]["returns"].mean()
    if pd.isna(thirty_day_avg) or thirty_day_avg == 0:
        return 1.0

    return float(one_day_vol / thirty_day_avg)


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_data():
    log.info("Loading Polymarket history...")
    df_poly = pd.read_csv(POLY_CSV)
    df_poly["timestamp"] = pd.to_datetime(df_poly["timestamp"], utc=True)
    log.info(f"  {len(df_poly)} rows, {df_poly['market_id'].nunique()} markets")

    log.info("Loading Yahoo Finance history...")
    df_stock = pd.read_csv(STOCK_CSV)
    df_stock["timestamp"] = pd.to_datetime(df_stock["timestamp"], utc=True)
    log.info(f"  {len(df_stock)} rows, {df_stock['symbol'].nunique()} symbols")

    return df_poly, df_stock


# ─────────────────────────────────────────────────────────────────────────────
# Core backtest loop
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest():
    df_poly, df_stock = load_data()

    # Build daily close history per symbol for realized-vol gate calculations.
    price_history_df = (
        df_stock.sort_values("timestamp")
        .assign(date=lambda x: x["timestamp"].dt.date)
        .groupby(["symbol", "date"], as_index=False)
        .agg(close=("close", "last"))
    )

    # Build a global sorted timeline of unique stock timestamps (these are the
    # 15-minute bars during which we can actually trade & mark positions).
    stock_timestamps = sorted(df_stock["timestamp"].unique())
    log.info(f"Stock timeline: {len(stock_timestamps)} bars from {stock_timestamps[0]} to {stock_timestamps[-1]}")

    # Pre-index stock prices as {timestamp -> {symbol -> close}}
    log.info("Indexing stock prices...")
    stock_by_ts: Dict[pd.Timestamp, Dict[str, float]] = {}
    for ts, grp in df_stock.groupby("timestamp"):
        stock_by_ts[ts] = dict(zip(grp["symbol"], grp["close"]))

    # Pre-index Polymarket data: for each market, build a sorted list of (ts, prob)
    log.info("Indexing Polymarket probabilities...")
    poly_by_market: Dict[str, pd.DataFrame] = {}
    for mid, grp in df_poly.groupby("market_id"):
        poly_by_market[mid] = grp.sort_values("timestamp").reset_index(drop=True)

    # Simulation state
    equity = ACCOUNT_EQUITY_START
    open_positions: List[Dict] = []
    closed_trades: List[Dict] = []
    signals_generated = 0
    signals_traded = 0
    market_side_last_trade_ts: Dict[Tuple[str, str], pd.Timestamp] = {}
    symbol_last_close_ts: Dict[str, pd.Timestamp] = {}

    vol_gate_suppressed = 0
    vol_gate_suppressed_by_symbol: Dict[str, int] = {}
    vol_gate_daily_stats: Dict[datetime.date, Dict[str, int]] = {}

    log.info(f"\n{'='*70}")
    log.info(f"STARTING BACKTEST  |  Equity: ${equity:.2f}  |  Bars: {len(stock_timestamps)}")
    log.info(f"{'='*70}\n")

    for bar_idx, ts in enumerate(stock_timestamps):
        prices = stock_by_ts.get(ts, {})
        if not prices:
            continue

        # ── 1. MARK-TO-MARKET & AUTO-CLOSE ────────────────────────────────
        still_open = []
        for pos in open_positions:
            sym = pos["symbol"]
            mark = prices.get(sym)
            if mark is None:
                still_open.append(pos)
                continue

            pos["mark_price"] = mark
            pos["bars_held"] += 1
            side_mult = 1.0 if pos["side"] == "BUY" else -1.0
            pnl = (mark - pos["entry_price"]) * pos["quantity"] * side_mult

            # Check close triggers
            close_reason = None
            if pos["side"] == "BUY":
                if mark <= pos["stop_loss"]:
                    close_reason = "SL_HIT"
                elif mark >= pos["take_profit"]:
                    close_reason = "TP_HIT"
            else:
                if mark >= pos["stop_loss"]:
                    close_reason = "SL_HIT"
                elif mark <= pos["take_profit"]:
                    close_reason = "TP_HIT"

            if pos["bars_held"] >= MAX_HOLD_BARS:
                close_reason = "TIME_EXIT"

            if close_reason:
                equity += pnl
                pos["status"] = "CLOSED"
                pos["close_date"] = str(ts)
                pos["close_reason"] = close_reason
                pos["realized_pnl"] = round(pnl, 4)
                closed_trades.append(pos)
                symbol_last_close_ts[sym] = ts
                log.info(
                    f"  CLOSE {pos['side']:4s} {sym:5s} | {close_reason:9s} | "
                    f"entry=${pos['entry_price']:.2f} exit=${mark:.2f} | "
                    f"pnl=${pnl:+.2f} | equity=${equity:.2f} | held {pos['bars_held']} bars"
                )
            else:
                still_open.append(pos)

        open_positions = still_open

        # ── 2. SIGNAL DETECTION ───────────────────────────────────────────
        candidate_signals: List[Dict] = []
        for mid, mdf in poly_by_market.items():
            old_price, new_price, anchor_ts = get_24h_delta_with_anchor(mdf, ts)
            if old_price is None or new_price is None:
                continue

            # Skip near-resolved contracts where equity follow-through edge is exhausted.
            if new_price < NEAR_RESOLVED_PROB_LOW or new_price > NEAR_RESOLVED_PROB_HIGH:
                continue

            delta_pp = (new_price - old_price) * 100.0
            if abs(delta_pp) < SIGNAL_THRESHOLD_PP:
                continue

            row_now = mdf.loc[mdf["timestamp"] <= ts].iloc[-1]
            question = row_now["question"]
            theme, instrument = classify_question(question)
            direction_logic = direction_logic_for_theme(theme)
            outcome_name = str(row_now.get("outcome_name", "YES"))
            outcome_sentiment = infer_outcome_sentiment(question, outcome_name, direction_logic)
            signals_generated += 1

            if instrument == "NONE" or instrument not in prices:
                continue

            day_key = ts.date()
            day_stats = vol_gate_daily_stats.setdefault(
                day_key,
                {"pre_gate": 0, "suppressed": 0, "post_gate": 0},
            )
            day_stats["pre_gate"] += 1

            if VOL_GATE_ENABLED:
                vol_ratio = compute_realized_vol_ratio(instrument, ts, price_history_df)
                if vol_ratio > VOL_GATE_MULTIPLIER:
                    vol_gate_suppressed += 1
                    vol_gate_suppressed_by_symbol[instrument] = vol_gate_suppressed_by_symbol.get(instrument, 0) + 1
                    day_stats["suppressed"] += 1
                    continue

            day_stats["post_gate"] += 1

            hours_since_move = estimate_hours_since_move(mdf, ts, old_price)
            edge_score = compute_edge_score(
                delta_pp,
                hours_since_move=hours_since_move,
                explained=False,
                direction_logic=direction_logic,
            )

            candidate_signals.append({
                "market_id": mid,
                "instrument": instrument,
                "theme": theme,
                "delta_pp": delta_pp,
                "question": question,
                "edge_score": edge_score,
                "direction_logic": direction_logic,
                "outcome_sentiment": outcome_sentiment,
                "hours_since_move": hours_since_move,
                "anchor_ts": anchor_ts,
            })

        # ── 3. BEST SIGNAL SELECTION (one trade per bar) ──────────────────
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            continue

        candidate_signals.sort(key=lambda x: x["edge_score"], reverse=True)
        open_symbols = {p["symbol"] for p in open_positions}
        selected = None
        for sig in candidate_signals:
            instrument = sig["instrument"]
            if instrument in open_symbols:
                continue

            if str(sig.get("outcome_sentiment", "")).strip().lower() == "neutral":
                continue

            recent_close_ts = symbol_last_close_ts.get(instrument)
            if recent_close_ts is not None:
                elapsed_symbol_hours = (ts - recent_close_ts).total_seconds() / 3600.0
                if elapsed_symbol_hours < SYMBOL_REENTRY_COOLDOWN_HOURS:
                    continue

            side_preview = determine_side(sig["delta_pp"], sig.get("outcome_sentiment", ""))
            cooldown_key = (sig["market_id"], side_preview)
            last_ts = market_side_last_trade_ts.get(cooldown_key)
            if last_ts is not None:
                elapsed_hours = (ts - last_ts).total_seconds() / 3600.0
                if elapsed_hours < MARKET_SIGNAL_COOLDOWN_HOURS:
                    continue

            selected = sig
            break

        if selected is None:
            continue

        # ── 4. TRADE SIZING ───────────────────────────────────────────────
        side = determine_side(selected["delta_pp"], selected.get("outcome_sentiment", ""))
        market_side_last_trade_ts[(selected["market_id"], side)] = ts

        entry = prices[selected["instrument"]]
        sl_frac = VOL_OVERRIDES.get(selected["instrument"], 0.05)
        sl_dist = entry * sl_frac
        if sl_dist <= 0:
            continue

        risk_dollar = equity * RISK_PCT
        qty = risk_dollar / sl_dist
        tp_dist = sl_dist * RR_MIN

        if side == "BUY":
            sl = round(entry - sl_dist, 4)
            tp = round(entry + tp_dist, 4)
        else:
            sl = round(entry + sl_dist, 4)
            tp = round(entry - tp_dist, 4)

        pos = {
            "trade_id": str(uuid.uuid4())[:8],
            "market_id": selected["market_id"],
            "symbol": selected["instrument"],
            "side": side,
            "quantity": round(qty, 6),
            "entry_price": round(entry, 4),
            "mark_price": round(entry, 4),
            "stop_loss": sl,
            "take_profit": tp,
            "status": "OPEN",
            "open_date": str(ts),
            "close_date": "",
            "close_reason": "",
            "realized_pnl": 0.0,
            "market_question": selected["question"][:80],
            "theme": selected["theme"],
            "direction_logic": selected.get("direction_logic", ""),
            "outcome_sentiment": selected.get("outcome_sentiment", ""),
            "delta_pp": round(selected["delta_pp"], 2),
            "edge_score": selected["edge_score"],
            "hours_since_move": round(selected["hours_since_move"], 2),
            "bars_held": 0,
        }
        open_positions.append(pos)
        signals_traded += 1
        anchor_str = selected["anchor_ts"].isoformat() if selected["anchor_ts"] is not None else "n/a"
        log.info(
            f"  OPEN  {side:4s} {selected['instrument']:5s} @ ${entry:.2f} | "
            f"SL=${sl:.2f} TP=${tp:.2f} | edge={selected['edge_score']:.2f} "
            f"age={selected['hours_since_move']:.2f}h anchor={anchor_str} | "
            f"signal: {selected['question'][:40]}..."
        )

    # ── Force-close any remaining open positions at last known price ───────
    for pos in open_positions:
        last_ts = stock_timestamps[-1]
        last_prices = stock_by_ts.get(last_ts, {})
        mark = last_prices.get(pos["symbol"], pos["entry_price"])
        side_mult = 1.0 if pos["side"] == "BUY" else -1.0
        pnl = (mark - pos["entry_price"]) * pos["quantity"] * side_mult
        equity += pnl
        pos["status"] = "CLOSED"
        pos["close_date"] = str(last_ts)
        pos["close_reason"] = "BACKTEST_END"
        pos["realized_pnl"] = round(pnl, 4)
        closed_trades.append(pos)
        symbol_last_close_ts[pos["symbol"]] = last_ts

    # ── Results ───────────────────────────────────────────────────────────────
    print_results(closed_trades, equity, signals_generated, signals_traded)

    if VOL_GATE_ENABLED:
        cash_days = sorted(
            d for d, s in vol_gate_daily_stats.items()
            if s["pre_gate"] > 0 and s["post_gate"] == 0 and s["suppressed"] > 0
        )
        print("\n  Vol Gate Diagnostics")
        print(f"  Enabled:              True")
        print(f"  Multiplier:           {VOL_GATE_MULTIPLIER:.2f}x")
        print(f"  Signals Suppressed:   {vol_gate_suppressed}")
        print(f"  Cash Days:            {len(cash_days)}")

        if cash_days:
            print("  Cash Day Dates:")
            for d in cash_days:
                print(f"    {d.isoformat()}")

        if vol_gate_suppressed_by_symbol:
            print("\n  Suppressed By Symbol:")
            for symbol, count in sorted(vol_gate_suppressed_by_symbol.items(), key=lambda kv: kv[1], reverse=True):
                print(f"    {symbol:<8s} {count:>4d}")

        try:
            diag_rows = []
            for d, s in sorted(vol_gate_daily_stats.items()):
                diag_rows.append({
                    "date": d.isoformat(),
                    "pre_gate_candidates": s["pre_gate"],
                    "suppressed_by_vol_gate": s["suppressed"],
                    "post_gate_candidates": s["post_gate"],
                    "all_suppressed": int(s["pre_gate"] > 0 and s["post_gate"] == 0 and s["suppressed"] > 0),
                })
            pd.DataFrame(diag_rows).to_csv(VOL_GATE_DIAGNOSTICS_CSV, index=False)
            print(f"  Diagnostics CSV:      {VOL_GATE_DIAGNOSTICS_CSV}")
        except Exception as e:
            print(f"  Diagnostics CSV write failed: {e}")

    if closed_trades:
        df_out = pd.DataFrame(closed_trades)
        df_out.to_csv(OUTPUT_CSV, index=False)
        log.info(f"Trades saved to {OUTPUT_CSV}")


def print_results(trades: List[Dict], final_equity: float, total_signals: int, traded_signals: int):
    print(f"\n{'='*70}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*70}")
    print(f"  Starting Equity:      ${ACCOUNT_EQUITY_START:,.2f}")
    print(f"  Final Equity:         ${final_equity:,.2f}")
    pnl = final_equity - ACCOUNT_EQUITY_START
    pct = (pnl / ACCOUNT_EQUITY_START) * 100
    print(f"  Total P&L:            ${pnl:+,.2f}  ({pct:+.1f}%)")
    print(f"  Total Signals:        {total_signals}")
    print(f"  Signals Traded:       {traded_signals}")
    print(f"  Total Trades Closed:  {len(trades)}")

    if not trades:
        print("  No trades were generated.")
        return

    df = pd.DataFrame(trades)
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
    cumulative = df["realized_pnl"].cumsum() + ACCOUNT_EQUITY_START
    peak = cumulative.expanding().max()
    drawdowns = (cumulative - peak)
    max_dd = drawdowns.min()
    print(f"  Max Drawdown:         ${max_dd:.2f}")

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

    print(f"{'='*70}")


if __name__ == "__main__":
    run_backtest()
