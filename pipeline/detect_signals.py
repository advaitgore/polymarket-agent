"""
Signal detection engine — v3.0 with strict economic relevance filter.

For each active market outcome, computes 24h probability change.
Flags moves > PRICE_MOVE_THRESHOLD pp as signals.

Each signal is enriched with:
  - theme      : one of the 6 allowed themes, or "none"
  - trade_eligible : True only if the market has a clear economic link
                     to the trading universe AND is not sports/entertainment/
                     novelty/celebrity noise
  - correlated_instrument : ticker or "NONE"

Only signals with trade_eligible=True are passed to trade_executor.
Non-tradable signals are still logged to signals.csv for audit purposes.
"""
import re
import sqlite3
import json
import logging
import csv
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

from config import (
    DB_PATH, SIGNALS_CSV, CORRELATIONS_JSON,
    PRICE_MOVE_THRESHOLD, MIN_HISTORY_HOURS_FOR_SIGNAL
)

logger = logging.getLogger(__name__)

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_JSON = os.path.join(BASE_DIR, "correlations_weights.json")

ALLOWED_THEMES = {
    "energy_geopolitics",
    "defense_geopolitics",
    "us_politics_macro",
    "global_macro",
    "tech_ai",
    "crypto_major",
}

# ── Load correlations ─────────────────────────────────────────────────────────

def load_correlations() -> Tuple[List[Dict], Dict]:
    """Load themes list and non_tradable_patterns from correlations.json."""
    with open(CORRELATIONS_JSON) as f:
        data = json.load(f)
    themes = data.get("themes", data.get("markets", []))
    non_tradable = data.get("non_tradable_patterns", {})
    return themes, non_tradable

def load_weights() -> Dict[str, Dict[str, float]]:
    if not os.path.exists(WEIGHTS_JSON):
        return {}
    try:
        with open(WEIGHTS_JSON) as f:
            return json.load(f).get("weights", {})
    except Exception as e:
        logger.warning(f"Could not load weights: {e}")
        return {}

_THEMES: Optional[List[Dict]] = None
_NON_TRADABLE: Optional[Dict] = None

def get_themes_and_patterns() -> Tuple[List[Dict], Dict]:
    global _THEMES, _NON_TRADABLE
    if _THEMES is None:
        _THEMES, _NON_TRADABLE = load_correlations()
    return _THEMES, _NON_TRADABLE

def invalidate_correlation_cache():
    global _THEMES, _NON_TRADABLE
    _THEMES = None
    _NON_TRADABLE = None

# ── Non-tradable pattern check (runs FIRST) ───────────────────────────────────

def is_non_tradable(question: str, non_tradable_patterns: Dict) -> Tuple[bool, str]:
    """
    Check question against all non-tradable pattern categories.
    Returns (is_blocked, reason_category).
    Runs before theme scoring — these signals are always trade_eligible=False.
    """
    q = question.lower()

    for category, patterns in non_tradable_patterns.items():
        if category == "description":
            continue
        for pattern in patterns:
            # Use substring match for simple strings, regex for patterns with wildcards
            try:
                if re.search(pattern.lower(), q):
                    return True, category
            except re.error:
                if pattern.lower() in q:
                    return True, category

    return False, ""

# ── Theme scoring ─────────────────────────────────────────────────────────────

def score_themes(question: str, themes: List[Dict]) -> List[Tuple[str, int, Dict]]:
    """
    Score every theme by keyword overlap.
    Returns [(theme_id, score, theme_entry)] sorted descending.
    """
    q = question.lower()
    scored = []
    for entry in themes:
        theme_id = entry.get("theme", "none")
        keywords = entry.get("keywords", [])
        count = sum(1 for kw in keywords if kw.lower() in q)
        if count > 0:
            scored.append((theme_id, count, entry))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored

# ── Instrument picker ─────────────────────────────────────────────────────────

