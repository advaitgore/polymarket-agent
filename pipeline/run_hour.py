"""
Hourly driver script — spawned by schedule_cron once per hour.

Runs up to 4 pipeline cycles spaced 15 minutes apart, but only during
US market hours (Mon–Fri 07:30–18:00 ET).  Exits after ~60 minutes
so the next cron invocation takes over cleanly.

This keeps individual sleeps to ≤15 minutes, which the sandbox handles
reliably.  The cron is the clock; this script is just the within-hour
executor.
"""
import subprocess
import sys
import os
import time
import logging
from datetime import datetime
import zoneinfo

ET = zoneinfo.ZoneInfo("America/New_York")
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(PIPELINE_DIR, "logs", "system.log")

os.makedirs(os.path.join(PIPELINE_DIR, "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] run_hour: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("run_hour")

MARKET_OPEN_H,  MARKET_OPEN_M  = 7, 30
MARKET_CLOSE_H, MARKET_CLOSE_M = 18, 0
INTERVAL_S = 15 * 60   # 15 minutes between cycles
MAX_CYCLES = 4         # at most 4 cycles per hour-slot


def now_et():
    return datetime.now(ET)


def is_market_hours():
    dt = now_et()
    if dt.weekday() >= 5:
        return False
    mins = dt.hour * 60 + dt.minute
    return (MARKET_OPEN_H * 60 + MARKET_OPEN_M) <= mins < (MARKET_CLOSE_H * 60 + MARKET_CLOSE_M)


def run_one_cycle():
    result = subprocess.run(
        [sys.executable, os.path.join(PIPELINE_DIR, "main.py")],
        cwd=PIPELINE_DIR,
    )
    return result.returncode


def main():
    hour_start = time.time()
    logger.info(f"run_hour starting — {now_et().strftime('%Y-%m-%d %H:%M:%S ET')}")

    cycles_run = 0
    next_run_at = time.time()  # run immediately on first cycle

    while cycles_run < MAX_CYCLES:
        now = time.time()

        # Wait until next_run_at using short 5s polls (wall-clock safe)
        while time.time() < next_run_at:
            time.sleep(5)

        if not is_market_hours():
            logger.info(f"Outside market hours at {now_et().strftime('%H:%M ET')} — skipping cycle")
            # Still advance the schedule so we check again in 15 min
            next_run_at += INTERVAL_S
            cycles_run += 1
            continue

        logger.info(f"Firing cycle {cycles_run + 1}/{MAX_CYCLES} at {now_et().strftime('%H:%M:%S ET')}")
        rc = run_one_cycle()
        if rc != 0:
            logger.error(f"main.py exited with code {rc}")

        cycles_run += 1
        next_run_at += INTERVAL_S

        # Stop early if we've used ~55 minutes (leave 5 min buffer before next cron fires)
        if time.time() - hour_start > 55 * 60:
            logger.info("55-minute budget reached — exiting early for clean handoff to next cron")
            break

    logger.info(f"run_hour done — ran {cycles_run} cycle(s). Exiting.")


if __name__ == "__main__":
    main()
