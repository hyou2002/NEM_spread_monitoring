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
from xml.etree import ElementTree

import pandas as pd
import requests

FEEDS = {
    "WattClarity": "https://wattclarity.com.au/feed/",
    "RenewEconomy": "https://reneweconomy.com.au/feed/",
}
USER_AGENT = "Mozilla/5.0 NEM-Weekly-Spread-Monitor/1.0 (+internal tool)"
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

    Returns {"items": [...], "errors": {source: msg}}. Each item has source,
    title, link, date (ISO or ''), excerpt, and keywords (matched terms).
    Raises ArticlesUnavailable only if every feed failed.
    """
    sess = session or requests.Session()
    window_lo = run_date - timedelta(days=days)
    items, errors = [], {}

    for name, url in FEEDS.items():
        try:
            resp = sess.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
            parsed = _parse_feed(name, resp.text)
        except (requests.RequestException, ElementTree.ParseError) as exc:
            errors[name] = str(exc)
            continue
        recent = [p for p in parsed
                  if p["date"] is None or window_lo <= p["date"] <= run_date]
        items.extend(recent[:max_per_source])

    if not items and errors:
        raise ArticlesUnavailable(f"모든 아티클 피드 실패: {errors}")

    for p in items:
        hay = f"{p['title']} {p['excerpt']}".lower()
        p["keywords"] = ", ".join(k for k in KEYWORDS if k in hay)
        p["date"] = p["date"].isoformat() if p["date"] else ""
    items.sort(key=lambda p: p["date"], reverse=True)
    return {"items": items, "errors": errors}


def articles_dataframe(result: dict) -> pd.DataFrame:
    cols = ["date", "source", "title", "keywords", "excerpt", "link"]
    if not result["items"]:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(result["items"])[cols]
