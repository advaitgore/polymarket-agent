import requests, json

POLYMARKET_BASE = "https://gamma-api.polymarket.com"
q = "trump"
r = requests.get(f"{POLYMARKET_BASE}/markets", params={"_q": q, "limit": 10, "active": "true"})
print(f"Status: {r.status_code}")
data = r.json()
print(f"Found: {len(data)}")
for m in data:
    print(f"- {m.get('question')}")
