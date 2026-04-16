from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..models import (
    AvailableSlot,
    Menu,
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
from ..scraper.runner import force_menu_scrape, force_reservation_scrape
from .deps import require_admin

router = APIRouter(prefix="/api")


# ─── Schemas ──────────────────────────────────────────────────────────────
class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    gchat_webhook: str | None = None
    preference: Literal["lunch", "dinner", "both", "any"] = "both"
    daily_reminder: bool = True
    notify_email: bool = True
    notify_gchat: bool = True
    enabled: bool = True


class UserPatch(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    gchat_webhook: str | None = None
    preference: Literal["lunch", "dinner", "both", "any"] | None = None
    daily_reminder: bool | None = None
    notify_email: bool | None = None
    notify_gchat: bool | None = None
    enabled: bool | None = None


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    gchat_webhook: str | None
    preference: str
    daily_reminder: bool
    notify_email: bool
    notify_gchat: bool
    enabled: bool
    command_token: str
    created_at: datetime

    @classmethod
    def from_orm_user(cls, u: User) -> "UserOut":
        return cls(
            id=u.id, name=u.name, email=u.email, gchat_webhook=u.gchat_webhook,
            preference=u.preference.value, daily_reminder=u.daily_reminder,
            notify_email=u.notify_email, notify_gchat=u.notify_gchat,
            enabled=u.enabled, command_token=u.command_token, created_at=u.created_at,
        )


# ─── Slots ────────────────────────────────────────────────────────────────
@router.get("/slots")
def list_slots(
    only_available: bool = True,
    since_days: int = 90,
    session: Session = Depends(get_session),
) -> list[dict]:
    q = session.query(AvailableSlot)
    if only_available:
        q = q.filter(AvailableSlot.is_currently_available.is_(True))
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        q = q.filter(AvailableSlot.last_seen_at >= cutoff)
    q = q.order_by(AvailableSlot.slot_date.asc(), AvailableSlot.service.asc())
    return [
        {
            "id": s.id,
            "date": s.slot_date.isoformat(),
            "service": s.service.value,
            "times": s.times,
            "first_seen_at": s.first_seen_at.isoformat(),
            "last_seen_at": s.last_seen_at.isoformat(),
            "closed_at": s.closed_at.isoformat() if s.closed_at else None,
            "is_currently_available": s.is_currently_available,
        }
        for s in q.all()
    ]


# ─── Menus ────────────────────────────────────────────────────────────────
@router.get("/menus")
def list_menus(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.query(Menu).order_by(desc(Menu.week_start.is_(None)), desc(Menu.week_start)).all()
    return [
        {
            "id": m.id,
            "title": m.title,
            "week_start": m.week_start.isoformat() if m.week_start else None,
            "week_end": m.week_end.isoformat() if m.week_end else None,
            "page_url": m.page_url,
            "image_url": m.image_url,
            "image_filename": m.image_filename,
            "first_seen_at": m.first_seen_at.isoformat(),
            "last_seen_at": m.last_seen_at.isoformat(),
        }
        for m in rows
    ]


# ─── Scrape runs (history) ────────────────────────────────────────────────
@router.get("/scrape-runs")
def list_scrape_runs(
    kind: str | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
) -> list[dict]:
    q = session.query(ScrapeRun).order_by(desc(ScrapeRun.started_at))
    if kind:
        q = q.filter(ScrapeRun.kind == kind)
    rows = q.limit(min(limit, 1000)).all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "success": r.success,
            "error_message": r.error_message,
            "n_yellow_days": r.n_yellow_days,
            "n_new_slots": r.n_new_slots,
            "n_closed_slots": r.n_closed_slots,
            "duration_ms": r.duration_ms,
        }
        for r in rows
    ]


@router.get("/stats/summary")
def stats_summary(session: Session = Depends(get_session)) -> dict:
    last_res = (session.query(ScrapeRun)
                .filter(ScrapeRun.kind == "reservations")
                .order_by(desc(ScrapeRun.started_at)).first())
    last_menu = (session.query(ScrapeRun)
                 .filter(ScrapeRun.kind == "menus")
                 .order_by(desc(ScrapeRun.started_at)).first())
    return {
        "last_reservation_scrape": last_res.started_at.isoformat() if last_res else None,
        "last_reservation_success": bool(last_res and last_res.success),
        "last_reservation_error": last_res.error_message if last_res else None,
        "last_menu_scrape": last_menu.started_at.isoformat() if last_menu else None,
        "last_menu_success": bool(last_menu and last_menu.success),
        "currently_open_count": session.query(AvailableSlot)
            .filter(AvailableSlot.is_currently_available.is_(True)).count(),
        "users_count": session.query(User).count(),
    }


# ─── Users ────────────────────────────────────────────────────────────────
@router.get("/users")
def list_users(session: Session = Depends(get_session), _=Depends(require_admin)) -> list[UserOut]:
    return [UserOut.from_orm_user(u) for u in session.query(User).order_by(User.id).all()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(payload: UserIn, session: Session = Depends(get_session),
                _=Depends(require_admin)) -> UserOut:
    if session.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="email already exists")
    u = User(
        name=payload.name, email=payload.email, gchat_webhook=payload.gchat_webhook,
        preference=Preference(payload.preference if payload.preference != "any" else "both"),
        daily_reminder=payload.daily_reminder, notify_email=payload.notify_email,
        notify_gchat=payload.notify_gchat, enabled=payload.enabled,
    )
    session.add(u)
    session.flush()
    return UserOut.from_orm_user(u)


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: UserPatch, session: Session = Depends(get_session),
                _=Depends(require_admin)) -> UserOut:
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    data = payload.model_dump(exclude_unset=True)
    if "preference" in data and data["preference"] is not None:
        v = data.pop("preference")
        u.preference = Preference(v if v != "any" else "both")
    for k, v in data.items():
        setattr(u, k, v)
    return UserOut.from_orm_user(u)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, session: Session = Depends(get_session), _=Depends(require_admin)):
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    session.delete(u)


