"""Scrape the weekly menu listing and per-week menu image."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from ..config import get_settings
from ..db import session_scope
from ..models import Menu, MenuDay, ScrapeRun, Service
from .menu_pdf import parse_menu_pdf
from .restaurant_info import scrape_restaurant_info

log = logging.getLogger(__name__)

_FR_MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

# Matches strings like "menus du 13 au 17 avril 2026" (case-insensitive, accents tolerant)
_DATE_RANGE_RE = re.compile(
    r"du\s+(\d{1,2})(?:\s+\w+)?\s+au\s+(\d{1,2})\s+([a-zéèûêàîô]+)\s+(\d{4})",
    re.IGNORECASE,
)


# Menu posts: old weekly ones live under /agenda/menus/…, newer ones are /agenda/menus-….html
_MENU_PAGE_RE = re.compile(r"/agenda/(?:menus/)?[^/]*menus?[^/]*\.html$", re.IGNORECASE)
# Share links to Acrobat's online viewer; the only other source we open in a browser.
_ACROBAT_RE = re.compile(r"^https://acrobat\.adobe\.com/id/urn:aaid:sc:[A-Za-z0-9:-]+$")
_MAX_PDF_BYTES = 25 * 1024 * 1024


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


def _fetch(client: httpx.Client, url: str, *, retries: int = 2) -> str:
    for attempt in range(retries + 1):
        try:
            r = client.get(url, timeout=20.0, follow_redirects=True)
            r.raise_for_status()
            return r.text
        except Exception:
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise


def _parse_listing(html: str, base_url: str) -> list[MenuListing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[MenuListing] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        full = urljoin(base_url, a.get("href", ""))
        if full in seen or not _MENU_PAGE_RE.search(full.split("?")[0]):
            continue
        title = a.get_text(strip=True) or "Menu"
        ws, we = _parse_date_range(title)
        if not ws:
            ws, we = _parse_date_range(full)
        listings.append(MenuListing(title=title, page_url=full, week_start=ws, week_end=we))
        seen.add(full)
    return listings


def _parse_menu_image(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    # The hotel site uses <img class="img-center"> for the menu image inside the article body.
    for img in soup.select("img.img-center, article img"):
        src = img.get("src")
        if src and "/medias/static/" not in src:  # skip file-type icons (e.g. the PDF glyph)
            return urljoin(base_url, src)
    return None


def _parse_menu_pdf_links(html: str, base_url: str) -> list[str]:
    """PDF attachments and Acrobat share links in the post body, in page order."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one(".content") or soup
    links: list[str] = []
    for a in body.select("a[href]"):
        full = urljoin(base_url, a["href"]).strip()
        if (full.split("?")[0].lower().endswith(".pdf") or _ACROBAT_RE.match(full)) and full not in links:
            links.append(full)
    return links


def _fetch_pdf(client: httpx.Client, url: str) -> bytes | None:
    r = client.get(url, timeout=30.0, follow_redirects=True)
    r.raise_for_status()
    data = r.content
    return data if data[:5] == b"%PDF-" and len(data) <= _MAX_PDF_BYTES else None


def _fetch_acrobat_pdf(url: str) -> bytes | None:
    """Acrobat share links are a JS viewer with no direct download URL: let headless Chromium
    open it and keep the PDF the viewer downloads."""
    got: list[bytes] = []

    def on_response(resp) -> None:
        if got or "pdf" not in resp.headers.get("content-type", "") + resp.url.split("?")[0][-4:]:
            return
        try:
            body = resp.body()
        except Exception:
            return
        if body[:5] == b"%PDF-" and len(body) <= _MAX_PDF_BYTES:
            got.append(body)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_context(locale="fr-FR").new_page()
            page.on("response", on_response)
            page.goto(url, timeout=60_000, wait_until="domcontentloaded")  # the viewer never goes idle
            for _ in range(60):
                if got:
                    break
                page.wait_for_timeout(1000)
        finally:
            browser.close()
    return got[0] if got else None


@dataclass
class _ScrapedPost:
    listing: MenuListing
    image_url: str | None = None
    image_filename: str | None = None
    # (source link, {(date, service): dishes}) in page order; later sources win on overlaps
    pdf_days: list[tuple[str, dict[tuple[date, Service], list[str]]]] = field(default_factory=list)


