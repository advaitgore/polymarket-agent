"""
Adaptive Instrument Mapper.

Reads trades.csv, groups realized P&L by (theme, ticker), and updates
the weight table in correlations_weights.json. Weights guide which
ticker within a theme is selected as primary by detect_signals.py.

Rules:
  - Only CLOSED trades contribute to weight adjustment.
  - Trades with realized_pnl > 0 (winners) increase the ticker's weight.
  - Trades with realized_pnl < 0 (losers) decrease the ticker's weight.
  - Weights are clamped to [MIN_WEIGHT, MAX_WEIGHT].
  - The entire weight table is re-normalized per theme after each update
    so relative weights, not absolute, drive selection.
  - A performance log entry is appended for audit/debugging.
  - Should be called once per day during market hours (wired in main.py).

No external API calls. Reads only from the sandbox filesystem.
"""
import csv
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
TRADES_CSV   = os.path.join(BASE_DIR, "data", "trades.csv")
WEIGHTS_JSON = os.path.join(BASE_DIR, "correlations_weights.json")

MIN_WEIGHT    = 0.1
MAX_WEIGHT    = 5.0
LEARNING_RATE = 0.15   # fraction of current weight adjusted per win/loss

# ── Load / save weights ───────────────────────────────────────────────────────

def _load_weights_file() -> Dict:
    """Load full weights JSON. Returns default structure if missing."""
    if not os.path.exists(WEIGHTS_JSON):
        return {
            "_meta": {"version": "1.0", "last_updated": None,
                      "cycles_since_last_adapt": 0, "adapt_every_n_cycles": 48,
                      "min_weight": MIN_WEIGHT, "max_weight": MAX_WEIGHT,
                      "learning_rate": LEARNING_RATE},
            "weights": {},
            "performance_log": []
        }
    with open(WEIGHTS_JSON) as f:
        return json.load(f)

def _save_weights_file(data: Dict):
    with open(WEIGHTS_JSON, "w") as f:
        json.dump(data, f, indent=2)

# ── P&L aggregation ───────────────────────────────────────────────────────────

