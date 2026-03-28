import requests
import json
import traceback

def test_endpoint(url, query):
    try:
        r = requests.post(url, json={'query': query})
        if r.status_code == 200:
            print(f"SUCCESS {url}")
            data = r.json().get('data', {})
            print(json.dumps(data, indent=2)[:500])
        else:
            print(f"FAILED {url} - Status: {r.status_code}")
    except Exception as e:
        print(f"ERROR {url}: {e}")

q_fpmm = """
{
  fpmmTrades(first: 5, orderBy: timestamp, orderDirection: desc) {
    id
    timestamp
    fpmm {
      id
    }
  }
}
"""

q_orders = """
{
  orders(first: 5, orderBy: timestamp, orderDirection: desc) {
    id
    timestamp
    maker
  }
}
"""

urls = [
    "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn",
    "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/exchange-v2/0.0.1/gn",
    "https://api.goldsky.com/api/public/project_clqvhl28d07wg01x7dsps1a17/subgraphs/exchange-v2/0.0.1/gn"
]

for u in urls:
    print("\n--- Testing FpmmTrades ---")
    test_endpoint(u, q_fpmm)
    print("\n--- Testing Orders ---")
    test_endpoint(u, q_orders)
