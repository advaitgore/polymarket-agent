import requests

url = "https://clob.polymarket.com/prices-history"
token = "32356885368383818320491038596041065741639535041499119934308557342080351229340" # Trump nominate Judy Shelton

print("--- Testing interval=all ---")
res3 = requests.get(url, params={"market": token, "interval": "all"})
if res3.status_code == 200:
    hist = res3.json().get('history', [])
    if hist:
        print(f"Interval=all: {len(hist)} points")
        if len(hist)>0:
            import datetime
            print("Start:", datetime.datetime.fromtimestamp(hist[0]['t']).strftime('%Y-%m-%d'))
            print("End:", datetime.datetime.fromtimestamp(hist[-1]['t']).strftime('%Y-%m-%d'))
    else:
        print("Empty history")
        
print("--- Testing interval=1y, fidelity=720 (12 hours) ---")
res4 = requests.get(url, params={"market": token, "interval": "1y", "fidelity": 720})
if res4.status_code == 200:
    hist = res4.json().get('history', [])
    if hist:
        print(f"Interval=1y: {len(hist)} points")
        if len(hist)>0:
            import datetime
            print("Start:", datetime.datetime.fromtimestamp(hist[0]['t']).strftime('%Y-%m-%d'))
            print("End:", datetime.datetime.fromtimestamp(hist[-1]['t']).strftime('%Y-%m-%d'))
