"""
Watchdog: ensures the pipeline keeps running.
Spawns main.py as a subprocess and restarts it if it dies.
This process itself is what you keep alive.
"""
import subprocess
import sys
import os
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("watchdog")

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(PIPELINE_DIR, "main.py")
LOG_DIR = os.path.join(PIPELINE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "watchdog.log")

os.makedirs(LOG_DIR, exist_ok=True)

def run():
    restart_count = 0
    while True:
        logger.info(f"Starting pipeline (restart #{restart_count})...")
        with open(LOG_FILE, "a") as log_f:
            proc = subprocess.Popen(
                [sys.executable, MAIN_SCRIPT],
                cwd=PIPELINE_DIR,
                stdout=log_f,
                stderr=log_f,
            )
        exit_code = proc.wait()
        restart_count += 1
        logger.info(f"Pipeline exited with code {exit_code}. Restarting in 10s...")
        time.sleep(10)

if __name__ == "__main__":
    run()
