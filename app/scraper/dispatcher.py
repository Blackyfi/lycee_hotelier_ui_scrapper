"""Reconcile scraped slots against DB state and dispatch notifications."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import (
    AvailableSlot,
    NotificationKind,
    NotificationSent,
    Preference,
    RestaurantInfo,
    ScrapeRun,
    Service,
    User,
)
from ..notifications.gchat import send_gchat
from ..notifications.gmail import send_email
from ..notifications.payload import NotifPayload, SlotInfo
from .reservations import ScrapedSlot, scrape_reservations

log = logging.getLogger(__name__)


def _user_wants_service(user: User, service: Service) -> bool:
    if not user.enabled:
        return False
    if user.preference in (Preference.BOTH, Preference.ANY):
        return True
    return user.preference.value == service.value


def _slot_to_info(slot: AvailableSlot) -> SlotInfo:
    return SlotInfo(slot_date=slot.slot_date, service=slot.service, times=tuple(slot.times))


def _restaurant_location(session: Session) -> tuple[str | None, str | None]:
    """Return (address, maps_url) from the scraped RestaurantInfo, if any."""
    from urllib.parse import quote
    row = session.query(RestaurantInfo).first()
    if not row or not row.address:
        return None, None
    return row.address, f"https://www.google.com/maps/search/?api=1&query={quote(row.address)}"


def _send_batch(session: Session, user: User, slots: list[AvailableSlot],
                kind: NotificationKind, booking_url: str) -> None:
    """Send one notification per channel covering all given slots; record per-slot rows."""
    if not slots:
        return
    address, maps_url = _restaurant_location(session)
    payload = NotifPayload(
        kind=kind,
        slots=tuple(_slot_to_info(s) for s in slots),
        booking_url=booking_url,
        # email dropped this; gchat keeps it
        dashboard_url=None,
        unsubscribe_token=user.command_token,
        address=address,
        maps_url=maps_url,
    )

    if user.notify_email and user.email:
        ok, err = send_email(user.email, payload)
        for s in slots:
            session.add(NotificationSent(
                user_id=user.id, slot_id=s.id, kind=kind, channel="email",
                success=ok, error_message=err,
            ))

    if user.notify_gchat and user.gchat_webhook:
        # GChat card may include the dashboard link if useful — keep it for chat only
        gchat_payload = NotifPayload(
            kind=kind,
            slots=payload.slots,
            booking_url=payload.booking_url,
            dashboard_url=f"http://localhost:{get_settings().app_port}/",
            address=address,
            maps_url=maps_url,
        )
        ok, err = send_gchat(user.gchat_webhook, gchat_payload)
        for s in slots:
            session.add(NotificationSent(
                user_id=user.id, slot_id=s.id, kind=kind, channel="gchat",
                success=ok, error_message=err,
            ))


def _already_sent_today(session: Session, user_id: int, slot_id: int,
                        kind: NotificationKind, day: date) -> bool:
    return session.query(NotificationSent).filter(
        NotificationSent.user_id == user_id,
        NotificationSent.slot_id == slot_id,
        NotificationSent.kind == kind,
        NotificationSent.sent_day == day,
        NotificationSent.success.is_(True),
    ).first() is not None


def reconcile_and_notify(scraped: list[ScrapedSlot]) -> dict:
    """Apply scraped state to DB and dispatch notifications. Returns counts."""
    settings = get_settings()
    today = datetime.now(timezone.utc).date()
    counts = {"new_open": 0, "closed": 0, "reminders": 0, "still_open": 0,
              "batches_sent": 0}

    scraped_map: dict[tuple[date, Service], tuple[str, ...]] = {
        (s.slot_date, s.service): s.times for s in scraped
    }

    with session_scope() as session:
        currently_open: list[AvailableSlot] = (
            session.query(AvailableSlot)
            .filter(AvailableSlot.is_currently_available.is_(True))
            .all()
        )
        users: list[User] = session.query(User).filter(User.enabled.is_(True)).all()

        # 1. Close slots that disappeared from the scrape
        for db_slot in currently_open:
            key = (db_slot.slot_date, db_slot.service)
            if key not in scraped_map:
                db_slot.is_currently_available = False
                db_slot.closed_at = datetime.now(timezone.utc)
                counts["closed"] += 1

        # 2. Process scraped slots — accumulate, don't dispatch yet
        newly_open: list[AvailableSlot] = []
        still_open: list[AvailableSlot] = []

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
                existing.last_seen_at = datetime.now(timezone.utc)
                existing.closed_at = None
                if list(times):
                    existing.times = list(times)
                if was_closed:
                    counts["new_open"] += 1
                    newly_open.append(existing)
                else:
                    counts["still_open"] += 1
                    still_open.append(existing)

        # 3. Dispatch — one batched notification per user per kind
        for u in users:
            user_new = [s for s in newly_open if _user_wants_service(u, s.service)]
            if user_new:
                _send_batch(session, u, user_new, NotificationKind.OPEN, settings.reservation_url)
                counts["batches_sent"] += 1

            if u.daily_reminder:
                user_rem = [
                    s for s in still_open
                    if _user_wants_service(u, s.service)
                    and not _already_sent_today(session, u.id, s.id,
                                                 NotificationKind.REMINDER, today)
                ]
                if user_rem:
                    _send_batch(session, u, user_rem, NotificationKind.REMINDER,
                                settings.reservation_url)
                    counts["reminders"] += len(user_rem)
                    counts["batches_sent"] += 1

    return counts


def run_reservation_scrape() -> dict:
    """Top-level: scrape, reconcile, persist run record. Safe to call from scheduler."""
    started = datetime.now(timezone.utc)
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
        finished = datetime.now(timezone.utc)
        with session_scope() as s:
            s.add(ScrapeRun(
                kind="reservations",
                started_at=started,
                finished_at=finished,
                success=error is None,
                error_message=error,
                n_yellow_days=summary.get("yellow_days", 0) if isinstance(summary, dict) else 0,
                n_new_slots=counts.get("new_open", 0),
                n_closed_slots=counts.get("closed", 0),
                duration_ms=int((finished - started).total_seconds() * 1000),
            ))
    return {"summary": summary, "counts": counts, "error": error}
