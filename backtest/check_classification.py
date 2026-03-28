"""Check what Polymarket questions we have and which ones match themes."""
import os, re, json
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
CORR = os.path.join(BASE, "..", "pipeline", "correlations.json")
POLY = os.path.join(BASE, "data", "polymarket_history.csv")

with open(CORR) as f:
    data = json.load(f)
themes = data["themes"]

df = pd.read_csv(POLY)
questions = df[["market_id", "question"]].drop_duplicates("market_id")

print(f"Total unique markets: {len(questions)}\n")

# Try classifying each
matched = 0
for _, row in questions.iterrows():
    q = row["question"].lower()
    best_theme = "none"
    best_score = 0
    for entry in themes:
        keywords = entry.get("keywords", [])
        score = sum(1 for kw in keywords if kw.lower() in q)
        if score > best_score:
            best_theme = entry["theme"]
            best_score = score
    
    tag = "✅" if best_score > 0 else "❌"
    if best_score > 0:
        matched += 1
    print(f"  {tag} [{best_theme:25s}] (score={best_score:2d}) {row['question'][:70]}")

print(f"\nMatched: {matched}/{len(questions)}")
