"""Reconcile scraped slots against DB state and dispatch notifications."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, date, datetime
from email.utils import make_msgid
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import (
    AvailableSlot,
    MenuDay,
    NotificationKind,
    NotificationSent,
    Preference,
    RestaurantInfo,
    ScrapeRun,
    Service,
    User,
)
from ..notifications.gchat import post_gchat, send_gchat
from ..notifications.gmail import send_email
from ..notifications.payload import NotifPayload, SlotInfo
from ..notifications.welcome import CalSlot, WelcomeCalendar, pick_gif
from .reservations import ScrapedSlot, scrape_reservations

log = logging.getLogger(__name__)


def _user_wants_service(user: User, service: Service) -> bool:
    if not user.enabled:
        return False
    if user.preference in (Preference.BOTH, Preference.ANY):
        return True
    return user.preference.value == service.value


def _slot_infos(session: Session, slots: list[AvailableSlot]) -> tuple[SlotInfo, ...]:
    """SlotInfo per slot, with that service's dishes when the menu PDF covers it."""
    dates = {s.slot_date for s in slots}
    menus = {(m.menu_date, m.service): m for m in session.query(MenuDay).filter(MenuDay.menu_date.in_(dates))}
    out = []
    for s in slots:
        m = menus.get((s.slot_date, s.service))
        out.append(
            SlotInfo(
                slot_date=s.slot_date,
                service=s.service,
                times=tuple(s.times),
                dishes=tuple(m.dishes) if m else (),
                menu_url=m.source_url if m else None,
            )
        )
    return tuple(out)


def _restaurant_location(session: Session) -> tuple[str | None, str | None]:
    """Return (address, maps_url) from the scraped RestaurantInfo, if any."""
    row = session.query(RestaurantInfo).first()
    if not row or not row.address:
        return None, None
    return (
        row.address,
        f"https://www.google.com/maps/search/?api=1&query={quote(row.address)}",
    )


def _local_today() -> date:
    tz = ZoneInfo(get_settings().tz)
    return datetime.now(tz).date()


def _record(
    session: Session,
    user: User,
    slots: list[AvailableSlot],
    kind: NotificationKind,
    channel: str,
    ok: bool,
    err: str | None,
) -> None:
    sent_day = _local_today()
    for s in slots:
        session.add(
            NotificationSent(
                user_id=user.id,
                slot_id=s.id,
                kind=kind,
                channel=channel,
                sent_day=sent_day,
                success=ok,
                error_message=err,
            )
        )


def _send_batch(
    session: Session,
    user: User,
    slots: list[AvailableSlot],
    kind: NotificationKind,
    booking_url: str,
) -> None:
    """Send one notification per channel covering all given slots; record per-slot rows."""
    if not slots:
        return
    address, maps_url = _restaurant_location(session)
    payload = NotifPayload(
        kind=kind,
        slots=_slot_infos(session, slots),
        booking_url=booking_url,
        unsubscribe_token=user.command_token,
        address=address,
        maps_url=maps_url,
    )

    if user.notify_email and user.email:
        ok, err = send_email(user.email, payload)
        _record(session, user, slots, kind, "email", ok, err)

    if user.notify_gchat and user.gchat_webhook:
        ok, err = send_gchat(user.gchat_webhook, replace(payload, unsubscribe_token=None))
        _record(session, user, slots, kind, "gchat", ok, err)


