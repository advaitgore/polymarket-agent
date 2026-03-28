"""Debug: inspect what the Gamma events API actually returns for market tokens."""
import requests, json

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Debug/1.0", "Accept": "application/json"})

# Fetch a small batch of closed events
r = SESSION.get("https://gamma-api.polymarket.com/events", params={
    "closed": "true", "limit": 3, "offset": 0, "order": "endDate", "ascending": "false"
}, timeout=20)

data = r.json()
events = data if isinstance(data, list) else data.get("data", [])

for ev in events:
    print(f"\n=== EVENT: {ev.get('title', 'N/A')[:60]} ===")
    print(f"  Event ID: {ev.get('id')}")
    markets = ev.get("markets", [])
    for m in markets[:2]:
        print(f"\n  Market question: {m.get('question', '')[:60]}")
        print(f"    conditionId: {m.get('conditionId')}")
        print(f"    id: {m.get('id')}")
        print(f"    clobTokenIds: {m.get('clobTokenIds')}")
        print(f"    outcomes: {m.get('outcomes')}")
        print(f"    outcomePrices: {m.get('outcomePrices')}")
        
        # Try to get the actual token IDs
        tokens_raw = m.get("clobTokenIds", "[]")
        if isinstance(tokens_raw, str):
            try:
                tokens = json.loads(tokens_raw)
            except:
                tokens = []
        else:
            tokens = tokens_raw
        print(f"    Parsed tokens: {tokens}")
        
        # Try CLOB price-history with the first token
        if tokens:
            import time
            t = tokens[0]
            print(f"    Trying CLOB prices-history with market={t}...")
            r2 = SESSION.get("https://clob.polymarket.com/prices-history", params={
                "market": t, "interval": "max", "fidelity": 60
            }, timeout=20)
            print(f"    CLOB status: {r2.status_code}")
            body = r2.json()
            if "history" in body:
                hist = body["history"]
                print(f"    History points: {len(hist)}")
                if hist:
                    print(f"    Sample: {hist[0]}")
            else:
                print(f"    Response keys: {list(body.keys())}")
                print(f"    Body (truncated): {str(body)[:200]}")
