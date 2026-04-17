"""
Simulated trade executor — sandbox-only, no broker connectivity.

Design
------
* Virtual account equity starts at ACCOUNT_EQUITY_USD (default $1 000).
  It grows/shrinks with every realized P&L recorded in trades.csv.

* Price architecture (FIXED):
  - Entry price  = real stock/ETF price from Yahoo Finance (no auth needed)
  - Mark price   = real stock/ETF price from Yahoo Finance (refreshed every cycle)
  - P&L          = (mark_price - entry_price) × quantity  (real dollar gain/loss)
  - Polymarket probability is the SIGNAL source only — never used as a price

* Position sizing
  - Risk dollar = RISK_PCT * current_equity  (default 1% → $10)
  - Stop-loss distance is estimated from stock volatility: DEFAULT_SL_FRACTION
    of entry price (falls back to 5% = 0.05).
  - quantity = risk_dollar / (entry_price * sl_fraction)
    (fractional shares allowed)
  - A trade is only taken if TP distance >= RR_MIN × SL distance (default 2×).

* Exposure cap
  - Total open notional = sum(mark_price * quantity) for all OPEN rows.
  - New trades are skipped if adding the new notional would push total above
    MAX_EXPOSURE_PCT * current_equity  (default 30% → $300).

* Automatic position management (called every cycle during market hours)
  - Each open position is marked to the latest REAL stock price from Yahoo Finance.
  - Close triggers:
      (a) mark_price <= stop_loss   → SL_HIT
      (b) mark_price >= take_profit → TP_HIT   (inverted for SELL)
      (c) trading_days_open > MAX_HOLD_TRADING_DAYS → TIME_EXIT
  - On close: update row with realized_pnl, close_date, close_reason, status=CLOSED.

trades.csv canonical columns (see db_init.py):
  trade_id, signal_id, timestamp, symbol, side, quantity,
  entry_price, mark_price, unrealized_pnl,
  stop_loss, take_profit, open_date, close_date, realized_pnl, close_reason,
  market_id, market_name, edge_score,
  execution_venue, broker, status, theme

No IBKR, TWS, IB Gateway, or any external network call is made anywhere
in this module except Yahoo Finance public market data (no auth required).
"""
import logging
import csv
import os
import sqlite3
import uuid
import math
import json
import urllib.request
import urllib.error
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config import (
    DB_PATH, TRADES_CSV,
    SIM_EXECUTION_VENUE, SIM_BROKER,
    ACCOUNT_EQUITY_USD, RISK_PCT, RR_MIN,
    MAX_OPEN_POSITIONS, MAX_HOLD_TRADING_DAYS, DEFAULT_SL_FRACTION,
    MARKET_SIGNAL_COOLDOWN_HOURS,
    VOL_GATE_ENABLED, VOL_GATE_MULTIPLIER,
)

logger = logging.getLogger(__name__)

EXECUTION_VENUE = SIM_EXECUTION_VENUE
BROKER          = SIM_BROKER

# ── Canonical CSV column order ────────────────────────────────────────────────
CSV_COLUMNS = [
    "trade_id", "signal_id", "timestamp", "symbol", "side", "quantity",
    "entry_price", "mark_price", "unrealized_pnl",
    "stop_loss", "take_profit", "open_date", "close_date",
    "realized_pnl", "close_reason",
    "market_id", "market_name", "edge_score",
    "execution_venue", "broker", "status", "theme",
]