def pick_ticker(theme_id: str, theme_entry: Dict) -> str:
    """Select the highest-weight ticker for this theme from weights file."""
    weights = load_weights()
    theme_weights = weights.get(theme_id, {})
    tickers = theme_entry.get("tickers", [])

    if not tickers:
        return "NONE"
    if not theme_weights:
        return theme_entry.get("primary_ticker", tickers[0])

    candidates = [(t, theme_weights.get(t, 1.0)) for t in tickers]
    return max(candidates, key=lambda x: x[1])[0]

# ── Company-specific instrument override ─────────────────────────────────────

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

def company_specific_ticker(question: str) -> Optional[str]:
    """Return a single-name ticker if the question clearly names a specific company."""
    q = question.lower()
    for keyword, ticker in COMPANY_OVERRIDES.items():
        if keyword in q:
            return ticker
    return None

# ── Main classification ────────────────────────────────────────────────────────

def classify_signal(market_question: str) -> Tuple[str, bool, str]:
    """
    Full classification pipeline for a market question.
    Returns (theme, trade_eligible, instrument).

    Steps:
      1. Non-tradable pattern check → if blocked, return (none, False, NONE)
      2. Theme keyword scoring → pick best theme
      3. If theme not in ALLOWED_THEMES → (none, False, NONE)
      4. Company-specific override for single-name instruments
      5. Dynamic weight-based ticker selection
      6. Return (theme, True, instrument)
    """
    themes, non_tradable = get_themes_and_patterns()

    # Step 1 — hard block on non-tradable patterns
    blocked, reason = is_non_tradable(market_question, non_tradable)
    if blocked:
        logger.debug(f"NON-TRADABLE [{reason}]: {market_question[:80]}")
        return "none", False, "NONE"

    # Step 2 — score themes
    scored = score_themes(market_question, themes)

    if not scored:
        return "none", False, "NONE"

    theme_id, score, theme_entry = scored[0]

    # Step 3 — must be an allowed theme
    if theme_id not in ALLOWED_THEMES:
        return "none", False, "NONE"

    # Additional eligibility check: celebrity/meme nominations in us_politics_macro
    if theme_id == "us_politics_macro":
        non_tradable_sigs = theme_entry.get("non_tradable_signals", [])
        q_lower = market_question.lower()
        # Check specifically for celebrity nominations flagged in non_tradable_patterns
        _, reason2 = is_non_tradable(market_question, {
            "celebrity_nominations": non_tradable.get("celebrity_nominations", []),
            "joke_novelty": non_tradable.get("joke_novelty", []),
        })
        if reason2:
            return "none", False, "NONE"

    # Step 4 — company-specific ticker override (for tech_ai, defense, energy single-names)
    override = company_specific_ticker(market_question)
    if override:
        instrument = override
    else:
        # Step 5 — weight-based ticker selection
        instrument = pick_ticker(theme_id, theme_entry)

    if not instrument or instrument == "":
        instrument = "NONE"

    trade_eligible = instrument != "NONE"

    logger.debug(
        f"CLASSIFY: theme={theme_id} score={score} instrument={instrument} "
        f"eligible={trade_eligible} | {market_question[:60]}"
    )

    return theme_id, trade_eligible, instrument


# Legacy wrapper — kept for backward compat
def find_instrument(market_question: str) -> Tuple[str, str, str]:
    """
    Returns (instrument, direction_logic, theme).
    Wraps classify_signal for compatibility with older call sites.
    """
    theme, trade_eligible, instrument = classify_signal(market_question)
    direction = "higher_prob_positive_outcome_means_long"
    themes, _ = get_themes_and_patterns()
    for entry in themes:
        if entry.get("theme") == theme:
            direction = entry.get("direction_logic", direction)
            break
    return instrument, direction, theme


