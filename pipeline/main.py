"""
Main orchestration loop for the Polymarket Trading System.

Scheduling rules (all times US Eastern):
  - Weekdays 07:30–18:00  → full cycle every 15 minutes
  - Weekdays before 07:30  → sleep until 07:30 same day
  - Weekdays after  18:00  → sleep until 07:30 next weekday
  - Saturday & Sunday      → FULL SLEEP until Monday 07:30 ET

  The pipeline is completely idle outside Mon–Fri 07:30–18:00 ET.

No external API keys or secrets are needed.  Everything runs in the
Perplexity Computer sandbox and writes to the sandbox filesystem only:
  data/markets.db   — SQLite snapshot of ~500 active markets + time series
  data/signals.csv  — signal feed with edge scores and explained/unexplained flags
  data/trades.csv   — simulated paper trades (no broker connectivity)

IMPORTANT: There is NO connection to IBKR, TWS, IB Gateway, or any broker.
All trade execution is local simulation using prices from markets.db.
"""
import sys
import os
import time
import logging
import traceback
from datetime import datetime, timezone, timedelta
import zoneinfo
from typing import Optional

from config import POLL_INTERVAL_MINUTES, LOG_FILE, LOG_DIR
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

# Market-hours window (Eastern)
MARKET_OPEN_H,  MARKET_OPEN_M  = 7, 30   # 07:30 ET
MARKET_CLOSE_H, MARKET_CLOSE_M = 18, 0   # 18:00 ET
MARKET_HOURS_INTERVAL_S = 15 * 60         # 15 min cadence during market hours


def now_et() -> datetime:
    return datetime.now(ET)


def is_weekday(dt: datetime = None) -> bool:
    """True if dt is Mon–Fri (weekday() 0–4)."""
    if dt is None:
        dt = now_et()
    return dt.weekday() < 5  # 5=Saturday, 6=Sunday


def is_market_hours(dt: datetime = None) -> bool:
    """
    True only when ALL of the following hold:
      - dt is a weekday (Mon–Fri)
      - dt is between 07:30 ET and 18:00 ET
    Outside this window the pipeline does NOT run — either it sleeps until
    the next open (same day, later) or until Monday 07:30 (weekend).
    """
    if dt is None:
        dt = now_et()
    if not is_weekday(dt):
        return False
    open_mins  = MARKET_OPEN_H  * 60 + MARKET_OPEN_M
    close_mins = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M
    return open_mins <= (dt.hour * 60 + dt.minute) < close_mins


def next_market_open(from_dt: datetime = None) -> datetime:
    """
    Return the exact datetime of the next market open (07:30 ET, Mon–Fri)
    strictly after `from_dt`.  Skips Saturday and Sunday entirely.

    Examples
    --------
    Friday 19:00 ET  → Monday 07:30 ET   (skips weekend)
    Saturday any     → Monday 07:30 ET
    Sunday  any      → Monday 07:30 ET
    Monday  06:00 ET → Monday 07:30 ET   (same day, later)
    Monday  08:00 ET → Tuesday 07:30 ET  (already open today; next open = tomorrow)
    """
    if from_dt is None:
        from_dt = now_et()

    # Candidate: today's open
    candidate = from_dt.replace(
        hour=MARKET_OPEN_H, minute=MARKET_OPEN_M, second=0, microsecond=0
    )

    # If candidate is already in the past (or right now), advance one day
    if candidate <= from_dt:
        candidate += timedelta(days=1)

    # Skip Saturday (5) and Sunday (6)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)

    return candidate


def seconds_until_next_cycle(last_run_et: datetime) -> int:
    """
    Return how many seconds to sleep before the next pipeline run.

    Rules (all times Eastern):
      - Weekdays 07:30–18:00  → run every 15 min (MARKET_HOURS_INTERVAL_S)
      - Weekday before 07:30  → sleep until 07:30 today
      - Weekday after  18:00  → sleep until 07:30 next weekday
      - Saturday / Sunday     → sleep until 07:30 Monday  (full weekend sleep)

    The pipeline NEVER runs outside Mon–Fri 07:30–18:00 ET.
    Never returns a negative value (minimum 10 seconds).
    """
    now = now_et()

    if is_market_hours(now):
        # Inside the trading window — next run is 15 min after the last one
        elapsed = (now - last_run_et).total_seconds()
        wait = max(10, MARKET_HOURS_INTERVAL_S - elapsed)
        return int(wait)

    # Outside trading hours (includes ALL of Saturday & Sunday):
    # sleep straight to the next weekday market open.
    nxt = next_market_open(now)
    wait = max(10, (nxt - now).total_seconds())
    return int(wait)


# ── Cycle ──────────────────────────────────────────────────────────────────────

