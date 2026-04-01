"""
News and context checker — no external API key required.

Priority order:
    1. Public RSS feeds (Reuters, AP, BBC) — unauthenticated
    2. DuckDuckGo Instant Answer API — unauthenticated
    3. Fallback: tag as "unverified (no news check)"

No PERPLEXITY_API_KEY or any other secret is needed. The system runs
end-to-end without any user-provided credentials.
"""
import logging
import time
import re
import requests
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PolymarketResearchBot/1.0",
    "Accept": "application/json, text/html, application/xml"
})

# ── 1. DuckDuckGo Instant Answer API (no key, no rate limit for low traffic) ──

DDG_URL = "https://api.duckduckgo.com/"

def _search_duckduckgo(query: str) -> Optional[str]:
    """Use DuckDuckGo's Instant Answer API — completely free, no auth."""
    try:
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        r = SESSION.get(DDG_URL, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            # AbstractText is the best source
            abstract = data.get("AbstractText", "").strip()
            if abstract and len(abstract) > 50:
                return abstract[:400]
            # RelatedTopics are also useful
            topics = data.get("RelatedTopics", [])
            snippets = []
            for t in topics[:3]:
                text = t.get("Text", "")
                if text and len(text) > 30:
                    snippets.append(text[:150])
            if snippets:
                return " | ".join(snippets)
    except Exception as e:
        logger.debug(f"DuckDuckGo search failed: {e}")
    return None

# ── 2. Public RSS feeds (no auth) ────────────────────────────────────────────

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://rss.ap.org/article/topnews",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
]

def _search_rss(keywords: List[str], max_articles: int = 5) -> Optional[str]:
    """Scan public RSS feeds for recent headlines matching keywords."""
    matches = []
    kw_lower = [k.lower() for k in keywords]

    for feed_url in RSS_FEEDS[:2]:  # limit to 2 feeds to stay fast
        try:
            r = SESSION.get(feed_url, timeout=8)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            # Both RSS 2.0 and Atom formats
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for item in items[:20]:
                title_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
                desc_el = item.find("description") or item.find("{http://www.w3.org/2005/Atom}summary")
                title = (title_el.text or "") if title_el is not None else ""
                desc = (desc_el.text or "") if desc_el is not None else ""
                combined = (title + " " + desc).lower()
                if any(kw in combined for kw in kw_lower):
                    # Strip HTML tags from description
                    clean_desc = re.sub(r"<[^>]+>", "", desc)[:200]
                    matches.append(f"{title}: {clean_desc}".strip(": "))
                    if len(matches) >= max_articles:
                        break
        except Exception as e:
            logger.debug(f"RSS feed {feed_url} failed: {e}")
        if matches:
            break

    return " | ".join(matches[:3]) if matches else None

# ── 4. Extract keywords from market name ─────────────────────────────────────

def _extract_keywords(market_name: str, instrument: str) -> List[str]:
    """Pull key nouns/proper nouns from a market question for RSS matching."""
    # Remove common question words
    stop = {"will", "the", "a", "an", "is", "are", "be", "in", "on", "at",
            "for", "to", "of", "and", "or", "not", "win", "lose", "by", "than"}
    words = re.findall(r"[A-Z][a-z]+|[A-Z]{2,}", market_name)  # proper nouns + acronyms
    keywords = [w for w in words if w.lower() not in stop and len(w) > 2]
    # Add the instrument itself
    if instrument and instrument not in keywords:
        keywords.append(instrument)
    return keywords[:6]

# ── Main classification logic ─────────────────────────────────────────────────

def _classify_response(text: str, market_name: str, change_pp: float) -> tuple[bool, float]:
    """
    Heuristic to decide if text 'explains' the probability move.
    Returns (explained: bool, confidence: float 0-1).
    """
    if not text or len(text) < 60:
        return False, 0.0

    text_lower = text.lower()
    market_lower = market_name.lower()

    # Strong explaining signals
    strong_explains = [
        "announced", "confirmed", "declared", "signed", "passed", "approved",
        "convicted", "arrested", "resigned", "won", "lost", "elected",
        "ceasefire", "deal", "agreement", "merger", "acquisition",
        "earnings beat", "earnings miss", "raised guidance", "cut guidance",
        "fda approved", "fda rejected", "rate cut", "rate hike", "default"
    ]
    strong_count = sum(1 for term in strong_explains if term in text_lower)

    # Check if text is actually about this market's topic
    # Take first 3 words of market name as topic signal
    topic_words = [w.lower() for w in market_name.split()[:5]
                   if len(w) > 3 and w.lower() not in {"will", "the", "win", "lose"}]
    topic_match = sum(1 for w in topic_words if w in text_lower)

    # Generic/vague text that doesn't really explain
    weak_signals = ["related", "trending", "discussion", "mentioned", "context"]
    weak_count = sum(1 for w in weak_signals if w in text_lower)

    if strong_count >= 2 and topic_match >= 1:
        return True, 0.85
    if strong_count >= 1 and topic_match >= 2:
        return True, 0.70
    if strong_count == 0 and weak_count >= 2:
        return False, 0.3
    if topic_match == 0:
        return False, 0.2

    return False, 0.5


