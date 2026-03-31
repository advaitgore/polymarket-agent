"""
Single-cycle runner — called by the cron scheduler each time.
No sleep loop; just runs one full pipeline cycle and exits.
"""
import sys
import os
import logging
import traceback
from datetime import datetime, timezone
import zoneinfo

# Ensure imports resolve from this pipeline directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import LOG_FILE, LOG_DIR
from db_init import init_db, init_csv_files
from fetch_markets import run_fetch_cycle
from detect_signals import run_signal_detection, append_signals_to_csv
from trade_executor import select_best_signal, place_paper_trade, update_mark_prices
from adaptive_mapper import run_adaptive_mapping

ET = zoneinfo.ZoneInfo("America/New_York")

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("run_once")

ADAPT_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".last_adapt_date")


def should_run_adapt() -> bool:
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if os.path.exists(ADAPT_FLAG):
        with open(ADAPT_FLAG) as f:
            if f.read().strip() == today:
                return False
    with open(ADAPT_FLAG, "w") as f:
        f.write(today)
    return True


def main():
    init_db()
    init_csv_files()

    now_et = datetime.now(ET)
    logger.info(f"=== run_once: {now_et.strftime('%Y-%m-%d %H:%M:%S ET')} ===")

    run_adapt = should_run_adapt()

    # 1. Fetch markets
    stored = []
    try:
        logger.info("[1/6] Fetching Polymarket data...")
        stored = run_fetch_cycle()
        logger.info(f"[1/6] Stored {len(stored)} outcome snapshots")
    except Exception as e:
        logger.error(f"[1/6] Fetch failed: {e}\n{traceback.format_exc()}")

    # 2. Detect signals
    signals = []
    try:
        logger.info("[2/6] Running signal detection...")
        signals = run_signal_detection()
        logger.info(f"[2/6] Found {len(signals)} signals")
    except Exception as e:
        logger.error(f"[2/6] Signal detection failed: {e}\n{traceback.format_exc()}")

    # 3. News check bypassed
    logger.info("[3/6] News check bypassed (backtest parity mode)")

    # 4. Write signals
    try:
        logger.info("[4/6] Appending signals to CSV...")
        append_signals_to_csv(signals)
    except Exception as e:
        logger.error(f"[4/6] CSV write failed: {e}")

    # 5. Trade
    try:
        logger.info("[5/6] Evaluating trade opportunity...")
        best = select_best_signal(signals)
        if best:
            logger.info(f"[5/6] Best edge: {best['market_name'][:60]} ({best['correlated_instrument']}, edge={best['edge_score']:.2f})")
            trade = place_paper_trade(best)
            if trade:
                logger.info(f"[5/6] Trade placed: {trade['side']} {trade['quantity']} {trade['symbol']} @ {trade['entry_price']:.2f}")
            else:
                logger.info("[5/6] No trade placed this cycle")
        else:
            logger.info("[5/6] No qualifying signal this cycle")
    except Exception as e:
        logger.error(f"[5/6] Trade execution failed: {e}\n{traceback.format_exc()}")

    # 6. Mark prices
    try:
        logger.info("[6/6] Updating mark prices...")
        update_mark_prices()
    except Exception as e:
        logger.error(f"[6/6] Mark update failed: {e}\n{traceback.format_exc()}")

    # 7. Adaptive mapper (once per day)
    if run_adapt:
        try:
            logger.info("[7/7] Running adaptive mapper...")
            updated = run_adaptive_mapping(force=True)
            logger.info(f"[7/7] Adaptive mapper: {'weights updated' if updated else 'no changes'}")
        except Exception as e:
            logger.error(f"[7/7] Adaptive mapper failed: {e}\n{traceback.format_exc()}")

    logger.info("=== run_once complete ===")


if __name__ == "__main__":
    main()
