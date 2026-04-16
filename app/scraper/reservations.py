"""Scrape the covermanager Angular calendar for available days/services/times."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

from ..config import get_settings
from ..models import Service

log = logging.getLogger(__name__)

# Heuristic mapping of French service header → Service enum
_SERVICE_LABEL_RE = {
    Service.LUNCH: re.compile(r"d[ée]jeuner|midi|lunch", re.IGNORECASE),
    Service.DINNER: re.compile(r"d[îi]ner|soir|dinner", re.IGNORECASE),
}


@dataclass(frozen=True)
class ScrapedSlot:
    slot_date: date
    service: Service
    times: tuple[str, ...] = field(default_factory=tuple)


def _wait_for_calendar(page: Page, timeout_ms: int) -> None:
    page.wait_for_selector(
        "td.disponibility, td[data-handler='selectDay'], .ui-datepicker-calendar",
        timeout=timeout_ms,
        state="attached",
    )
    # Give Angular a moment to bind
    page.wait_for_timeout(400)


def _extract_visible_yellow_days(page: Page) -> list[date]:
    """Return dates of all bookable cells currently visible across all month panels."""
    cells = page.locator("td.disponibility[data-handler='selectDay']")
    out: list[date] = []
    n = cells.count()
    for i in range(n):
        c = cells.nth(i)
        try:
            month_str = c.get_attribute("data-month")
            year_str = c.get_attribute("data-year")
            day_text = (c.locator("a").first.inner_text() or "").strip()
            if month_str is None or year_str is None or not day_text.isdigit():
                continue
            # data-month is 0-indexed (jQuery UI datepicker convention)
            d = date(int(year_str), int(month_str) + 1, int(day_text))
            out.append(d)
        except Exception:
            continue
    # Deduplicate (two visible months can't share dates, but be safe)
    return sorted(set(out))


def _click_next_month(page: Page) -> bool:
    """Click the 'next' month arrow. Returns True if it advanced."""
    selectors = [
        "a.ui-datepicker-next:not(.ui-state-disabled)",
        ".ui-datepicker-next:not(.ui-state-disabled)",
        "[ng-click*='next']",
    ]
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.count() > 0 and loc.is_visible():
                loc.click()
                page.wait_for_timeout(500)  # let calendar redraw
                return True
        except Exception:
            continue
    return False


def _click_day(page: Page, target: date) -> bool:
    """Click a specific day cell. Returns True if click landed."""
    sel = (
        f"td.disponibility[data-handler='selectDay']"
        f"[data-month='{target.month - 1}'][data-year='{target.year}']"
    )
    cells = page.locator(sel)
    n = cells.count()
    for i in range(n):
        c = cells.nth(i)
        try:
            day_text = (c.locator("a").first.inner_text() or "").strip()
            if day_text == str(target.day):
                c.locator("a").first.click()
                page.wait_for_timeout(600)
                return True
        except Exception:
            continue
    return False


def _read_services_for_day(page: Page) -> dict[Service, list[str]]:
    """After clicking a day, read each service block (Déjeuner/Dîner) and its time slots."""
    out: dict[Service, list[str]] = {}
    # Wait briefly for service blocks to render
    try:
        page.wait_for_selector("h6.service-name", timeout=4000, state="visible")
    except PWTimeout:
        return out

    headers = page.locator("h6.service-name:visible")
    n = headers.count()
    for i in range(n):
        h = headers.nth(i)
        label = (h.inner_text() or "").strip()
        svc: Service | None = None
        for s, rx in _SERVICE_LABEL_RE.items():
            if rx.search(label):
                svc = s
                break
        if svc is None:
            continue

        times: list[str] = []
        # Look for time-like buttons in the same logical block (sibling or descendant of parent).
        # We search a generous container; covermanager uses "cover-time" / "time" classes.
        try:
            container = h.locator("xpath=ancestor::*[self::div or self::section][1]")
            time_nodes = container.locator(
                "button:has-text(':'), .cover-time, .time, [ng-click*='select_time'], [ng-click*='SelectTime']"
            )
            tn = time_nodes.count()
            for j in range(tn):
                t = (time_nodes.nth(j).inner_text() or "").strip()
                m = re.search(r"\b(\d{1,2}[:hH]\d{2})\b", t)
                if m:
                    times.append(m.group(1).replace("h", ":").replace("H", ":"))
        except Exception:
            pass

        # Dedup + keep order
        seen: set[str] = set()
        uniq: list[str] = []
        for t in times:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        out[svc] = uniq
    return out


def scrape_reservations() -> tuple[list[ScrapedSlot], dict]:
    """Scrape the covermanager calendar. Returns (slots, summary)."""
    settings = get_settings()
    started = datetime.now(timezone.utc)
    summary: dict = {"yellow_days": 0, "months_visited": 0, "errors": []}
    slots: list[ScrapedSlot] = []
    seen_dates: set[date] = set()

    # Strict cap: even with side-by-side panels, lookahead_months iterations covers it.
    max_clicks = max(1, settings.lookahead_months) + 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            locale="fr-FR",
            viewport={"width": 1280, "height": 1600},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        try:
            # SPAs often never reach "networkidle" (constant polling) — use DCL and rely on
            # our explicit calendar wait below.
            page.goto(settings.reservation_url, timeout=settings.playwright_timeout_ms,
                      wait_until="domcontentloaded")
            _wait_for_calendar(page, max(settings.playwright_timeout_ms, 30_000))

            for step in range(max_clicks):
                summary["months_visited"] = step + 1
                yellow = [d for d in _extract_visible_yellow_days(page) if d not in seen_dates]
                seen_dates.update(yellow)
                summary["yellow_days"] += len(yellow)

                if not settings.skip_day_detail:
                    for d in yellow:
                        try:
                            if _click_day(page, d):
                                services = _read_services_for_day(page)
                                if services:
                                    for svc, times in services.items():
                                        slots.append(ScrapedSlot(d, svc, tuple(times)))
                                else:
                                    # Couldn't determine service → record both as unknown? Default lunch+dinner false.
                                    log.info("no service block found for %s, recording as lunch+dinner empty", d)
                                    slots.append(ScrapedSlot(d, Service.LUNCH, ()))
                                    slots.append(ScrapedSlot(d, Service.DINNER, ()))
                        except Exception as e:
                            log.warning("day detail failed for %s: %s", d, e)
                            summary["errors"].append(f"{d.isoformat()}: {e!r}")
                else:
                    for d in yellow:
                        slots.append(ScrapedSlot(d, Service.LUNCH, ()))
                        slots.append(ScrapedSlot(d, Service.DINNER, ()))

                if not _click_next_month(page):
                    break
                try:
                    _wait_for_calendar(page, settings.playwright_timeout_ms)
                except PWTimeout:
                    log.warning("calendar did not re-render after next click; stopping")
                    break
        finally:
            ctx.close()
            browser.close()

    summary["duration_ms"] = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return slots, summary