def check_news_for_signal(signal: Dict) -> Dict:
    """
    Check news for a single signal. Uses public sources only, no auth needed.
    Tags each signal with:
      - news_summary: text from best source found
      - explained: True/False
            - news_check_method: 'duckduckgo' | 'rss' | 'unverified'
      - edge_score: adjusted based on explained status
    """
    market_name = signal["market_name"]
    change_pp = signal["change_pp"]
    instrument = signal["correlated_instrument"]
    direction = "increased" if change_pp > 0 else "decreased"

    search_query = (
        f"{market_name} {direction} prediction market news"
    )
    keywords = _extract_keywords(market_name, instrument)

    logger.debug(f"News check: {market_name[:60]}… keywords={keywords}")

    text = None
    method = "unverified"

    # ── Try each source in order ──────────────────────────────────────────
    time.sleep(0.3)  # gentle rate limiting
    text = _search_duckduckgo(search_query)
    if text:
        method = "duckduckgo"
    elif keywords:
        text = _search_rss(keywords)
        if text:
            method = "rss"

    # ── Classify ──────────────────────────────────────────────────────────
    raw_score = signal["edge_score"]

    if method == "unverified" or not text:
        # No news data available — tag clearly, keep score neutral
        summary = (
            f"[UNVERIFIED — no news check] Probability {direction} "
            f"{abs(change_pp):.1f}pp. Instrument: {instrument}. "
            f"No news source was reachable; signal flagged as unverified."
        )
        explained = False
        adjusted_score = round(raw_score * 1.0, 3)  # neutral — don't boost or reduce
        signal["news_check_method"] = "unverified"
    else:
        explained, confidence = _classify_response(text, market_name, change_pp)
        tag = "[EXPLAINED]" if explained else "[UNEXPLAINED]"
        summary = f"{tag} (via {method}, conf={confidence:.0%}) {text[:350]}"
        adjusted_score = round(raw_score * (0.3 if explained else 1.5), 3)
        signal["news_check_method"] = method

        if explained:
            logger.info(f"  → EXPLAINED ({method}). Edge {raw_score:.2f} → {adjusted_score:.2f}")
        else:
            logger.info(f"  → UNEXPLAINED ({method}). Edge {raw_score:.2f} → {adjusted_score:.2f}")

    signal["news_summary"] = summary[:500]
    signal["explained"] = explained
    signal["edge_score"] = adjusted_score
    return signal


def enrich_signals_with_news(signals: List[Dict], max_checks: int = 50) -> List[Dict]:
    """
    Run news check for each signal. Caps at max_checks per cycle to stay fast.
    Signals beyond the cap are tagged as unverified.
    """
    logger.info(f"Running news check for {min(len(signals), max_checks)}/{len(signals)} signals...")

    enriched = []
    for i, sig in enumerate(signals):
        if i >= max_checks:
            # Cap exceeded — tag remaining as unverified without network call
            sig["news_summary"] = (
                "[UNVERIFIED — no news check] Beyond per-cycle check limit. "
                "Signal flagged as unverified."
            )
            sig["explained"] = False
            sig["news_check_method"] = "unverified"
            enriched.append(sig)
            continue

        try:
            enriched.append(check_news_for_signal(sig))
        except Exception as e:
            logger.error(f"News check failed for {sig.get('market_name', '?')}: {e}")
            sig["news_summary"] = f"[UNVERIFIED — news check error: {e}]"
            sig["explained"] = False
            sig["news_check_method"] = "unverified"
            enriched.append(sig)

    return enriched


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    test_signal = {
        "market_id": "test_123",
        "market_name": "Will the Fed cut rates in March 2026?",
        "outcome": "Yes",
        "old_prob": 40.0,
        "new_prob": 52.0,
        "change_pp": 12.0,
        "correlated_instrument": "TLT",
        "direction_logic": "higher_prob_outcome_up_means_long",
        "edge_score": 3.6,
        "explained": False,
        "news_summary": ""
    }
    result = check_news_for_signal(test_signal)
    print(f"\nSummary:  {result['news_summary']}")
    print(f"Explained: {result['explained']}")
    print(f"Method:    {result.get('news_check_method')}")
    print(f"Edge:      {result['edge_score']}")
