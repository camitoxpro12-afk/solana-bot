"""
Modulo de noticias en tiempo real.
Usa CryptoPanic y feeds RSS de Solana para detectar sentimiento.
"""

import asyncio
import time
from typing import Dict, List, Optional
import httpx
import config


# Cache global de noticias y sentimiento
_news_cache: List[Dict] = []
_sol_sentiment_score: float = 2.5  # Neutral (0-5)
_token_mentions: Dict[str, float] = {}  # address -> sentiment boost
_last_fetch: float = 0


BULLISH_WORDS = [
    "surge", "pump", "moon", "bullish", "rally", "breakout", "ath", "gain",
    "explosion", "massive", "huge", "flying", "100x", "gem", "launch",
    "sube", "alza", "subida", "disparo", "toro", "bullish"
]
BEARISH_WORDS = [
    "crash", "dump", "rug", "scam", "fraud", "sell", "bearish", "collapse",
    "down", "drop", "hack", "exploit", "warning", "danger", "avoid",
    "baja", "caida", "estafa", "fraude", "vender", "oso"
]


def _analyze_sentiment(text: str) -> float:
    """Returns 0-5 sentiment score (2.5 = neutral)"""
    text_lower = text.lower()
    bull_count = sum(1 for w in BULLISH_WORDS if w in text_lower)
    bear_count = sum(1 for w in BEARISH_WORDS if w in text_lower)

    if bull_count == 0 and bear_count == 0:
        return 2.5
    total = bull_count + bear_count
    bull_ratio = bull_count / total
    return round(1.0 + bull_ratio * 4.0, 2)  # Maps 0->1, 1->5


async def _fetch_cryptopanic() -> List[Dict]:
    if not config.CRYPTOPANIC_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={
                    "auth_token": config.CRYPTOPANIC_API_KEY,
                    "currencies": "SOL",
                    "public": "true",
                    "filter": "hot",
                    "limit": 20,
                }
            )
            if r.status_code == 200:
                posts = r.json().get("results", [])
                return [
                    {
                        "title": p.get("title", ""),
                        "url": p.get("url", ""),
                        "source": p.get("source", {}).get("title", ""),
                        "published": p.get("published_at", ""),
                        "sentiment": p.get("votes", {}).get("positive", 0) - p.get("votes", {}).get("negative", 0),
                    }
                    for p in posts
                ]
    except Exception:
        pass
    return []


# Feeds RSS de medios cripto - GRATIS, sin API key
RSS_FEEDS = [
    ("Cointelegraph", "https://cointelegraph.com/rss/tag/solana"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt", "https://decrypt.co/feed"),
]


def _parse_rss(content: bytes, source: str) -> List[Dict]:
    """Parsea un feed RSS/Atom. Usa ElementTree y, si falla, regex como respaldo."""
    items = []
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(content)
        # RSS 2.0 (<item>) o Atom (<entry>)
        nodes = list(root.iter("item")) or list(root.iter("{http://www.w3.org/2005/Atom}entry"))
        for node in nodes:
            title = node.findtext("title") or node.findtext("{http://www.w3.org/2005/Atom}title") or ""
            link = node.findtext("link") or ""
            if not link:  # Atom: link es un atributo href
                le = node.find("{http://www.w3.org/2005/Atom}link")
                link = le.get("href") if le is not None else ""
            title = title.strip()
            if title:
                items.append({"title": title, "url": link.strip(), "source": source, "sentiment": 0})
    except Exception:
        # Respaldo: regex sobre el texto crudo
        import re
        text = content.decode("utf-8", errors="ignore")
        titles = re.findall(r"<title[^>]*>\s*<!\[CDATA\[(.*?)\]\]>", text, re.DOTALL) or \
                 re.findall(r"<title[^>]*>(.*?)</title>", text, re.DOTALL)
        for t in titles[1:]:
            t = t.strip()
            if t:
                items.append({"title": t, "url": "", "source": source, "sentiment": 0})
    return items


async def _fetch_rss() -> List[Dict]:
    """Noticias cripto desde feeds RSS publicos (sin API key)."""
    news = []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for source, url in RSS_FEEDS:
            try:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (news-bot)"})
                if r.status_code == 200:
                    news.extend(_parse_rss(r.content, source)[:12])
            except Exception:
                continue
    return news


async def refresh_news():
    """Background task: refreshes news cache every NEWS_INTERVAL seconds"""
    global _news_cache, _sol_sentiment_score, _last_fetch

    # Fuente principal sin key: RSS de medios cripto. CryptoPanic como mejora opcional.
    news = await _fetch_rss()
    cryptopanic = await _fetch_cryptopanic()
    if cryptopanic:
        news = cryptopanic + news

    # Deduplica por titulo
    seen = set()
    unique = []
    for n in news:
        t = n.get("title", "").strip()
        if t and t not in seen:
            seen.add(t)
            unique.append(n)

    if unique:
        _news_cache = unique[:30]
        sentiments = [_analyze_sentiment(n["title"]) for n in unique]
        if sentiments:
            _sol_sentiment_score = round(sum(sentiments) / len(sentiments), 2)

    _last_fetch = time.time()


def get_sol_sentiment() -> float:
    """Returns current SOL sentiment score (0-5)"""
    return _sol_sentiment_score


def get_token_sentiment_boost(token_name: str, token_symbol: str) -> float:
    """
    Check if a specific token is mentioned in recent news.
    Returns 0-5 sentiment bonus.
    """
    if not _news_cache:
        return 2.5

    name_lower = token_name.lower()
    sym_lower = token_symbol.lower()
    relevant = []

    for n in _news_cache:
        title_lower = n["title"].lower()
        if name_lower in title_lower or (len(sym_lower) > 2 and sym_lower in title_lower):
            relevant.append(_analyze_sentiment(n["title"]))

    if not relevant:
        return _sol_sentiment_score  # Use general SOL sentiment

    # Blend token-specific + general sentiment
    token_sent = sum(relevant) / len(relevant)
    return round(0.6 * token_sent + 0.4 * _sol_sentiment_score, 2)


def get_recent_news() -> List[Dict]:
    return _news_cache[:20]


async def news_loop():
    """Runs continuously, refreshing news in background"""
    while True:
        try:
            await refresh_news()
        except Exception:
            pass
        await asyncio.sleep(config.NEWS_INTERVAL)
