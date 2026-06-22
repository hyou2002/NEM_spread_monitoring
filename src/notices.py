"""AEMO market notices for the analysis week (spec Phase 2e).

ISOLATED / BEST-EFFORT: raises only ``NoticesUnavailable`` if the listing cannot
be read; per-file errors are skipped. No AI summary — only the notice's own
fields (ID / type / issue date / external reference / a short Reason excerpt) and
a direct link, for a human to read and write up.

Source: NEMWeb market-notice directory. Each notice is a separate file
    .../Market_Notice/NEMITWEB1_MKTNOTICE_{YYYYMMDD}.R{id}
The date is embedded in the filename, so the week filter needs no per-file fetch.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import requests

LISTING_URL = "https://www.nemweb.com.au/Reports/CURRENT/Market_Notice/"
HOST = "https://www.nemweb.com.au"
USER_AGENT = "Mozilla/5.0 NEM-Weekly-Spread-Monitor/1.0 (+internal tool)"
_FILE_RE = re.compile(r'href="(/Reports/CURRENT/Market_Notice/'
                      r'NEMITWEB1_MKTNOTICE_(\d{8})\.R\d+)"', re.I)


class NoticesUnavailable(RuntimeError):
    """Raised when the AEMO notice listing cannot be fetched."""


def _field(text: str, label: str) -> str:
    m = re.search(rf"{re.escape(label)}\s*:\s*(.+)", text)
    return m.group(1).strip() if m else ""


def _excerpt(text: str, max_chars: int = 300) -> str:
    """First substantive lines of the Reason block (no interpretation added)."""
    after = text.split("Reason :", 1)[-1]
    lines = [ln.strip() for ln in after.splitlines() if ln.strip()]
    # drop the standard banner line if present
    lines = [ln for ln in lines if ln.upper() != "AEMO ELECTRICITY MARKET NOTICE"]
    body = " ".join(lines)
    return (body[:max_chars] + "…") if len(body) > max_chars else body


def fetch_notices(run_date: date, *, max_items: int = 60,
                  session: requests.Session | None = None) -> dict:
    """AEMO market notices issued in the analysis week (prev Monday .. run_date).

    Returns {"items": [ {date,id,type,title,excerpt,link}... ],
             "total_in_week": int, "shown": int}.
    Raises NoticesUnavailable if the directory listing can't be read.
    """
    sess = session or requests.Session()
    try:
        resp = sess.get(LISTING_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise NoticesUnavailable(
            f"AEMO 공지 목록을 가져오지 못했습니다: {exc}") from exc

    week_start = run_date - timedelta(days=7)
    found = []  # (date, path)
    for path, ymd in _FILE_RE.findall(resp.text):
        d = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
        if week_start <= d <= run_date:
            found.append((d, path))
    found = sorted(set(found), reverse=True)
    total = len(found)

    items = []
    for d, path in found[:max_items]:
        try:
            nr = sess.get(HOST + path, headers={"User-Agent": USER_AGENT}, timeout=20)
            nr.raise_for_status()
            txt = nr.text
        except requests.RequestException:
            continue  # skip a single bad file, keep going
        title = _field(txt, "External Reference")
        ntype = _field(txt, "Notice Type Description")
        if _is_price_review(title, ntype):
            continue  # routine "Prices ... subject to review" noise — excluded
        items.append({
            "date": d.isoformat(),
            "type": ntype,
            "title": title,
            "text": _excerpt(txt, 1500),  # shown in-app (file link forces download)
            "link": HOST + path,
        })
    return {"items": items, "total_in_week": total,
            "scanned": min(total, max_items), "shown": len(items)}


def _is_price_review(title: str, ntype: str) -> bool:
    """Routine 'Prices for interval ... are subject to review' notices (noise)."""
    t = (title or "").lower()
    return (t.startswith("prices for interval")
            or "subject to review" in t
            or "subject to review" in (ntype or "").lower())


def notices_dataframe(result: dict) -> pd.DataFrame:
    """Tidy table view of fetch_notices()['items'] (no excerpt)."""
    cols = ["date", "type", "title", "link"]
    if not result["items"]:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(result["items"])[cols]