@router.post("/users/{user_id}/regenerate-token")
def regenerate_command_token(user_id: int, session: Session = Depends(get_session),
                              _=Depends(require_admin)) -> UserOut:
    """Issue a fresh command_token. Old STOP/START emails referencing the previous one stop working."""
    from ..models import _new_token
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    u.command_token = _new_token()
    session.flush()
    return UserOut.from_orm_user(u)


@router.post("/users/{user_id}/test-notification")
def test_notification(user_id: int, session: Session = Depends(get_session),
                      _=Depends(require_admin)) -> dict:
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    settings = get_settings()
    today = date.today()
    from urllib.parse import quote
    info = session.query(RestaurantInfo).first()
    addr = info.address if info else None
    maps = (f"https://www.google.com/maps/search/?api=1&query={quote(addr)}"
            if addr else None)
    payload = NotifPayload(
        kind=NotificationKind.OPEN,
        slots=(
            SlotInfo(slot_date=today, service=Service.LUNCH, times=("12:00", "12:15", "12:30")),
            SlotInfo(slot_date=today, service=Service.DINNER, times=("19:30", "20:00")),
        ),
        booking_url=settings.reservation_url,
        dashboard_url=None,  # email never includes the local dashboard link
        unsubscribe_token=u.command_token,
        address=addr,
        maps_url=maps,
    )
    results = {}
    if u.notify_email:
        ok, err = send_email(u.email, payload)
        results["email"] = {"sent": ok, "error": err}
    if u.notify_gchat and u.gchat_webhook:
        gchat_payload = NotifPayload(
            kind=payload.kind, slots=payload.slots, booking_url=payload.booking_url,
            dashboard_url=f"http://localhost:{settings.app_port}/",
            address=addr, maps_url=maps,
        )
        ok, err = send_gchat(u.gchat_webhook, gchat_payload)
        results["gchat"] = {"sent": ok, "error": err}
    return results


@router.get("/restaurant")
def restaurant_info(session: Session = Depends(get_session)) -> dict:
    row = session.query(RestaurantInfo).first()
    if row is None:
        return {"address": None, "maps_url": None, "source_url": None, "last_refreshed_at": None}
    from urllib.parse import quote
    maps = (f"https://www.google.com/maps/search/?api=1&query={quote(row.address)}"
            if row.address else None)
    return {
        "address": row.address,
        "maps_url": maps,
        "source_url": row.source_url,
        "last_refreshed_at": row.last_refreshed_at.isoformat(),
    }


# ─── Force scrape ─────────────────────────────────────────────────────────
@router.post("/scrape/reservations", status_code=202)
def trigger_reservation_scrape(_=Depends(require_admin)) -> dict:
    force_reservation_scrape()
    return {"status": "queued"}


@router.post("/scrape/menus", status_code=202)
def trigger_menu_scrape(_=Depends(require_admin)) -> dict:
    force_menu_scrape()
    return {"status": "queued"}


# ─── Notifications history ────────────────────────────────────────────────
@router.get("/notifications")
def list_notifications(limit: int = 200, session: Session = Depends(get_session)) -> list[dict]:
    rows = (session.query(NotificationSent)
            .order_by(desc(NotificationSent.sent_at)).limit(min(limit, 1000)).all())
    return [
        {
            "id": n.id,
            "user_id": n.user_id,
            "slot_id": n.slot_id,
            "kind": n.kind.value,
            "channel": n.channel,
            "sent_at": n.sent_at.isoformat(),
            "success": n.success,
            "error_message": n.error_message,
        }
        for n in rows
    ]
