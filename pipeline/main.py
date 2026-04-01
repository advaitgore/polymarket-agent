"""
Single-shot pipeline runner for the Polymarket Trading System.

This script runs ONE cycle and exits.  It is driven externally by
schedule_cron (the Perplexity Computer scheduler) which fires it every
15 minutes during US market hours (Mon–Fri 07:30–18:00 ET) and once per
hour off-hours.  There is no internal sleep loop — the sandbox scheduler
is the clock.

No external API keys or secrets are needed.  Everything runs in the
Perplexity Computer sandbox and writes to the sandbox filesystem only:
  data/markets.db   — SQLite snapshot of ~500 active markets + time series
  data/signals.csv  — signal feed with edge scores and explained/unexplained flags
  data/trades.csv   — simulated paper trades (no broker connectivity)

IMPORTANT: There is NO connection to IBKR, TWS, IB Gateway, or any broker.
All trade execution is local simulation using prices from Yahoo Finance.
"""
import sys
import os
import time
import logging
import traceback
from datetime import datetime, timedelta
import zoneinfo
from typing import Optional

from config import LOG_FILE, LOG_DIR
from db_init import init_db, init_csv_files
from fetch_markets import run_fetch_cycle
from detect_signals import run_signal_detection, append_signals_to_csv
from trade_executor import select_best_signal, place_paper_trade, update_mark_prices
from adaptive_mapper import run_adaptive_mapping

# ── Logging setup ──────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("main")

# ── Eastern timezone ───────────────────────────────────────────────────────────
ET = zoneinfo.ZoneInfo("America/New_York")

MARKET_OPEN_H,  MARKET_OPEN_M  = 7, 30   # 07:30 ET
MARKET_CLOSE_H, MARKET_CLOSE_M = 18, 0   # 18:00 ET


def now_et() -> datetime:
    return datetime.now(ET)


def is_weekday(dt: datetime = None) -> bool:
    if dt is None:
        dt = now_et()
    return dt.weekday() < 5


def is_market_hours(dt: datetime = None) -> bool:
    if dt is None:
        dt = now_et()
    if not is_weekday(dt):
        return False
    open_mins  = MARKET_OPEN_H  * 60 + MARKET_OPEN_M
    close_mins = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M
    return open_mins <= (dt.hour * 60 + dt.minute) < close_mins


def get_cycle_num() -> int:
    """Read a persistent cycle counter from disk, increment, and save."""
    counter_file = os.path.join(os.path.dirname(__file__), "data", ".cycle_counter")
    try:
        with open(counter_file) as f:
            n = int(f.read().strip())
    except Exception:
        n = 0
    n += 1
    try:
        os.makedirs(os.path.dirname(counter_file), exist_ok=True)
        with open(counter_file, "w") as f:
            f.write(str(n))
    except Exception:
        pass
    return n


def should_run_adapt() -> bool:
    """Return True if adaptive mapper hasn't run today yet."""
    adapt_file = os.path.join(os.path.dirname(__file__), "data", ".last_adapt_date")
    today = now_et().strftime("%Y-%m-%d")
    try:
        with open(adapt_file) as f:
            last = f.read().strip()
        if last == today:
            return False
    except Exception:
        pass
    # Write today's date
    try:
        os.makedirs(os.path.dirname(adapt_file), exist_ok=True)
        with open(adapt_file, "w") as f:
            f.write(today)
    except Exception:
        pass
    return True


