"""Fetch business & economics headlines from free RSS feeds — no API key required."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

_FEEDS = [
    ("CNBC Business", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147"),
    ("Yahoo Finance", "https://finance.yahoo.com/rss/topfinstories"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
]

_TIMEOUT = 6.0


def fetch_headlines(max_items: int = 8, *, source: str | None = None) -> list[str]:
    """
    Return up to ``max_items`` headline strings from the first reachable RSS feed.
    Each item is plain text: "Title — Source".
    Returns an empty list on failure (always safe to call).
    """
    feeds = _FEEDS
    if source:
        feeds = [(n, u) for n, u in _FEEDS if source.lower() in n.lower()] or _FEEDS

    for feed_name, url in feeds:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read()
            root = ET.fromstring(raw)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            headlines: list[str] = []
            for item in items[:max_items]:
                title_el = item.find("title")
                if title_el is None:
                    title_el = item.find("atom:title", ns)
                if title_el is not None and title_el.text:
                    title = title_el.text.strip().rstrip(".")
                    headlines.append(f"{title} — {feed_name}")
            if headlines:
                return headlines
        except Exception:
            continue

    return []


def headlines_speech(max_items: int = 8) -> str:
    """Return a spoken phrase listing top headlines, or empty string if unavailable."""
    items = fetch_headlines(max_items)
    if not items:
        return ""
    if len(items) == 1:
        return f"In the news: {items[0]}."
    joined = ". ".join(items)
    return f"Top headlines: {joined}."
