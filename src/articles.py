"""Related-article links for the analysis week (spec Phase 2e, best-effort).

ISOLATED / BEST-EFFORT: a failing feed is skipped; if all fail, raises
``ArticlesUnavailable``. NO AI summary — only each item's own RSS title, link,
date and (HTML-stripped) RSS excerpt, for a human to review and write up.

Sources: WattClarity and RenewEconomy public RSS feeds. We surface recent items
near the analysis week; relevance keywords are highlighted, not used to drop
items (the feeds are already energy-market focused).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from itertools import zip_longest
from xml.etree import ElementTree

import pandas as pd
import requests

FEEDS = {
    "WattClarity": "https://wattclarity.com.au/feed/",
    "RenewEconomy": "https://reneweconomy.com.au/feed/",
}
# A realistic browser UA — some hosts (e.g. WattClarity's nginx) reject custom
# bot UAs, especially from datacenter IPs like Streamlit Cloud.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
FEED_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-AU,en;q=0.9",
}
# Highlight (not filter) keywords — generation / demand / spread / battery / price.
KEYWORDS = ["spread", "battery", "storage", "price", "demand", "generation",
            "renewable", "wind", "solar", "coal", "gas", "interconnector",
            "arbitrage", "nem", "aemo"]
_TAG_RE = re.compile(r"<[^>]+>")


class ArticlesUnavailable(RuntimeError):
    """Raised when no article feed could be fetched."""


def _strip_html(text: str, max_chars: int = 300) -> str:
    clean = _TAG_RE.sub("", text or "").replace("&#8217;", "'").strip()
    clean = re.sub(r"\s+", " ", clean)
    return (clean[:max_chars] + "…") if len(clean) > max_chars else clean


def _parse_feed(name: str, xml_text: str) -> list[dict]:
    root = ElementTree.fromstring(xml_text)
    items = []
    for it in root.iterfind(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = it.findtext("description") or ""
        pub = it.findtext("pubDate") or ""
        try:
            dt = parsedate_to_datetime(pub).date() if pub else None
        except (TypeError, ValueError):
            dt = None
        items.append({"source": name, "title": title, "link": link,
                      "date": dt, "excerpt": _strip_html(desc)})
    return items


def fetch_articles(run_date: date, *, days: int = 9, max_per_source: int = 8,
                   session: requests.Session | None = None) -> dict:
    """Recent articles near the analysis week from the RSS feeds.

    Returns {"items": [...], "errors": {source: msg}}. Sources are interleaved
    (round-robin) so a feed that floods recent dates can't bury the other.
    Raises ArticlesUnavailable only if every feed failed.
    """
    sess = session or requests.Session()
    window_lo = run_date - timedelta(days=days)
    per_source: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}

    for name, url in FEEDS.items():
        try:
            resp = sess.get(url, headers=FEED_HEADERS, timeout=30)
            resp.raise_for_status()
            parsed = _parse_feed(name, resp.text)
        except (requests.RequestException, ElementTree.ParseError) as exc:
            errors[name] = str(exc)
            continue
        recent = [p for p in parsed
                  if p["date"] is None or window_lo <= p["date"] <= run_date]
        recent.sort(key=lambda p: (p["date"] or run_date), reverse=True)
        per_source[name] = recent[:max_per_source]

    if not per_source and errors:
        raise ArticlesUnavailable(f"모든 아티클 피드 실패: {errors}")

    items: list[dict] = []
    for group in zip_longest(*per_source.values()):
        items.extend(p for p in group if p is not None)
    for p in items:
        p["date"] = p["date"].isoformat() if p["date"] else ""
    return {"items": items, "errors": errors}


def articles_dataframe(result: dict) -> pd.DataFrame:
    cols = ["date", "source", "title", "link"]
    if not result["items"]:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(result["items"])[cols]