def send_welcome_calendar(user_id: int) -> dict:
    """Send a new (or returning) subscriber everything bookable from today onward.

    Sends nothing when no slot is open. Afterwards the user only gets the usual alerts, which
    fire on transitions, so the slots listed here are never re-announced while they stay open.
    """
    settings = get_settings()
    try:
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None or not user.enabled:
                return {"sent": False, "reason": "user missing or disabled"}
            rows = sorted(
                (
                    s
                    for s in session.query(AvailableSlot).filter(AvailableSlot.slot_date >= _local_today()).all()
                    if _user_wants_service(user, s.service)
                ),
                key=lambda s: (s.slot_date, s.service != Service.LUNCH),  # lunch before dinner
            )
            open_rows = [s for s in rows if s.is_currently_available]
            if not open_rows:
                log.info("welcome calendar skipped for user=%s: nothing bookable", user.id)
                return {"sent": False, "reason": "nothing bookable"}

            address, maps_url = _restaurant_location(session)
            services = (
                (Service.LUNCH, Service.DINNER)
                if user.preference in (Preference.BOTH, Preference.ANY)
                else (Service(user.preference.value),)
            )
            cal = WelcomeCalendar(
                name=user.name,
                today=_local_today(),
                slots=tuple(CalSlot(s.slot_date, s.service, tuple(s.times), s.is_currently_available) for s in rows),
                booking_url=settings.reservation_url,
                unsubscribe_token=user.command_token,
                address=address,
                maps_url=maps_url,
                services=services,
            )

            results: dict = {"sent": True}
            send_mail = user.notify_email and bool(user.email)
            if send_mail:
                gif = pick_gif()
                cid = make_msgid(domain="lycee-hotelier")[1:-1] if gif else None
                ok, err = send_email(user.email, replace(cal, gif_cid=cid), {cid: gif} if gif else None)
                _record(session, user, open_rows, NotificationKind.WELCOME, "email", ok, err)
                results["email"] = {"sent": ok, "error": err, "gif": gif.name if gif else None}
            if user.notify_gchat and user.gchat_webhook:
                ok, err = post_gchat(user.gchat_webhook, cal.gchat_message(email_also_sent=send_mail))
                _record(session, user, open_rows, NotificationKind.WELCOME, "gchat", ok, err)
                results["gchat"] = {"sent": ok, "error": err}
            log.info("welcome calendar for user=%s: %s", user.id, results)
            return results
    except Exception:
        log.exception("welcome calendar failed for user=%s", user_id)
        return {"sent": False, "reason": "error"}


def reconcile_and_notify(scraped: list[ScrapedSlot]) -> dict:
    """Apply scraped state to DB and dispatch notifications. Returns counts."""
    settings = get_settings()
    counts = {
        "new_open": 0,
        "closed": 0,
        "still_open": 0,
        "batches_sent": 0,
    }

    scraped_map: dict[tuple[date, Service], tuple[str, ...]] = {(s.slot_date, s.service): s.times for s in scraped}

    with session_scope() as session:
        currently_open: list[AvailableSlot] = (
            session.query(AvailableSlot).filter(AvailableSlot.is_currently_available.is_(True)).all()
        )
        users: list[User] = session.query(User).filter(User.enabled.is_(True)).all()

        # 1. Close slots that disappeared from the scrape
        for db_slot in currently_open:
            key = (db_slot.slot_date, db_slot.service)
            if key not in scraped_map:
                db_slot.is_currently_available = False
                db_slot.closed_at = datetime.now(UTC)
                counts["closed"] += 1

        # 2. Process scraped slots — accumulate, don't dispatch yet
        newly_open: list[AvailableSlot] = []

        for (sdate, svc), times in scraped_map.items():
            existing = (
                session.query(AvailableSlot)
                .filter(AvailableSlot.slot_date == sdate, AvailableSlot.service == svc)
                .one_or_none()
            )

            if existing is None:
                slot = AvailableSlot(slot_date=sdate, service=svc, is_currently_available=True)
                slot.times = list(times)
                session.add(slot)
                session.flush()  # populate slot.id
                counts["new_open"] += 1
                newly_open.append(slot)
            else:
                was_closed = not existing.is_currently_available
                existing.is_currently_available = True
                existing.last_seen_at = datetime.now(UTC)
                existing.closed_at = None
                if list(times):
                    existing.times = list(times)
                if was_closed:
                    counts["new_open"] += 1
                    newly_open.append(existing)
                else:
                    counts["still_open"] += 1

        # 3. Dispatch — one batched notification per user for newly-opened slots
        for u in users:
            user_new = [s for s in newly_open if _user_wants_service(u, s.service)]
            if user_new:
                _send_batch(
                    session,
                    u,
                    user_new,
                    NotificationKind.OPEN,
                    settings.reservation_url,
                )
                counts["batches_sent"] += 1

    return counts


def run_reservation_scrape() -> dict:
    """Top-level: scrape, reconcile, persist run record. Safe to call from scheduler."""
    started = datetime.now(UTC)
    error: str | None = None
    counts: dict = {}
    summary: dict = {}
    try:
        slots, summary = scrape_reservations()
        counts = reconcile_and_notify(slots)
    except Exception as e:
        log.exception("reservation scrape failed")
        error = str(e)
    finally:
        finished = datetime.now(UTC)
        with session_scope() as s:
            s.add(
                ScrapeRun(
                    kind="reservations",
                    started_at=started,
                    finished_at=finished,
                    success=error is None,
                    error_message=error,
                    n_yellow_days=summary.get("yellow_days", 0) if isinstance(summary, dict) else 0,
                    n_new_slots=counts.get("new_open", 0),
                    n_closed_slots=counts.get("closed", 0),
                    duration_ms=int((finished - started).total_seconds() * 1000),
                )
            )
    return {"summary": summary, "counts": counts, "error": error}
