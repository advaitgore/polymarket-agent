"""
Polymarket data fetcher.
Hits the Gamma API + CLOB API to pull:
  - Active markets and metadata
  - Current token prices (implied probabilities)
  - Recent trade history for 24h delta calculation
"""
import requests
import sqlite3
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from config import (
    POLYMARKET_BASE, POLYMARKET_CLOB, DB_PATH,
    FETCH_LIMIT, LOG_FILE
)

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PolymarketResearchBot/1.0",
    "Accept": "application/json"
})

# ── helpers ───────────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, retries: int = 3, backoff: float = 2.0) -> Optional[dict]:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP {r.status_code} on {url}: {e}")
            if r.status_code in (429, 503):
                time.sleep(backoff * (attempt + 1))
            else:
                return None
        except Exception as e:
            logger.warning(f"Request error attempt {attempt+1} for {url}: {e}")
            time.sleep(backoff * (attempt + 1))
    logger.error(f"All retries exhausted for {url}")
    return None

# ── Gamma API — market metadata ───────────────────────────────────────────────

def fetch_active_markets(limit: int = FETCH_LIMIT) -> List[Dict]:
    """
    Fetch active markets from Gamma API.
    Returns list of market dicts.
    """
    markets = []
    offset = 0

    while True:
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "offset": offset
        }
        data = _get(f"{POLYMARKET_BASE}/markets", params=params)
        if not data:
            break

        # Gamma returns a list directly or wrapped in data key
        batch = data if isinstance(data, list) else data.get("data", data.get("markets", []))
        if not batch:
            break

        markets.extend(batch)
        logger.info(f"Fetched {len(batch)} markets (offset {offset})")

        # Polymarket pagination — stop if we got fewer than limit
        if len(batch) < limit:
            break
        offset += limit

        # Rate limit courtesy
        time.sleep(0.3)

        # Cap at 500 markets to keep runtime sane
        if len(markets) >= 500:
            break

    logger.info(f"Total active markets fetched: {len(markets)}")
    return markets