def _parse_db_timestamp(raw_ts: str) -> Optional[datetime]:
    """Parse SQLite timestamp strings into UTC-aware datetimes."""
    try:
        ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def estimate_hours_since_move(
    conn: sqlite3.Connection,
    market_id: str,
    outcome_name: str,
    old_price: float,
    now_utc: datetime,
) -> float:
    """
    Estimate signal recency by finding the first timestamp in the last 24h
    where the absolute move from the anchor price crosses the threshold.
    """
    rows = conn.execute(
        """
        SELECT price, timestamp FROM price_history
        WHERE market_id=? AND outcome_name=? AND timestamp >= ?
        ORDER BY timestamp ASC
        """,
        (market_id, outcome_name, (now_utc - timedelta(hours=24)).isoformat()),
    ).fetchall()

    if not rows:
        return 24.0

    first_cross_ts: Optional[datetime] = None
    for price, ts_raw in rows:
        ts = _parse_db_timestamp(ts_raw)
        if ts is None:
            continue
        if abs((float(price) - old_price) * 100.0) >= PRICE_MOVE_THRESHOLD:
            first_cross_ts = ts
            break

    if first_cross_ts is None:
        # Fallback to the freshest sample age if no threshold cross could be resolved.
        latest_ts = None
        for _, ts_raw in reversed(rows):
            latest_ts = _parse_db_timestamp(ts_raw)
            if latest_ts is not None:
                break
        if latest_ts is None:
            return 24.0
        return max(0.0, (now_utc - latest_ts).total_seconds() / 3600.0)

    return max(0.0, (now_utc - first_cross_ts).total_seconds() / 3600.0)


# ── Compute 24h delta ─────────────────────────────────────────────────────────

def get_24h_delta(
    conn: sqlite3.Connection,
    market_id: str,
    outcome_name: str,
) -> Tuple[Optional[float], Optional[float], Optional[datetime]]:
    now_utc = datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(hours=24)).isoformat()

    row = conn.execute(
        "SELECT current_price FROM outcomes WHERE market_id=? AND outcome_name=?",
        (market_id, outcome_name)
    ).fetchone()
    if not row:
        return None, None, None
    current_price = row[0]

    old_row = conn.execute("""
        SELECT price, timestamp FROM price_history
        WHERE market_id=? AND outcome_name=? AND timestamp < ?
        ORDER BY timestamp DESC LIMIT 1
    """, (market_id, outcome_name, cutoff)).fetchone()

    if old_row:
        old_ts = _parse_db_timestamp(old_row[1])
        if old_ts is None:
            return None, None, None
        return old_row[0], current_price, old_ts

    # Cold-start path: no 24h-old data exists yet. We allow a fallback anchor
    # only when the overall history age is old enough to be meaningful.
    snap = conn.execute("""
        SELECT price, timestamp FROM price_history
        WHERE market_id=? AND outcome_name=?
        ORDER BY timestamp ASC LIMIT 1
    """, (market_id, outcome_name)).fetchone()
    if not snap:
        return None, None, None

    snap_ts = _parse_db_timestamp(snap[1])
    if snap_ts is None:
        return None, None, None

    history_age_hours = (now_utc - snap_ts).total_seconds() / 3600.0
    if history_age_hours < MIN_HISTORY_HOURS_FOR_SIGNAL:
        logger.debug(
            "SKIP [insufficient_history]: market=%s outcome=%s age=%.2fh (< %.2fh)",
            market_id,
            outcome_name,
            history_age_hours,
            MIN_HISTORY_HOURS_FOR_SIGNAL,
        )
        return None, None, None

    return snap[0], current_price, snap_ts


# ── Edge score ────────────────────────────────────────────────────────────────

def compute_edge_score(
    prob_change_pp: float,
    hours_since_move: float = 12.0,
    explained: bool = False,
) -> float:
    base = abs(prob_change_pp) / 5.0
    recency = 1.5 if hours_since_move <= 6 else (1.2 if hours_since_move <= 12 else 1.0)
    news_mult = 0.3 if explained else 1.5
    return round(base * recency * news_mult, 3)