def run_cycle(cycle_num: int, run_adapt: bool = False) -> int:
    et = now_et()
    in_market = is_market_hours(et)
    logger.info(f"\n{'='*60}")
    logger.info(f"CYCLE {cycle_num} — {et.strftime('%Y-%m-%d %H:%M:%S ET')} "
                f"({'MARKET HOURS' if in_market else 'OFF HOURS'})")
    logger.info(f"{'='*60}")

    # ── 1. Fetch Polymarket data ──────────────────────────────────────────
    stored = []
    try:
        logger.info("[1/6] Fetching Polymarket data (public API, no auth)...")
        stored = run_fetch_cycle()
        logger.info(f"[1/6] Stored {len(stored)} outcome snapshots")
    except Exception as e:
        logger.error(f"[1/6] Market fetch failed: {e}\n{traceback.format_exc()}")

    if not in_market:
        logger.info("[1/6] Off-hours fetch only — skipping signals/trades.")
        return len(stored)

    # ── 2. Detect signals ─────────────────────────────────────────────────
    signals = []
    try:
        logger.info("[2/6] Running signal detection (>5pp 24h move)...")
        signals = run_signal_detection()
        logger.info(f"[2/6] Found {len(signals)} signals above threshold")
    except Exception as e:
        logger.error(f"[2/6] Signal detection failed: {e}\n{traceback.format_exc()}")

    # ── 3. News check (bypassed for backtest parity) ──────────────────────
    if signals:
        logger.info("[3/6] Qualitative news check bypassed to enforce pure 15m mathematical backtest parity.")
    else:
        logger.info("[3/6] No signals to check")

    # ── 4. Write signals to CSV ───────────────────────────────────────────
    try:
        logger.info("[4/6] Appending signals to signals.csv...")
        append_signals_to_csv(signals)
    except Exception as e:
        logger.error(f"[4/6] CSV write failed: {e}")

    # ── 5. Place best trade ───────────────────────────────────────────────
    try:
        logger.info("[5/6] Evaluating trade opportunity...")
        best = select_best_signal(signals)
        if best:
            logger.info(
                f"[5/6] Best mathematical edge: {best['market_name'][:60]} "
                f"({best['correlated_instrument']}, edge={best['edge_score']:.2f})"
            )
            trade = place_paper_trade(best)
            if trade:
                logger.info(
                    f"[5/6] Trade placed: {trade['side']} {trade['quantity']} "
                    f"{trade['symbol']} @ {trade['entry_price']:.2f}"
                )
            else:
                logger.info("[5/6] No simulated trade placed (no new qualifying signal)")
        else:
            logger.info("[5/6] No new trade opportunity this cycle")
    except Exception as e:
        logger.error(f"[5/6] Trade execution failed: {e}\n{traceback.format_exc()}")

    # ── 6. Update mark prices ─────────────────────────────────────────────
    try:
        logger.info("[6/6] Updating mark prices for open positions...")
        update_mark_prices()
    except Exception as e:
        logger.error(f"[6/6] Mark price update failed: {e}\n{traceback.format_exc()}")

    # ── 7. Adaptive mapper (once per trading day) ─────────────────────────
    if run_adapt:
        try:
            logger.info("[7/7] Running adaptive instrument mapper (daily weight update)...")
            updated = run_adaptive_mapping(force=True)
            logger.info(f"[7/7] Adaptive mapper: {'weights updated' if updated else 'no closed trades yet — weights unchanged'}.")
        except Exception as e:
            logger.error(f"[7/7] Adaptive mapper failed: {e}\n{traceback.format_exc()}")

    logger.info(f"Cycle {cycle_num} complete.\n")
    return len(stored)


def main():
    start = time.time()
    et = now_et()

    logger.info("┌──────────────────────────────────────────────────────────────┐")
    logger.info("│         Polymarket Trading System — Single-Shot Run           │")
    logger.info(f"│  {et.strftime('%Y-%m-%d %H:%M:%S ET'):<58}│")
    logger.info("│  Driven by schedule_cron — no internal sleep loop            │")
    logger.info("└──────────────────────────────────────────────────────────────┘")

    init_db()
    init_csv_files()

    cycle_num = get_cycle_num()
    run_adapt = should_run_adapt()

    try:
        run_cycle(cycle_num, run_adapt=run_adapt)
    except Exception as e:
        logger.error(f"Unhandled error in cycle {cycle_num}: {e}\n{traceback.format_exc()}")

    elapsed = time.time() - start
    logger.info(f"Cycle took {elapsed:.1f}s — exiting (scheduler will re-invoke in ~15min)")


if __name__ == "__main__":
    main()