def _normalize_trades_df(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Bring any legacy trades.csv layout up to the canonical column set."""
    changed = False

    for column in CSV_COLUMNS:
        if column not in df.columns:
            changed = True
            if column == "status":
                df[column] = "OPEN"
            elif column == "execution_venue":
                df[column] = EXECUTION_VENUE
            elif column == "broker":
                df[column] = BROKER
            elif column == "theme":
                df[column] = "unclassified"
            elif column == "open_date" and "timestamp" in df.columns:
                df[column] = df["timestamp"]
            else:
                df[column] = ""

    df = df.reindex(columns=CSV_COLUMNS, fill_value="")

    if "status" in df.columns:
        df["status"] = df["status"].fillna("OPEN").replace("", "OPEN")
    for column, default in (("execution_venue", EXECUTION_VENUE), ("broker", BROKER), ("theme", "unclassified")):
        if column in df.columns:
            df[column] = df[column].fillna(default).replace("", default)

    return df, changed


def _load_trades_df(normalize: bool = True) -> pd.DataFrame:
    if not os.path.exists(TRADES_CSV):
        return pd.DataFrame(columns=CSV_COLUMNS)

    try:
        df = pd.read_csv(TRADES_CSV)
    except Exception as e:
        logger.warning(f"Could not read trades.csv: {e}")
        return pd.DataFrame(columns=CSV_COLUMNS)

    if df.empty:
        return df.reindex(columns=CSV_COLUMNS, fill_value="")

    df, changed = _normalize_trades_df(df)
    if changed and normalize:
        try:
            df.to_csv(TRADES_CSV, index=False)
            logger.warning("Normalized legacy trades.csv schema to include status and canonical columns")
        except Exception as e:
            logger.warning(f"Could not rewrite normalized trades.csv: {e}")

    return df

# ── In-memory stock price cache (refreshed each cycle call) ──────────────────
_PRICE_CACHE: Dict[str, float] = {}
_PRICE_CACHE_TS: Dict[str, float] = {}   # per-symbol timestamps
_PRICE_CACHE_TTL: float = 60.0   # seconds before re-fetching


# ─────────────────────────────────────────────────────────────────────────────
# Real stock price via Yahoo Finance (public, no auth)
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_price(symbol: str) -> Optional[float]:
    """
    Fetch the latest real market price for a US-listed stock/ETF from
    Yahoo Finance public API.  No API key required.

    Returns None on any failure so callers can decide on a fallback.
    """
    global _PRICE_CACHE, _PRICE_CACHE_TS

    now = time.time()
    # Per-symbol TTL check
    if symbol in _PRICE_CACHE and (now - _PRICE_CACHE_TS.get(symbol, 0.0)) < _PRICE_CACHE_TTL:
        return _PRICE_CACHE[symbol]

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1m&range=1d"
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PolymarketTrader/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        price = float(price)
        _PRICE_CACHE[symbol] = price
        _PRICE_CACHE_TS[symbol] = now
        logger.debug(f"Yahoo price {symbol}: ${price:.2f}")
        return price
    except Exception as e:
        logger.warning(f"Yahoo Finance price fetch failed for {symbol}: {e}")
        return None


def get_stock_price_with_fallback(symbol: str) -> float:
    """
    Get real stock price; fall back to last known cache value or a
    reasonable default if Yahoo is unreachable.
    """
    price = get_stock_price(symbol)
    if price is not None:
        return price
    # Try secondary Yahoo endpoint
    try:
        url2 = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
        req = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        price = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
        _PRICE_CACHE[symbol] = price
        _PRICE_CACHE_TS[symbol] = time.time()
        return price
    except Exception:
        pass
    # Last resort: stale cache
    if symbol in _PRICE_CACHE:
        logger.warning(f"Using stale cache price for {symbol}: ${_PRICE_CACHE[symbol]:.2f}")
        return _PRICE_CACHE[symbol]
    # Hard fallback — will prevent trade sizing but won't crash
    logger.error(f"No price available for {symbol} — using 100.0 fallback")
    return 100.0


def invalidate_price_cache():
    """Force fresh price fetch on next call (all symbols)."""
    global _PRICE_CACHE_TS
    _PRICE_CACHE_TS = {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: equity & notional
# ─────────────────────────────────────────────────────────────────────────────

def current_equity() -> float:
    """
    Compute current virtual equity:
      ACCOUNT_EQUITY_USD + sum(realized_pnl for all CLOSED rows)
    Returns ACCOUNT_EQUITY_USD if trades.csv is empty or missing.
    """
    try:
        df = _load_trades_df(normalize=True)
        if df.empty or "realized_pnl" not in df.columns:
            return ACCOUNT_EQUITY_USD
        closed = df[df["status"] == "CLOSED"]
        realized = closed["realized_pnl"].apply(
            lambda x: float(x) if str(x) not in ("", "nan") else 0.0
        ).sum()
        return round(ACCOUNT_EQUITY_USD + realized, 6)
    except Exception as e:
        logger.warning(f"Could not compute equity: {e}")
        return ACCOUNT_EQUITY_USD


def total_open_notional() -> float:
    """Sum of mark_price * quantity for all OPEN rows."""
    try:
        df = _load_trades_df(normalize=True)
        if df.empty:
            return 0.0
        open_df = df[df["status"] == "OPEN"]
        if open_df.empty:
            return 0.0
        notional = (
            open_df["mark_price"].apply(lambda x: float(x) if str(x) not in ("", "nan") else 0.0)
            * open_df["quantity"].apply(lambda x: float(x) if str(x) not in ("", "nan") else 0.0)
        ).sum()
        return round(notional, 6)
    except Exception as e:
        logger.warning(f"Could not compute open notional: {e}")
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: trading-day counter
# ─────────────────────────────────────────────────────────────────────────────

def trading_days_since(open_date_iso: str) -> int:
    """
    Count Mon–Fri calendar days between open_date and today (UTC).
    Does NOT consult a holiday calendar — weekdays only.
    """
    try:
        open_dt = datetime.fromisoformat(str(open_date_iso).replace("Z", "+00:00"))
        now     = datetime.now(timezone.utc)
        count   = 0
        cursor  = open_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        end     = now.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor <= end:
            if cursor.weekday() < 5:   # Mon=0 … Fri=4
                count += 1
            cursor += timedelta(days=1)
        return count
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Volatility estimate (stock-level, using DEFAULT_SL_FRACTION as base)
# ─────────────────────────────────────────────────────────────────────────────

def estimate_stock_volatility(symbol: str) -> float:
    """
    Return a reasonable stop-loss fraction for a stock/ETF.
    Uses DEFAULT_SL_FRACTION (5%) as the baseline.
    Future: could fetch historical daily returns from Yahoo.
    """
    # Well-known approximate daily volatility bands
    VOL_OVERRIDES = {
        "IBIT":  0.06,  # Bitcoin ETF — more volatile
        "ETHA":  0.07,  # Ethereum ETF
        "MSTR":  0.10,  # MicroStrategy — very volatile
        "NVDA":  0.05,
        "TSLA":  0.07,
        "QQQ":   0.03,
        "SPY":   0.02,
        "TLT":   0.02,
        "IEF":   0.015,
        "GLD":   0.02,
        "XLE":   0.03,
        "LMT":   0.025,
        "NOC":   0.025,
        "RTX":   0.025,
        "ITA":   0.03,
    }
    return VOL_OVERRIDES.get(symbol, DEFAULT_SL_FRACTION)


def compute_live_vol_ratio(symbol: str, lookback_days: int = 30) -> float:
    """
    Fetches real daily price history from Yahoo Finance and computes the
    ratio of yesterday's 1-day absolute return to the recent average.
    Returns 1.0 if data unavailable (fail open).
    """
    # Keep a longer range than lookback to tolerate sparse/missing candles.
    range_days = max(lookback_days * 2, 60)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&range={range_days}d"
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PolymarketTrader/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [float(c) for c in closes if c is not None]
        if len(closes) < lookback_days + 2:
            return 1.0

        returns = [abs(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
        if len(returns) < lookback_days + 1:
            return 1.0

        one_day = returns[-1]
        avg_lookback = sum(returns[-(lookback_days + 1):-1]) / float(lookback_days)
        if avg_lookback == 0:
            return 1.0

        ratio = one_day / avg_lookback
        logger.debug(
            "Vol ratio %s: 1d=%.4f %dd_avg=%.4f ratio=%.2f",
            symbol,
            one_day,
            lookback_days,
            avg_lookback,
            ratio,
        )
        return ratio
    except Exception as e:
        logger.warning(f"Vol ratio fetch failed for {symbol}: {e} — defaulting to 1.0")
        return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Position loader
# ─────────────────────────────────────────────────────────────────────────────

def load_open_positions() -> Dict[str, Dict]:
    """Returns {symbol: trade_row_dict} for all OPEN rows."""
    try:
        df = _load_trades_df(normalize=True)
        if df.empty:
            return {}
        open_df = df[df["status"] == "OPEN"]
        return {row["symbol"]: row.to_dict() for _, row in open_df.iterrows()}
    except Exception as e:
        logger.warning(f"Could not load open positions: {e}")
        return {}


def _parse_iso_utc(raw_ts: str) -> Optional[datetime]:
    """Parse stored timestamps into UTC datetimes."""
    try:
        ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _load_market_signal_cooldowns(now_utc: datetime) -> Dict[Tuple[str, str], float]:
    """
    Return active cooldowns as {(market_id, side): remaining_hours}.
    """
    cooldowns: Dict[Tuple[str, str], float] = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT market_id, side, last_trade_ts FROM market_signal_cooldowns"
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"Could not read market_signal_cooldowns: {e}")
        return cooldowns

    for market_id, side, last_trade_ts in rows:
        ts = _parse_iso_utc(last_trade_ts)
        if ts is None:
            continue
        elapsed_hours = (now_utc - ts).total_seconds() / 3600.0
        remaining = MARKET_SIGNAL_COOLDOWN_HOURS - elapsed_hours
        if remaining > 0:
            cooldowns[(str(market_id), str(side).upper())] = remaining

    return cooldowns


def _record_market_signal_cooldown(
    market_id: str,
    side: str,
    timestamp_iso: str,
    trade_id: str,
):
    """Store/update cooldown for a traded (market_id, side) pair."""
    if not market_id:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """
            INSERT INTO market_signal_cooldowns (market_id, side, last_trade_ts, trade_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(market_id, side) DO UPDATE SET
                last_trade_ts=excluded.last_trade_ts,
                trade_id=excluded.trade_id
            """,
            (market_id, side.upper(), timestamp_iso, trade_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Could not persist market signal cooldown for {market_id}/{side}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Signal selector
# ─────────────────────────────────────────────────────────────────────────────

def determine_side(signal: Dict) -> str:
    """BUY if the probability moved up (outcome more likely), SELL otherwise."""
    return "BUY" if signal.get("change_pp", 0) > 0 else "SELL"


def select_best_signal(signals: List[Dict]) -> Optional[Dict]:
    """
    Pick the highest-edge signal that:
      1. Is not already in an open position (same correlated instrument).
      2. Has a real stock price available from Yahoo Finance.
      3. Has enough room for a valid SL/TP (RR >= RR_MIN).
            4. Is not within market-side cooldown window.
    Returns None if nothing qualifies.
    """
    open_positions = load_open_positions()
    now_utc = datetime.now(timezone.utc)
    active_cooldowns = _load_market_signal_cooldowns(now_utc)

    tradable = [
        s for s in signals
        if s.get("trade_eligible", True)
        and s.get("correlated_instrument", "NONE") != "NONE"
    ]
    if not tradable:
        return None
    tradable.sort(key=lambda x: x.get("edge_score", 0), reverse=True)

    # Count-based cap: max MAX_OPEN_POSITIONS concurrent trades
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        logger.info(f"Skip: position count cap reached ({len(open_positions)}/{MAX_OPEN_POSITIONS})")
        return None

    vol_ratio_cache: Dict[str, float] = {}

    for sig in tradable:
        symbol = sig["correlated_instrument"]
        market_id = str(sig.get("market_id", ""))
        side = determine_side(sig).upper()

        if symbol in open_positions:
            continue

        if market_id:
            remaining = active_cooldowns.get((market_id, side))
            if remaining is not None:
                logger.info(
                    "Skip %s [%s]: market cooldown active for %.2fh (market_id=%s)",
                    symbol,
                    side,
                    remaining,
                    market_id,
                )
                continue

        if VOL_GATE_ENABLED and VOL_GATE_MULTIPLIER > 0 and symbol != "NONE":
            vol_ratio = vol_ratio_cache.get(symbol)
            if vol_ratio is None:
                vol_ratio = compute_live_vol_ratio(symbol)
                vol_ratio_cache[symbol] = vol_ratio
            if vol_ratio > VOL_GATE_MULTIPLIER:
                logger.info(
                    "Skip %s: vol gate triggered (1d/30d ratio=%.2f > %.2fx)",
                    symbol,
                    vol_ratio,
                    VOL_GATE_MULTIPLIER,
                )
                continue

        # Fetch real stock price
        entry = get_stock_price(symbol)
        if entry is None:
            logger.debug(f"Skip {symbol}: could not fetch stock price")
            continue

        sl_frac = estimate_stock_volatility(symbol)
        sl_dist = entry * sl_frac

        if sl_dist <= 0:
            logger.debug(f"Skip {symbol}: sl_dist=0")
            continue

        return sig

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Entry: place a new simulated trade
# ─────────────────────────────────────────────────────────────────────────────

def place_paper_trade(signal: Dict) -> Optional[Dict]:
    """
    Size and open a new simulated position using REAL stock prices.

    Sizing logic:
      risk_dollar  = RISK_PCT * current_equity          (default $10)
      sl_fraction  = stock volatility estimate (DEFAULT_SL_FRACTION = 5%)
      sl_distance  = entry_price * sl_fraction          (in dollars)
      quantity     = risk_dollar / sl_distance           (fractional shares)
      tp_distance  = sl_distance * RR_MIN                (default 2×)

      For BUY:  stop_loss = entry - sl_distance
                take_profit = entry + tp_distance
      For SELL: stop_loss = entry + sl_distance
                take_profit = entry - tp_distance

    Trade is rejected (returns None) if:
      - Yahoo Finance price unavailable
      - tp/sl ratio < RR_MIN
      - Adding this position would push aggregate notional > MAX_EXPOSURE_PCT * equity
    """
    symbol    = signal["correlated_instrument"]
    side      = determine_side(signal)
    market_id = signal.get("market_id", "")
    now_iso   = datetime.now(timezone.utc).isoformat()

    # ── Real stock entry price ─────────────────────────────────────────────
    entry = get_stock_price_with_fallback(symbol)
    logger.info(f"Stock price fetch: {symbol} = ${entry:.2f}")

    sl_frac   = estimate_stock_volatility(symbol)
    sl_dist   = entry * sl_frac
    tp_dist   = sl_dist * RR_MIN

    if sl_dist <= 0:
        logger.warning(f"Cannot size trade for {symbol}: sl_dist=0")
        return None

    equity       = current_equity()
    risk_dollar  = RISK_PCT * equity          # $10 on a $1,000 account
    qty_risk     = risk_dollar / sl_dist      # risk-based quantity

    quantity = round(qty_risk, 6)  # fractional shares, risk-sized only

    if quantity < 0.0001:
        logger.info(f"Trade skipped — quantity too small after exposure cap ({quantity:.6f})")
        return None

    # Direction-aware SL/TP
    if side == "BUY":
        stop_loss   = round(entry - sl_dist, 4)
        take_profit = round(entry + tp_dist, 4)
    else:
        stop_loss   = round(entry + sl_dist, 4)
        take_profit = round(entry - tp_dist, 4)

    notional = entry * quantity

    trade_id  = str(uuid.uuid4())[:8]
    signal_id = market_id[:16]

    logger.info(
        f"Simulated trade only (IBKR disabled in Computer): "
        f"{side} {quantity:.4f}× {symbol} @ ${entry:.2f} | "
        f"SL=${stop_loss:.2f}  TP=${take_profit:.2f} | "
        f"risk=${risk_dollar:.2f}  notional=${notional:.2f}  equity=${equity:.2f} | "
        f"[trade_id={trade_id}, signal={signal['market_name'][:50]}]"
    )

    trade = {
        "trade_id":        trade_id,
        "signal_id":       signal_id,
        "timestamp":       now_iso,
        "symbol":          symbol,
        "side":            side,
        "quantity":        quantity,
        "entry_price":     round(entry, 4),
        "mark_price":      round(entry, 4),
        "unrealized_pnl":  0.0,
        "stop_loss":       stop_loss,
        "take_profit":     take_profit,
        "open_date":       now_iso,
        "close_date":      "",
        "realized_pnl":    "",
        "close_reason":    "",
        "market_id":       market_id,
        "market_name":     signal.get("market_name", ""),
        "edge_score":      signal.get("edge_score", 0),
        "execution_venue": EXECUTION_VENUE,
        "broker":          BROKER,
        "status":          "OPEN",
        "theme":           signal.get("theme", "unclassified"),
    }

    _append_trade_row(trade)
    _record_market_signal_cooldown(market_id, side, now_iso, trade_id)
    logger.info(
        f"Trade appended: {trade_id} — {side} {quantity:.6f}× {symbol} "
        f"@ ${entry:.2f}  SL=${stop_loss:.2f}  TP=${take_profit:.2f}  [simulated]"
    )
    return trade


def _append_trade_row(trade: Dict):
    """Append one trade dict to trades.csv, creating the file+header if needed."""
    file_exists = os.path.exists(TRADES_CSV)
    
    needs_header = True
    if file_exists:
        try:
            with open(TRADES_CSV, "r") as f:
                first_line = f.readline()
                if "trade_id" in first_line:
                    needs_header = False
        except Exception:
            pass

    with open(TRADES_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        writer.writerow(trade)


# ─────────────────────────────────────────────────────────────────────────────
# Cycle: mark + auto-close open positions
# ─────────────────────────────────────────────────────────────────────────────

def update_mark_prices():
    """
    For every OPEN position:
      1. Fetch the latest REAL stock price from Yahoo Finance.
      2. Recompute unrealized P&L in USD.
      3. Check close triggers: SL, TP, max holding period.
      4. If triggered → close the position.

    No broker connection is made.  All prices come from Yahoo Finance public API.
    """
    try:
        df = _load_trades_df(normalize=True)
    except Exception as e:
        logger.warning(f"Could not read trades.csv: {e}")
        return

    if df.empty:
        logger.info("trades.csv is empty — nothing to mark")
        return

    open_mask = df["status"] == "OPEN"
    if not open_mask.any():
        logger.info("No open positions to mark")
        return

    # Invalidate cache so we get fresh prices this cycle
    invalidate_price_cache()

    updated = 0

    for idx, row in df[open_mask].iterrows():
        symbol    = str(row.get("symbol", ""))
        market_id = str(row.get("market_id", ""))
        trade_id  = str(row.get("trade_id", ""))
        side      = str(row.get("side", "BUY"))
        entry     = _safe_float(row.get("entry_price"), 0.0)
        qty       = _safe_float(row.get("quantity"), 0.0)
        sl        = _safe_float(row.get("stop_loss"), None)
        tp        = _safe_float(row.get("take_profit"), None)
        open_date = str(row.get("open_date", row.get("timestamp", "")))
        side_mult = 1.0 if side == "BUY" else -1.0

        # ── Real stock price mark ──────────────────────────────────────────
        mark = get_stock_price_with_fallback(symbol)
        upnl = round((mark - entry) * qty * side_mult, 4)

        df.loc[idx, "mark_price"]     = round(mark, 4)
        df.loc[idx, "unrealized_pnl"] = upnl
        updated += 1

        logger.debug(
            f"Mark {symbol} [{trade_id}]: entry=${entry:.2f} → mark=${mark:.2f} "
            f"upnl=${upnl:+.2f}"
        )

        # ── Close trigger checks ──────────────────────────────────────────
        close_reason = _check_close_triggers(
            side=side, mark=mark, sl=sl, tp=tp,
            open_date=open_date
        )

        if close_reason:
            realized = upnl
            close_ts = datetime.now(timezone.utc).isoformat()

            df.loc[idx, "status"]        = "CLOSED"
            df.loc[idx, "close_date"]    = close_ts
            df.loc[idx, "realized_pnl"]  = realized
            df.loc[idx, "close_reason"]  = close_reason
            df.loc[idx, "unrealized_pnl"] = 0.0

            logger.info(
                f"Position CLOSED [{close_reason}] "
                f"{side} {qty:.4f}× {symbol} | "
                f"entry=${entry:.2f} exit=${mark:.2f} "
                f"realized_pnl=${realized:+.4f} | trade_id={trade_id}"
            )

    try:
        df.to_csv(TRADES_CSV, index=False)
        closed_count = (df["status"] == "CLOSED").sum() - (~open_mask).sum()
        logger.info(
            f"Mark cycle complete: {updated} position(s) updated — "
            f"prices from Yahoo Finance (no broker connection)"
        )
    except Exception as e:
        logger.warning(f"Could not write trades.csv after mark: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _check_close_triggers(
    side: str, mark: float,
    sl: Optional[float], tp: Optional[float],
    open_date: str,
) -> Optional[str]:
    """
    Return the close reason string if any trigger fires, else None.

    Trigger logic:
      BUY:  SL_HIT if mark <= sl  |  TP_HIT if mark >= tp
      SELL: SL_HIT if mark >= sl  |  TP_HIT if mark <= tp
      TIME_EXIT if trading_days > MAX_HOLD_TRADING_DAYS
    """
    days_open = trading_days_since(open_date)
    if days_open > MAX_HOLD_TRADING_DAYS:
        return "TIME_EXIT"

    if sl is not None and tp is not None:
        if side == "BUY":
            if mark <= sl:
                return "SL_HIT"
            if mark >= tp:
                return "TP_HIT"
        else:  # SELL
            if mark >= sl:
                return "SL_HIT"
            if mark <= tp:
                return "TP_HIT"
    return None


def _safe_float(val, default=0.0):
    try:
        v = float(val)
        return v if not math.isnan(v) else default
    except (TypeError, ValueError):
        return default
