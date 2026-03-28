import requests
import datetime
import time

url = "https://clob.polymarket.com/prices-history"
# Using the token for "Trump nominate Judy Shelton" which we know is a valid market
token = "32356885368383818320491038596041065741639535041499119934308557342080351229340"

# Target: January 2026 (Since we know current data is Feb-Mar 2026)
start_dt = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
end_dt = datetime.datetime(2026, 1, 31, tzinfo=datetime.timezone.utc)

start_ts = int(start_dt.timestamp())
end_ts = int(end_dt.timestamp())

print(f"Testing with start={start_ts} and end={end_ts}")

params_list = [
    {"market": token, "interval": "max", "fidelity": 60, "start": start_ts, "end": end_ts},
    {"market": token, "interval": "max", "fidelity": 60, "startTs": start_ts, "endTs": end_ts},
    {"market": token, "interval": "max", "fidelity": 60, "cursor": "something"},
]

for p in params_list:
    print(f"\n--- Testing params: {p} ---")
    res = requests.get(url, params=p)
    if res.status_code == 200:
        hist = res.json().get('history', [])
        print(f"Returned {len(hist)} points.")
        if hist:
            print("First pt:", datetime.datetime.fromtimestamp(hist[0]['t'], tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            print("Last pt: ", datetime.datetime.fromtimestamp(hist[-1]['t'], tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
    else:
        print(f"Error {res.status_code}")
