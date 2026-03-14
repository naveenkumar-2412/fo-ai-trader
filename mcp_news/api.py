from fastapi import FastAPI, HTTPException
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from email.utils import parsedate_to_datetime
import requests
import xml.etree.ElementTree as ET
import time
import os

app = FastAPI(title="News MCP", version="1.0.0")

NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=Indian+stock+market+OR+NSE+OR+NIFTY&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=RBI+policy+OR+inflation+India+OR+FII+DII&hl=en-IN&gl=IN&ceid=IN:en",
]

NEWS_TIMEOUT_SEC = int(os.getenv("NEWS_TIMEOUT_SEC", "5"))
NEWS_CACHE_TTL = int(os.getenv("NEWS_CACHE_TTL", "30"))

_cache: Dict[str, Dict[str, Any]] = {}

SYMBOL_KEYWORDS = {
    "NIFTY": ["nifty", "nse", "sensex", "largecap", "index"],
    "BANKNIFTY": ["bank nifty", "banknifty", "banking", "psu bank", "private bank", "rbi"],
    "FINIFTY": ["fin nifty", "finifty", "financial services", "nbfc", "bank", "insurance"],
}

POSITIVE_WORDS = {
    "beats", "surge", "rally", "upgrade", "profit", "growth", "eases", "cools", "bullish", "record high",
}

NEGATIVE_WORDS = {
    "misses", "falls", "crash", "downgrade", "loss", "decline", "spike", "hawkish", "war", "selloff",
}

HIGH_IMPACT_WORDS = {
    "rbi", "policy", "rate hike", "rate cut", "inflation", "cpi", "geopolitical", "war", "budget", "fomc",
}


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < NEWS_CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


def _parse_pub_date(value: str):
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _clean_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _fetch_feed(feed_url: str) -> List[Dict[str, Any]]:
    resp = requests.get(feed_url, timeout=NEWS_TIMEOUT_SEC)
    if resp.status_code != 200:
        return []

    root = ET.fromstring(resp.text)
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date = item.findtext("pubDate") or ""
        items.append(
            {
                "title": title.strip(),
                "link": link.strip(),
                "published_at": pub_date,
                "published_dt": _parse_pub_date(pub_date),
            }
        )
    return items


def _sentiment_score(text: str) -> float:
    lowered = _clean_text(text)
    pos_hits = sum(1 for w in POSITIVE_WORDS if w in lowered)
    neg_hits = sum(1 for w in NEGATIVE_WORDS if w in lowered)
    total = pos_hits + neg_hits
    if total == 0:
        return 0.0
    return round((pos_hits - neg_hits) / total, 4)


def _impact_score(text: str) -> float:
    lowered = _clean_text(text)
    hits = sum(1 for w in HIGH_IMPACT_WORDS if w in lowered)
    return round(min(1.0, hits / 2), 4)


def _symbol_match(title: str, symbol: str) -> bool:
    keywords = SYMBOL_KEYWORDS.get(symbol.upper(), SYMBOL_KEYWORDS["NIFTY"])
    lowered = _clean_text(title)
    return any(k in lowered for k in keywords)


def _get_headlines(symbol: str) -> List[Dict[str, Any]]:
    cache_key = f"headlines:{symbol.upper()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    rows: List[Dict[str, Any]] = []
    seen = set()
    for feed in NEWS_FEEDS:
        for item in _fetch_feed(feed):
            title_key = _clean_text(item["title"])
            if not title_key or title_key in seen:
                continue
            if not _symbol_match(item["title"], symbol):
                continue
            seen.add(title_key)
            rows.append(item)

    rows.sort(key=lambda r: r.get("published_dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    _cache_set(cache_key, rows)
    return rows


@app.get("/headlines")
def headlines(symbol: str = "NIFTY", limit: int = 20):
    try:
        data = _get_headlines(symbol)
        limit = max(1, min(limit, 100))
        out = [
            {
                "title": row["title"],
                "link": row["link"],
                "published_at": row["published_at"],
                "sentiment": _sentiment_score(row["title"]),
                "impact": _impact_score(row["title"]),
            }
            for row in data[:limit]
        ]
        return {"status": "success", "symbol": symbol.upper(), "count": len(out), "data": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/summary")
def summary(symbol: str = "NIFTY", lookback_minutes: int = 15):
    try:
        lookback_minutes = max(1, min(lookback_minutes, 180))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
        rows = _get_headlines(symbol)

        filtered = []
        for row in rows:
            dt = row.get("published_dt")
            if dt is None or dt >= cutoff:
                filtered.append(row)

        if not filtered:
            return {
                "status": "success",
                "symbol": symbol.upper(),
                "data": {
                    "headline_count": 0,
                    "avg_sentiment": 0.0,
                    "avg_impact": 0.0,
                    "high_impact_news": 0,
                },
            }

        sentiments = [_sentiment_score(r["title"]) for r in filtered]
        impacts = [_impact_score(r["title"]) for r in filtered]
        avg_sent = round(sum(sentiments) / len(sentiments), 4)
        avg_imp = round(sum(impacts) / len(impacts), 4)

        return {
            "status": "success",
            "symbol": symbol.upper(),
            "data": {
                "headline_count": len(filtered),
                "avg_sentiment": avg_sent,
                "avg_impact": avg_imp,
                "high_impact_news": int(avg_imp >= 0.5),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {
        "status": "success",
        "data": {
            "feeds": len(NEWS_FEEDS),
            "cache_items": len(_cache),
            "cache_ttl_sec": NEWS_CACHE_TTL,
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