def run_cycle(cycle_num: int, last_snapshot_count: int, run_adapt: bool = False) -> int:
    """
    Run one full data pipeline cycle.
    Returns the number of outcome snapshots stored (for change detection).
    If run_adapt=True, calls the adaptive mapper weight update.
    """
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

    # Pipeline only runs during market hours (07:30–18:00 ET, Mon–Fri).
    # This guard is a belt-and-suspenders check; the scheduler should never
    # call run_cycle outside that window.
    if not in_market:
        logger.warning("[1/6] run_cycle called outside market hours — skipping (scheduler bug?)")
        return last_snapshot_count

    # ── 2. Detect signals ─────────────────────────────────────────────────
    signals = []
    try:
        logger.info("[2/6] Running signal detection (>5pp 24h move)...")
        signals = run_signal_detection()
        logger.info(f"[2/6] Found {len(signals)} signals above threshold")
    except Exception as e:
        logger.error(f"[2/6] Signal detection failed: {e}\n{traceback.format_exc()}")

    # ── 3. News check (Bypassed for Backtest Parity) ───────────────────────
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

    # ── 5. Place best trade (Pure mathematical edges only) ─────────
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

    # ── 6. Update open-position mark prices ──────────────────────────────
    try:
        logger.info("[6/6] Updating mark prices for open positions...")
        update_mark_prices()
    except Exception as e:
        logger.error(f"[6/6] Mark price update failed: {e}\n{traceback.format_exc()}")

    # ── 7. Adaptive mapper (once per day) ───────────────────────────────
    if run_adapt:
        try:
            logger.info("[7/7] Running adaptive instrument mapper (daily weight update)...")
            updated = run_adaptive_mapping(force=True)
            if updated:
                logger.info("[7/7] Adaptive mapper: weights updated and saved.")
            else:
                logger.info("[7/7] Adaptive mapper: no closed trades yet — weights unchanged.")
        except Exception as e:
            logger.error(f"[7/7] Adaptive mapper failed: {e}\n{traceback.format_exc()}")

    logger.info(f"Cycle {cycle_num} complete.\n")
    return len(stored)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    logger.info("┌──────────────────────────────────────────────────────────────┐")
    logger.info("│         Polymarket Trading System — Starting                  │")
    logger.info("│  Sandbox-only simulation — no broker connectivity             │")
    logger.info("│  No external API keys or secrets required                     │")
    logger.info("│  Active:  Mon–Fri 07:30–18:00 ET → every 15 min              │")
    logger.info("│  Idle:    weekday off-hours & full weekends → deep sleep      │")
    logger.info("└──────────────────────────────────────────────────────────────┘")

    init_db()
    init_csv_files()
    logger.info("Database and CSV files initialised (sandbox filesystem only)")

    cycle_num = 0
    last_snapshot_count = 0
    last_run_et = now_et() - timedelta(hours=2)  # force immediate first run
    last_adapt_date: Optional[str] = None         # tracks daily adaptive mapper call

    while True:
        # Decide whether it is time to run
        wait = seconds_until_next_cycle(last_run_et)

        if wait > 30:
            # next_market_open() is always correct: handles same-day, next-day,
            # and full weekend skips (Fri close → Mon 07:30).
            nxt = next_market_open()
            if is_market_hours():
                # Already inside the window — next run is 15 min from last
                nxt_display = (last_run_et + timedelta(seconds=MARKET_HOURS_INTERVAL_S)).strftime('%H:%M:%S ET')
                reason = "market hours"
            else:
                nxt_display = nxt.strftime('%A %Y-%m-%d %H:%M ET')
                reason = "weekend — sleeping until Monday open" if not is_weekday() else "after close — sleeping until next open"
            logger.info(f"Sleeping {wait}s ({wait//3600}h {(wait%3600)//60}m) — next run: {nxt_display} [{reason}]")
            # Wall-clock sleep: poll every 5s so sandbox can't stall us.
            # We sleep until the target wall-clock time, not a counted duration.
            started_outside = not is_market_hours()
            wake_at = time.time() + wait
            try:
                while True:
                    remaining = wake_at - time.time()
                    if remaining <= 0:
                        break
                    # Early exit when transitioning INTO market hours from outside
                    if started_outside and is_market_hours():
                        break
                    time.sleep(min(5, remaining))
            except KeyboardInterrupt:
                logger.info("Interrupted — shutting down")
                break

        cycle_num += 1
        start = time.time()
        last_run_et = now_et()

        # Run adaptive mapper once per calendar day (first cycle of each market day)
        today_str = last_run_et.strftime("%Y-%m-%d")
        run_adapt = (last_adapt_date != today_str)
        if run_adapt:
            last_adapt_date = today_str

        try:
            last_snapshot_count = run_cycle(cycle_num, last_snapshot_count, run_adapt=run_adapt)
        except KeyboardInterrupt:
            logger.info("Interrupted — shutting down")
            break
        except Exception as e:
            logger.error(f"Unhandled error in cycle {cycle_num}: {e}\n{traceback.format_exc()}")

        elapsed = time.time() - start
        logger.info(f"Cycle took {elapsed:.1f}s")


if __name__ == "__main__":
    main()
