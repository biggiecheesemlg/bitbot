"""
STEP 1 & 2: Gamma API Market Puller + clobTokenIds Extractor
=============================================================
Fetches the BTC April 2026 event from Polymarket Gamma API,
extracts all outcome markets and their YES/NO clobTokenIds.
"""

import requests
import json
from typing import Optional

GAMMA_BASE = "https://gamma-api.polymarket.com"
EVENT_SLUG  = "what-price-will-bitcoin-hit-april-6-12"


def pull_event_by_slug(slug: str) -> Optional[dict]:
    """Pull a single event from Gamma API by slug."""
    url = f"{GAMMA_BASE}/events"
    params = {"slug": slug, "active": "true"}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"No event found for slug: {slug}")
    return data[0]


def extract_asset_ids(event: dict) -> list[dict]:
    """
    STEP 2: Extract clobTokenIds from every market in the event.
    Returns list of:
      {
        question, condition_id,
        yes_token_id, no_token_id,
        yes_price, no_price,
        volume
      }
    """
    markets_info = []
    for mkt in event.get("markets", []):
        token_ids = mkt.get("clobTokenIds", [])
        outcomes  = json.loads(mkt.get("outcomes", "[]"))
        prices    = json.loads(mkt.get("outcomePrices", "[]"))

        if len(token_ids) < 2:
            continue  # skip non-binary or disabled markets

        markets_info.append({
            "question":      mkt.get("question"),
            "condition_id":  mkt.get("conditionId"),
            "yes_token_id":  token_ids[0],
            "no_token_id":   token_ids[1],
            "yes_price":     float(prices[0]) if prices else None,
            "no_price":      float(prices[1]) if prices else None,
            "volume":        float(mkt.get("volume", 0)),
            "enable_clob":   mkt.get("enableOrderBook", False),
        })

    # Sort by volume descending
    markets_info.sort(key=lambda x: x["volume"], reverse=True)
    return markets_info


if __name__ == "__main__":
    print(f"[STEP 1] Pulling event: {EVENT_SLUG}")
    event = pull_event_by_slug(EVENT_SLUG)
    print(f"  Event title  : {event.get('title')}")
    print(f"  Event ID     : {event.get('id')}")
    print(f"  Markets count: {len(event.get('markets', []))}")

    print("\n[STEP 2] Extracting clobTokenIds...")
    markets = extract_asset_ids(event)
    for m in markets:
        print(f"\n  {m['question']}")
        print(f"    YES token : {m['yes_token_id']}")
        print(f"    NO  token : {m['no_token_id']}")
        print(f"    Prices    : YES={m['yes_price']:.2f}  NO={m['no_price']:.2f}")
        print(f"    Volume    : ${m['volume']:,.0f}")

    # Save for downstream steps
    with open("markets_cache.json", "w") as f:
        json.dump({"event": event, "markets": markets}, f, indent=2)
    print("\n[✓] Saved to markets_cache.json")
