"""Scrape the weekly menu listing and per-week menu image."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings
from ..db import session_scope
from ..models import Menu, ScrapeRun
from .restaurant_info import scrape_restaurant_info

log = logging.getLogger(__name__)

_FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

# Matches strings like "menus du 13 au 17 avril 2026" (case-insensitive, accents tolerant)
_DATE_RANGE_RE = re.compile(
    r"du\s+(\d{1,2})(?:\s+\w+)?\s+au\s+(\d{1,2})\s+([a-zéèûêàîô]+)\s+(\d{4})",
    re.IGNORECASE,
)


@dataclass
class MenuListing:
    title: str
    page_url: str
    week_start: date | None
    week_end: date | None


def _parse_date_range(text: str) -> tuple[date | None, date | None]:
    m = _DATE_RANGE_RE.search(text.lower())
    if not m:
        return None, None
    d1, d2, mon, year = m.groups()
    month = _FR_MONTHS.get(mon.lower())
    if not month:
        return None, None
    try:
        return date(int(year), month, int(d1)), date(int(year), month, int(d2))
    except ValueError:
        return None, None


def _fetch(client: httpx.Client, url: str) -> str:
    r = client.get(url, timeout=20.0, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _parse_listing(html: str, base_url: str) -> list[MenuListing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[MenuListing] = []
    seen: set[str] = set()
    # Be permissive: any anchor whose href looks like a menu detail page
    for a in soup.select("a[href*='menus/']"):
        href = a.get("href", "")
        if not href or "menus/" not in href:
            continue
        full = urljoin(base_url, href)
        if full == base_url.rstrip("/") + "/" or full in seen:
            continue
        if not full.endswith(".html"):
            continue
        title = a.get_text(strip=True) or "Menu"
        ws, we = _parse_date_range(title)
        if not ws:
            ws, we = _parse_date_range(href)
        listings.append(MenuListing(title=title, page_url=full, week_start=ws, week_end=we))
        seen.add(full)
    return listings


def _parse_menu_image(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    # The hotel site uses <img class="img-center"> for the menu image inside the article body.
    img = soup.select_one("img.img-center") or soup.select_one("article img")
    if not img:
        return None
    src = img.get("src")
    return urljoin(base_url, src) if src else None


def _safe_filename(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:200]


def _download_image(client: httpx.Client, image_url: str, dest_dir: Path) -> str:
    fname = _safe_filename(image_url)
    dest = dest_dir / fname
    if dest.exists() and dest.stat().st_size > 0:
        return fname
    r = client.get(image_url, timeout=30.0, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return fname


def scrape_menus() -> dict:
    """Scrape menu list, persist to DB, download images. Returns summary dict."""
    settings = get_settings()
    started = datetime.now(timezone.utc)
    summary = {"total": 0, "new": 0, "updated": 0, "with_image": 0}
    error: str | None = None

    try:
        with httpx.Client(headers={"User-Agent": "lycee-scraper/1.0"}) as client:
            html = _fetch(client, settings.menus_index_url)
            listings = _parse_listing(html, settings.menus_index_url)
            summary["total"] = len(listings)

            with session_scope() as s:
                for li in listings:
                    existing = s.query(Menu).filter(Menu.page_url == li.page_url).one_or_none()
                    is_new = existing is None
                    menu = existing or Menu(page_url=li.page_url, title=li.title)
                    menu.title = li.title
                    if li.week_start:
                        menu.week_start = li.week_start
                    if li.week_end:
                        menu.week_end = li.week_end
                    menu.last_seen_at = datetime.now(timezone.utc)

                    try:
                        detail_html = _fetch(client, li.page_url)
                        img_url = _parse_menu_image(detail_html, li.page_url)
                        if img_url:
                            menu.image_url = img_url
                            try:
                                menu.image_filename = _download_image(
                                    client, img_url, settings.menu_images_dir
                                )
                                summary["with_image"] += 1
                            except Exception as e:
                                log.warning("menu image download failed for %s: %s", img_url, e)
                    except Exception as e:
                        log.warning("menu detail fetch failed for %s: %s", li.page_url, e)

                    if is_new:
                        s.add(menu)
                        summary["new"] += 1
                    else:
                        summary["updated"] += 1

                # Refresh restaurant address while we're at it (cheap, daily).
                try:
                    info = scrape_restaurant_info()
                    summary["address_refreshed"] = info.get("ok", False)
                except Exception as e:
                    log.warning("address refresh failed: %s", e)

                run = ScrapeRun(
                    kind="menus",
                    started_at=started,
                    finished_at=datetime.now(timezone.utc),
                    success=True,
                    n_yellow_days=0,
                    n_new_slots=summary["new"],
                    n_closed_slots=0,
                    duration_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                )
                s.add(run)
    except Exception as e:
        error = str(e)
        log.exception("menu scrape failed")
        with session_scope() as s:
            s.add(ScrapeRun(
                kind="menus",
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                success=False,
                error_message=error,
                duration_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            ))
        summary["error"] = error
    return summary
