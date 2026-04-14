"""
Database initialization — creates markets.db schema.
Run once (idempotent).
"""
import sqlite3
import os
from config import DB_PATH, SIGNALS_CSV, TRADES_CSV
import csv

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ── markets ──────────────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS markets (
        id TEXT PRIMARY KEY,
        question TEXT,
        category TEXT,
        end_date TEXT,
        volume_usd REAL,
        active INTEGER DEFAULT 1,
        last_updated TEXT
    )""")

    # ── outcomes (each market has 1-N outcomes / tokens) ─────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id TEXT,
        outcome_name TEXT,
        token_id TEXT,
        current_price REAL,
        last_updated TEXT,
        UNIQUE(market_id, outcome_name)
    )""")

    # ── price_history — one row per outcome per snapshot ─────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id TEXT,
        outcome_name TEXT,
        token_id TEXT,
        price REAL,
        timestamp TEXT
    )""")

    # ── edge detection cache ─────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS signals_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id TEXT,
        outcome_name TEXT,
        old_prob REAL,
        new_prob REAL,
        change_pp REAL,
        correlated_instrument TEXT,
        edge_score REAL,
        explained INTEGER DEFAULT 0,
        news_summary TEXT,
        timestamp TEXT
    )""")

    # ── market signal cooldowns ─────────────────────────────────────────────
    # Prevents re-trading the same (market_id, side) signal repeatedly.
    c.execute("""
    CREATE TABLE IF NOT EXISTS market_signal_cooldowns (
        market_id TEXT NOT NULL,
        side TEXT NOT NULL,
        last_trade_ts TEXT NOT NULL,
        trade_id TEXT,
        PRIMARY KEY (market_id, side)
    )""")

    conn.commit()
    conn.close()

def init_csv_files():
    """Create CSV headers if files don't exist."""
    if not os.path.exists(SIGNALS_CSV):
        os.makedirs(os.path.dirname(SIGNALS_CSV), exist_ok=True)
        with open(SIGNALS_CSV, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "market_id", "market_name", "outcome",
                "old_prob", "new_prob", "change_pp",
                "correlated_instrument", "edge_score",
                "explained", "news_summary", "news_check_method",
                "theme", "trade_eligible"
            ])

    if not os.path.exists(TRADES_CSV):
        with open(TRADES_CSV, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "trade_id", "signal_id", "timestamp", "symbol", "side", "quantity",
                "entry_price", "mark_price", "unrealized_pnl",
                "stop_loss", "take_profit", "open_date", "close_date",
                "realized_pnl", "close_reason",
                "market_id", "market_name", "edge_score",
                "execution_venue", "broker", "status", "theme"
            ])

if __name__ == "__main__":
    init_db()
    init_csv_files()
    print("✓ Database and CSV files initialized.")