def run_signal_detection() -> List[Dict]:
    """
    Scan all active markets for 24h probability moves above threshold.
    Each signal includes theme, trade_eligible, and correlated_instrument.
    Non-tradable signals are included in the output for logging but will
    never reach trade_executor (filtered by select_best_signal).
    """
    logger.info("=== Running signal detection (v3 — economic relevance filter) ===")
    conn = sqlite3.connect(DB_PATH)

    now_utc = datetime.now(timezone.utc)

    markets = conn.execute(
        "SELECT id, question FROM markets WHERE active=1"
    ).fetchall()

    signals      = []
    n_eligible   = 0
    n_blocked    = 0

    for market_id, question in markets:
        outcomes = conn.execute(
            "SELECT outcome_name FROM outcomes WHERE market_id=?", (market_id,)
        ).fetchall()

        for (outcome_name,) in outcomes:
            old_price, new_price, anchor_ts = get_24h_delta(conn, market_id, outcome_name)
            if old_price is None or new_price is None:
                continue

            change_pp = (new_price - old_price) * 100.0
            if abs(change_pp) < PRICE_MOVE_THRESHOLD:
                continue

            hours_since_move = estimate_hours_since_move(
                conn,
                market_id,
                outcome_name,
                old_price,
                now_utc,
            )

            theme, trade_eligible, instrument = classify_signal(question)
            edge_score = compute_edge_score(
                change_pp,
                hours_since_move=hours_since_move,
                explained=False,
            )

            signal = {
                "market_id":            market_id,
                "market_name":          question,
                "outcome":              outcome_name,
                "old_prob":             round(old_price * 100, 2),
                "new_prob":             round(new_price * 100, 2),
                "change_pp":            round(change_pp, 2),
                "correlated_instrument": instrument,
                "theme":                theme,
                "trade_eligible":       trade_eligible,
                "edge_score":           edge_score,
                "explained":            False,
                "news_summary":         "",
                "news_check_method":    "unverified",
                "hours_since_move":     round(hours_since_move, 2),
            }
            signals.append(signal)

            if trade_eligible:
                n_eligible += 1
                anchor_str = anchor_ts.isoformat() if anchor_ts else "n/a"
                logger.info(
                    f"SIGNAL [TRADABLE]: {question[:60]} [{outcome_name}] "
                    f"{old_price*100:.1f}%→{new_price*100:.1f}% ({change_pp:+.1f}pp) "
                    f"→ {instrument} [{theme}] | age={hours_since_move:.2f}h anchor={anchor_str}"
                )
            else:
                n_blocked += 1
                logger.debug(
                    f"SIGNAL [NON-TRADABLE]: {question[:60]} [{outcome_name}] "
                    f"({change_pp:+.1f}pp) [theme={theme}] age={hours_since_move:.2f}h"
                )

    conn.close()
    logger.info(
        f"Signal detection complete. {len(signals)} total | "
        f"{n_eligible} tradable | {n_blocked} non-tradable (logged only)"
    )
    return signals


def append_signals_to_csv(signals: List[Dict]):
    """Append signals to signals.csv. Includes trade_eligible column."""
    if not signals:
        return
    now = datetime.now(timezone.utc).isoformat()
    with open(SIGNALS_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        for s in signals:
            writer.writerow([
                now,
                s["market_id"],
                s["market_name"],
                s["outcome"],
                s["old_prob"],
                s["new_prob"],
                s["change_pp"],
                s["correlated_instrument"],
                s["edge_score"],
                int(s["explained"]),
                s["news_summary"],
                s.get("news_check_method", "unverified"),
                s.get("theme", "none"),
                int(s.get("trade_eligible", False)),
            ])
    logger.info(f"Appended {len(signals)} signals to {SIGNALS_CSV}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sigs = run_signal_detection()
    tradable = [s for s in sigs if s["trade_eligible"]]
    non_tradable = [s for s in sigs if not s["trade_eligible"]]
    print(f"\nTotal signals:    {len(sigs)}")
    print(f"Tradable:         {len(tradable)}")
    print(f"Non-tradable:     {len(non_tradable)}")
    print("\nTop 5 tradable signals:")
    for s in sorted(tradable, key=lambda x: x["edge_score"], reverse=True)[:5]:
        print(f"  [{s['theme']:22s}] {s['market_name'][:55]:55s} → {s['correlated_instrument']:5s} edge={s['edge_score']:.2f}")
    print("\nSample non-tradable (first 5):")
    for s in non_tradable[:5]:
        print(f"  [none] {s['market_name'][:70]}")
