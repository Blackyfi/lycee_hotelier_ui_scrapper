"""Scrape the restaurant's postal address from the legal-mentions page."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings
from ..db import session_scope
from ..models import RestaurantInfo

log = logging.getLogger(__name__)

# Match a French postal address chunk: street + 5-digit postcode + city.
_ADDR_RE = re.compile(
    r"(?P<street>\d+[^\n,;]{3,80}?)\s*[,\n]?\s*(?P<post>\d{5})\s+(?P<city>[A-Z][A-ZÉÈÀÂÎÔÛa-zéèàâîôû\-' ]{2,40})",
    re.MULTILINE,
)


def _parse_address(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    m = _ADDR_RE.search(text)
    if not m:
        return None
    street = m.group("street").strip(" .,")
    post = m.group("post")
    city = m.group("city").strip()
    return f"{street}, {post} {city}"


def scrape_restaurant_info() -> dict:
    """Refresh the cached restaurant address from /about/legal/. Returns the new row data."""
    settings = get_settings()
    url = f"{settings.site_root.rstrip('/')}/about/legal/"

    html: str | None = None
    last_error: str | None = None
    for attempt in range(3):
        try:
            r = httpx.get(
                url,
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "lycee-scraper/1.0"},
            )
            r.raise_for_status()
            html = r.text
            break
        except Exception as e:
            last_error = str(e)
            log.warning("restaurant info fetch failed (attempt %d/3): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(3.0 * (attempt + 1))

    if html is None:
        return {"ok": False, "error": last_error}

    addr = _parse_address(html)
    if not addr:
        log.info("could not parse address from %s", url)
        return {"ok": False, "error": "address pattern not found"}

    now = datetime.now(UTC)
    with session_scope() as s:
        row = s.query(RestaurantInfo).first()
        if row is None:
            s.add(RestaurantInfo(address=addr, source_url=url, last_refreshed_at=now))
        else:
            row.address = addr
            row.source_url = url
            row.last_refreshed_at = now
    return {"ok": True, "address": addr}
