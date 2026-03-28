"""
Download historical Polymarket probability data for backtesting - V7 (HIGH PRECISION).

Strategy:
  1. Load all keywords from correlations.json.
  2. Load all noise patterns (non_tradable_patterns) from correlations.json.
  3. Fetch a broad set of ACTIVE and CLOSED markets (high volume focus).
  4. Use local keyword matching (multi-keyword hits for confidence) to find relevant markets.
  5. Strictly exclude noisy categories (sports, entertainment, etc.) using regex.
  6. Fetch and save CLOB price history to backtest/data/polymarket_history.csv.
"""
import os
import time
import requests
import json
import pandas as pd
import re
from datetime import datetime, timezone

POLYMARKET_BASE = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"
CORR_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline", "correlations.json")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PolymarketBacktestFetcher/7.0",
    "Accept": "application/json"
})


def _get(url: str, params: dict = None, retries: int = 3):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = 5 * (attempt + 1)
                time.sleep(wait)
            else:
                return None
        except Exception:
            time.sleep(2)
    return None


def parse_tokens(m):
    raw = m.get("clobTokenIds", "[]")
    try:
        tokens = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except:
        tokens = []
    return tokens


def load_config():
    with open(CORR_JSON) as f:
        data = json.load(f)
    
    keywords_by_theme = {}
    for theme in data["themes"]:
        keywords_by_theme[theme["theme"]] = [kw.lower() for kw in theme.get("keywords", [])]
    
    return keywords_by_theme, data.get("non_tradable_patterns", {})


def is_non_tradable(question: str, patterns: dict) -> bool:
    q = question.lower()
    for cat, pat_list in patterns.items():
        if cat == "description": continue
        for pat in pat_list:
            try:
                if re.search(pat.lower(), q): return True
            except:
                if pat.lower() in q: return True
    
    # Extra noise common in active markets
    custom_noise = [r"\bvs\.?\b", r"\bo/u\b", r"\bspread:\b", r"\bgta\b", r"\bnhl\b", r"\bnba\b", r"\bnfl\b", r"\bmlb\b", r"\bweather\b", r"\btemperature\b"]
    for pat in custom_noise:
        if re.search(pat, q): return True
        
    return False


def score_market(question: str, keywords_by_theme: dict):
    q = question.lower()
    best_theme = "none"
    max_score = 0
    
    for theme, keywords in keywords_by_theme.items():
        score = sum(1 for kw in keywords if kw in q)
        if score > max_score:
            max_score = score
            best_theme = theme
            
    return best_theme, max_score


def fetch_paged_markets(active: bool = True, limit: int = 1000):
    markets = []
    offset = 0
    batch_size = 100
    while len(markets) < limit:
        params = {
            "limit": batch_size,
            "offset": offset,
            "order": "volumeNum",
            "ascending": "false"
        }
        if active:
            params["active"] = "true"
        else:
            params["closed"] = "true"
            
        data = _get(f"{POLYMARKET_BASE}/markets", params=params)
        if not data: break
        batch = data if isinstance(data, list) else data.get("data", [])
        if not batch: break
        
        for m in batch:
            tokens = parse_tokens(m)
            if not tokens: continue
            mid = m.get("conditionId", m.get("id", ""))
            q = m.get("question", "")
            if not mid or not q: continue
            
            markets.append({
                "market_id": mid,
                "question": q,
                "yes_token": tokens[0],
                "volume": float(m.get("volumeNum", m.get("volume", 0)) or 0),
                "active": m.get("active", False)
            })
        if len(batch) < batch_size: break
        offset += batch_size
        time.sleep(0.3)
    return markets


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    keywords_by_theme, non_tradable_patterns = load_config()
    print(f"Loaded keywords for {len(keywords_by_theme)} themes.")

    # Fetch large batches
    print("\nFetching broad batches of ACTIVE and CLOSED markets...")
    active_pool = fetch_paged_markets(active=True, limit=600)
    closed_pool = fetch_paged_markets(active=False, limit=600)
    
    all_raw_markets = active_pool + closed_pool
    print(f"Total raw markets fetched: {len(all_raw_markets)}")

    relevant_markets = []
    seen_ids = set()

    for m in all_raw_markets:
        mid = m["market_id"]
        if mid in seen_ids: continue
        
        # 1. Filter out noise
        if is_non_tradable(m["question"], non_tradable_patterns):
            continue
            
        # 2. Score against themes
        theme, score = score_market(m["question"], keywords_by_theme)
        
        # 3. Require confidence (score >= 1)
        if score >= 1:
            seen_ids.add(mid)
            m["theme"] = theme
            m["relevance_score"] = score
            relevant_markets.append(m)

    print(f"Filtered down to {len(relevant_markets)} high-signal markets.")
    
    # Sort by relevance and volume
    relevant_markets.sort(key=lambda x: (x["relevance_score"], x["volume"]), reverse=True)
    targets = relevant_markets[:200] # Top 200 vetted markets
    
    all_history = []
    fetched = 0
    print(f"\nDownloading price history for top {len(targets)} markets...")
    
    for i, m in enumerate(targets):
        token = m["yes_token"]
        # Use a longer window for historical backtest (e.g. max history)
        res = _get(f"{POLYMARKET_CLOB}/prices-history", params={"market": token, "interval": "max", "fidelity": 60})
        
        if res and "history" in res and res["history"]:
            hist = res["history"]
            fetched += 1
            for pt in hist:
                ts = pt.get("t")
                prob = pt.get("p")
                if ts is not None and prob is not None:
                    dt_str = datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                    all_history.append({
                        "market_id": m["market_id"],
                        "question": m["question"],
                        "timestamp": dt_str,
                        "probability": prob,
                        "theme": m["theme"]
                    })
            if fetched <= 15:
                print(f"  [{i+1}/{len(targets)}] ({m['theme']}) {m['question'][:60]}...")

        time.sleep(0.35)
        if (i+1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(targets)} processed, {len(all_history)} points total.")

    if all_history:
        df = pd.DataFrame(all_history)
        df = df.sort_values(by="timestamp")
        out_file = os.path.join(data_dir, "polymarket_history.csv")
        df.to_csv(out_file, index=False)
        print(f"\nSuccess! Saved {len(df)} points to {out_file}")
        print(f"Date range: {df['timestamp'].min()} -> {df['timestamp'].max()}")


if __name__ == "__main__":
    main()