def _parse_json_field(val):
    """Parse a field that may be a JSON string or already a list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            import json
            return json.loads(val)
        except Exception:
            return []
    return []

def fetch_market_prices(market: Dict) -> List[Dict]:
    """
    Extract token/outcome prices from market dict.
    Gamma API stores outcomes/prices as JSON strings in outcomePrices/outcomes/clobTokenIds.
    """
    # Try structured tokens first (newer API)
    raw_tokens = market.get("tokens", [])
    if raw_tokens and isinstance(raw_tokens, list):
        results = []
        for t in raw_tokens:
            results.append({
                "token_id": t.get("token_id", t.get("id", "")),
                "outcome": t.get("outcome", t.get("name", "Yes")),
                "price": float(t.get("price", 0.5)),
            })
        return results

    # Parse JSON string fields
    outcome_names = _parse_json_field(market.get("outcomes", '["Yes","No"]'))
    outcome_prices = _parse_json_field(market.get("outcomePrices", '["0.5","0.5"]'))
    token_ids = _parse_json_field(market.get("clobTokenIds", '[]'))

    results = []
    for i, name in enumerate(outcome_names):
        try:
            price = float(outcome_prices[i]) if i < len(outcome_prices) else 0.5
        except (ValueError, TypeError):
            price = 0.5
        token_id = token_ids[i] if i < len(token_ids) else ""
        results.append({
            "token_id": str(token_id),
            "outcome": str(name),
            "price": price,
        })
    return results

def fetch_clob_price(token_id: str) -> Optional[float]:
    """
    Fetch current mid price from CLOB for a specific token.
    """
    data = _get(f"{POLYMARKET_CLOB}/price", params={"token_id": token_id, "side": "buy"})
    if data and "price" in data:
        return float(data["price"])
    return None

# ── 24h historical prices via Gamma timeseries ────────────────────────────────

def fetch_price_history_24h(market_id: str, token_id: str = None) -> List[Dict]:
    """
    Fetch price timeseries for a market over the last 24 hours.
    """
    # Try CLOB timeseries first
    end_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - 86400  # 24h ago

    if token_id:
        params = {
            "market": token_id,
            "startTs": start_ts,
            "endTs": end_ts,
            "interval": "1h",
            "fidelity": 60
        }
        data = _get(f"{POLYMARKET_CLOB}/prices-history", params=params)
        if data and "history" in data:
            return data["history"]

    # Fallback: Gamma timeseries
    params = {"id": market_id, "startDate": start_ts, "endDate": end_ts, "fidelity": 60}
    data = _get(f"{POLYMARKET_BASE}/prices-history", params=params)
    if data and "history" in data:
        return data["history"]

    return []

# ── Persist to DB ─────────────────────────────────────────────────────────────

def upsert_market(conn: sqlite3.Connection, market: Dict):
    now = datetime.now(timezone.utc).isoformat()
    # Handle category: may be in tags array or category field
    tags = market.get("tags", [])
    if isinstance(tags, list) and tags:
        category = str(tags[0])
    elif isinstance(tags, str):
        category = tags
    else:
        category = market.get("category", "")

    # Volume: volumeNum is already float, volume may be string
    vol = market.get("volumeNum", market.get("volume", 0))
    try:
        vol = float(vol or 0)
    except (ValueError, TypeError):
        vol = 0.0

    market_id = market.get("conditionId", market.get("condition_id", market.get("id", "")))

    conn.execute("""
        INSERT INTO markets (id, question, category, end_date, volume_usd, active, last_updated)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(id) DO UPDATE SET
            question=excluded.question,
            category=excluded.category,
            end_date=excluded.end_date,
            volume_usd=excluded.volume_usd,
            last_updated=excluded.last_updated
    """, (
        market_id,
        market.get("question", ""),
        category,
        market.get("endDateIso", market.get("end_date_iso", market.get("endDate", ""))),
        vol,
        now
    ))

def upsert_outcome(conn: sqlite3.Connection, market_id: str, outcome: str, token_id: str, price: float):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO outcomes (market_id, outcome_name, token_id, current_price, last_updated)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(market_id, outcome_name) DO UPDATE SET
            current_price=excluded.current_price,
            token_id=excluded.token_id,
            last_updated=excluded.last_updated
    """, (market_id, outcome, token_id, price, now))

def insert_price_snapshot(conn: sqlite3.Connection, market_id: str, outcome: str, token_id: str, price: float):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO price_history (market_id, outcome_name, token_id, price, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (market_id, outcome, token_id, price, now))

# ── Main entry ────────────────────────────────────────────────────────────────

def run_fetch_cycle():
    """
    Full data collection cycle:
    1. Fetch active markets
    2. For each market, get current token prices
    3. Persist to DB with timestamp
    Returns list of stored (market_id, outcome, price) tuples for downstream use.
    """
    logger.info("=== Starting Polymarket fetch cycle ===")
    markets = fetch_active_markets()
    if not markets:
        logger.warning("No markets returned — API may be down")
        return []

    conn = sqlite3.connect(DB_PATH)
    stored = []

    for m in markets:
        market_id = m.get("conditionId", m.get("condition_id", m.get("id", "")))
        if not market_id:
            continue

        upsert_market(conn, m)
        tokens = fetch_market_prices(m)

        if not tokens:
            tokens = [
                {"token_id": "", "outcome": "Yes", "price": 0.5},
                {"token_id": "", "outcome": "No",  "price": 0.5},
            ]

        for t in tokens:
            price = t["price"]
            outcome = t["outcome"]
            token_id = t["token_id"]

            upsert_outcome(conn, market_id, outcome, token_id, price)
            insert_price_snapshot(conn, market_id, outcome, token_id, price)
            stored.append((market_id, m.get("question", ""), outcome, price))

    conn.commit()
    conn.close()
    logger.info(f"Fetch cycle complete. Stored {len(stored)} outcome snapshots.")
    return stored


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/user/workspace/polymarket_trader")
    logging.basicConfig(level=logging.INFO)
    results = run_fetch_cycle()
    print(f"Fetched {len(results)} outcomes across all markets.")
    if results:
        print("Sample:", results[:3])