def _aggregate_pnl_by_theme_ticker() -> Dict[Tuple[str, str], float]:
    """
    Read trades.csv, sum realized_pnl for CLOSED trades grouped by (theme, symbol).
    Returns dict: {(theme, ticker): total_realized_pnl}
    """
    if not os.path.exists(TRADES_CSV):
        logger.info("adaptive_mapper: trades.csv not found — nothing to adapt.")
        return {}

    pnl_map: Dict[Tuple[str, str], float] = defaultdict(float)

    try:
        with open(TRADES_CSV, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("status", "").upper() != "CLOSED":
                    continue
                theme  = row.get("theme", "").strip()
                symbol = row.get("symbol", "").strip()
                try:
                    rpnl = float(row.get("realized_pnl", 0) or 0)
                except (ValueError, TypeError):
                    rpnl = 0.0
                if theme and symbol:
                    pnl_map[(theme, symbol)] += rpnl
    except Exception as e:
        logger.error(f"adaptive_mapper: error reading trades.csv: {e}")
        return {}

    return dict(pnl_map)

# ── Weight update logic ───────────────────────────────────────────────────────

def _adjust_weights(
    weights_section: Dict[str, Dict[str, float]],
    pnl_map: Dict[Tuple[str, str], float]
) -> Tuple[Dict[str, Dict[str, float]], int]:
    """
    Apply P&L-based adjustments to weights.
    Returns (updated_weights, number_of_adjustments).
    """
    adjustments = 0

    for (theme, ticker), total_pnl in pnl_map.items():
        if theme not in weights_section:
            # Theme not yet in weights — initialize all tickers at 1.0
            logger.info(f"adaptive_mapper: new theme '{theme}' — initializing weights")
            weights_section[theme] = {ticker: 1.0}
        if ticker not in weights_section[theme]:
            # New ticker seen for existing theme
            weights_section[theme][ticker] = 1.0

        current_w = weights_section[theme][ticker]

        if total_pnl > 0:
            # Winner: increase weight
            new_w = min(current_w * (1.0 + LEARNING_RATE), MAX_WEIGHT)
            direction = "↑"
        elif total_pnl < 0:
            # Loser: decrease weight
            new_w = max(current_w * (1.0 - LEARNING_RATE), MIN_WEIGHT)
            direction = "↓"
        else:
            # Breakeven: no change
            continue

        weights_section[theme][ticker] = round(new_w, 4)
        adjustments += 1
        logger.info(
            f"  {theme}/{ticker}: weight {current_w:.4f} → {new_w:.4f} "
            f"{direction} (P&L={total_pnl:+.2f})"
        )

    return weights_section, adjustments

def _normalize_weights(weights_section: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """
    Normalize weights per theme so the max in each theme = 1.0 (relative scale).
    This prevents weights from drifting monotonically upward.
    """
    normalized = {}
    for theme, ticker_map in weights_section.items():
        if not ticker_map:
            normalized[theme] = ticker_map
            continue
        max_w = max(ticker_map.values())
        if max_w <= 0:
            normalized[theme] = {t: 1.0 for t in ticker_map}
        else:
            normalized[theme] = {
                t: round(w / max_w, 4) for t, w in ticker_map.items()
            }
    return normalized

# ── Public entry point ─────────────────────────────────────────────────────────

def run_adaptive_mapping(force: bool = False) -> bool:
    """
    Run one adaptive weight update cycle.

    Steps:
      1. Load correlations_weights.json.
      2. Increment cycles_since_last_adapt counter.
      3. If not yet due (and not forced), return False.
      4. Aggregate realized P&L from trades.csv by (theme, ticker).
      5. Adjust weights based on P&L sign and magnitude.
      6. Normalize weights per theme.
      7. Write updated weights back to correlations_weights.json.
      8. Log a performance entry.

    Returns True if weights were updated, False if skipped.
    """
    data = _load_weights_file()
    meta = data.setdefault("_meta", {})
    cycles = meta.get("cycles_since_last_adapt", 0) + 1
    adapt_every = meta.get("adapt_every_n_cycles", 48)
    meta["cycles_since_last_adapt"] = cycles

    if not force and cycles < adapt_every:
        logger.debug(
            f"adaptive_mapper: cycle {cycles}/{adapt_every} — not yet due, skipping"
        )
        _save_weights_file(data)
        return False

    logger.info("=== Adaptive mapper: running weight update ===")
    meta["cycles_since_last_adapt"] = 0

    pnl_map = _aggregate_pnl_by_theme_ticker()

    if not pnl_map:
        logger.info("adaptive_mapper: no closed trades found — weights unchanged.")
        meta["last_updated"] = datetime.now(timezone.utc).isoformat()
        _save_weights_file(data)
        return False

    # Log what we found
    logger.info(f"adaptive_mapper: {len(pnl_map)} (theme, ticker) P&L entries found:")
    for (theme, ticker), pnl in sorted(pnl_map.items()):
        logger.info(f"  {theme}/{ticker}: P&L={pnl:+.2f}")

    weights_section, n_adjustments = _adjust_weights(data.get("weights", {}), pnl_map)
    weights_section = _normalize_weights(weights_section)
    data["weights"] = weights_section

    # Performance log entry (keep last 100)
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_adjustments": n_adjustments,
        "pnl_by_theme_ticker": {
            f"{theme}/{ticker}": round(pnl, 4)
            for (theme, ticker), pnl in pnl_map.items()
        },
        "top_tickers_after": {
            theme: max(ticker_map.items(), key=lambda x: x[1])[0]
            for theme, ticker_map in weights_section.items()
            if ticker_map
        }
    }
    perf_log = data.get("performance_log", [])
    perf_log.append(log_entry)
    data["performance_log"] = perf_log[-100:]  # keep last 100

    meta["last_updated"] = log_entry["timestamp"]
    _save_weights_file(data)

    # Invalidate signal detection cache so new weights are used immediately
    try:
        from detect_signals import invalidate_correlation_cache
        invalidate_correlation_cache()
    except Exception:
        pass

    logger.info(
        f"=== Adaptive mapper done: {n_adjustments} weight adjustments. "
        f"Top tickers: {log_entry['top_tickers_after']} ==="
    )
    return True

# ── Diagnostic helpers ────────────────────────────────────────────────────────

def get_current_primary_tickers() -> Dict[str, str]:
    """
    Return the current highest-weight ticker per theme (for logging/display).
    """
    data = _load_weights_file()
    result = {}
    for theme, ticker_map in data.get("weights", {}).items():
        if ticker_map:
            result[theme] = max(ticker_map.items(), key=lambda x: x[1])[0]
    return result

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    updated = run_adaptive_mapping(force=True)
    print(f"Weight update ran: {updated}")
    print("Current primary tickers by theme:")
    for theme, ticker in get_current_primary_tickers().items():
        print(f"  {theme:30s} → {ticker}")