def _scrape_post(client: httpx.Client, li: MenuListing, images_dir: Path, summary: dict) -> _ScrapedPost:
    post = _ScrapedPost(li)
    try:
        detail_html = _fetch(client, li.page_url)
    except Exception as e:
        log.warning("menu detail fetch failed for %s: %s", li.page_url, e)
        return post

    post.image_url = _parse_menu_image(detail_html, li.page_url)
    if post.image_url:
        try:
            post.image_filename = _download_image(client, post.image_url, images_dir)
            summary["with_image"] += 1
        except Exception as e:
            log.warning("menu image download failed for %s: %s", post.image_url, e)

    for link in _parse_menu_pdf_links(detail_html, li.page_url):
        try:
            data = _fetch_acrobat_pdf(link) if _ACROBAT_RE.match(link) else _fetch_pdf(client, link)
            days = parse_menu_pdf(data) if data else {}
        except Exception as e:
            log.warning("menu pdf failed for %s: %s", link, e)
            continue
        log.info("menu pdf %s: %d services parsed", link, len(days))
        if days:
            post.pdf_days.append((link, days))
    return post


def _store_post(s, post: _ScrapedPost, summary: dict) -> None:
    li = post.listing
    menu = s.query(Menu).filter(Menu.page_url == li.page_url).one_or_none()
    if menu is None:
        menu = Menu(page_url=li.page_url, title=li.title)
        s.add(menu)
        summary["new"] += 1
    else:
        summary["updated"] += 1
    menu.title = li.title
    menu.week_start, menu.week_end = li.week_start or menu.week_start, li.week_end or menu.week_end
    menu.last_seen_at = datetime.now(UTC)
    if post.image_url:
        menu.image_url = post.image_url
    if post.image_filename:
        menu.image_filename = post.image_filename

    for link, days in post.pdf_days:
        for (d, svc), dishes in days.items():
            row = s.query(MenuDay).filter(MenuDay.menu_date == d, MenuDay.service == svc).one_or_none()
            if row is None:
                row = MenuDay(menu_date=d, service=svc, source_url=link)
                s.add(row)
            row.dishes = dishes
            row.source_url = link
            row.updated_at = datetime.now(UTC)
        summary["menu_days"] += len(days)
        if not li.week_start:  # title had no "du … au …": use the dates the PDF covers
            first, last = min(d for d, _ in days), max(d for d, _ in days)
            menu.week_start = min(menu.week_start, first) if menu.week_start else first
            menu.week_end = max(menu.week_end, last) if menu.week_end else last


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
    """Scrape menu posts, their images and PDFs, then persist. Returns summary dict.

    All network work happens before the DB transaction: the Acrobat viewer can take up to a
    minute, and holding SQLite's write lock that long would stall the reservation scraper.
    """
    settings = get_settings()
    started = datetime.now(UTC)
    summary = {"total": 0, "new": 0, "updated": 0, "with_image": 0, "menu_days": 0}
    error: str | None = None

    try:
        with httpx.Client(headers={"User-Agent": "lycee-scraper/1.0"}) as client:
            html = _fetch(client, settings.menus_index_url)
            listings = _parse_listing(html, settings.menus_index_url)
            summary["total"] = len(listings)
            posts = [_scrape_post(client, li, settings.menu_images_dir, summary) for li in listings]

        # Refresh restaurant address while we're at it (cheap, daily).
        try:
            info = scrape_restaurant_info()
            summary["address_refreshed"] = info.get("ok", False)
        except Exception as e:
            log.warning("address refresh failed: %s", e)

        with session_scope() as s:
            for post in posts:
                _store_post(s, post, summary)
            s.add(
                ScrapeRun(
                    kind="menus",
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    success=True,
                    n_yellow_days=0,
                    n_new_slots=summary["new"],
                    n_closed_slots=0,
                    duration_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
                )
            )
    except Exception as e:
        error = str(e)
        log.exception("menu scrape failed")
        with session_scope() as s:
            s.add(
                ScrapeRun(
                    kind="menus",
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    success=False,
                    error_message=error,
                    duration_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
                )
            )
        summary["error"] = error
    return summary
