from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..config import get_settings
from ..datetime_utils import browser_iso
from ..db import get_session
from ..models import AvailableSlot, Menu, MenuDay, RestaurantInfo, ScrapeRun, Service
from ..notifications.welcome import fmt_long
from ..runtime_settings import get_effective_settings

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.globals["browser_iso"] = browser_iso


def _upcoming_menu(session: Session, n_days: int = 5) -> list[dict]:
    """Next n served days from today with their lunch/dinner dishes, from the parsed menu PDFs."""
    today = datetime.now(ZoneInfo(get_settings().tz)).date()
    by_day: dict[date, dict[str, MenuDay]] = {}
    for m in session.query(MenuDay).filter(MenuDay.menu_date >= today).order_by(MenuDay.menu_date).limit(n_days * 2):
        by_day.setdefault(m.menu_date, {})[m.service.value] = m
    return [
        {
            "label": fmt_long(d).capitalize(),
            "lunch": svc.get(Service.LUNCH.value),
            "dinner": svc.get(Service.DINNER.value),
        }
        for d, svc in list(by_day.items())[:n_days]
    ]


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    settings = get_effective_settings(session)
    open_slots = (
        session.query(AvailableSlot)
        .filter(AvailableSlot.is_currently_available.is_(True))
        .order_by(AvailableSlot.slot_date.asc(), AvailableSlot.service.asc())
        .all()
    )
    last_res = (
        session.query(ScrapeRun).filter(ScrapeRun.kind == "reservations").order_by(desc(ScrapeRun.started_at)).first()
    )
    current_menu = session.query(Menu).order_by(desc(Menu.week_start.is_(None)), desc(Menu.week_start)).first()
    info = session.query(RestaurantInfo).first()
    address = info.address if info else None
    maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(address)}" if address else None
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "settings": settings,
            "open_slots": open_slots,
            "last_res": last_res,
            "current_menu": current_menu,
            "upcoming_menu": _upcoming_menu(session),
            "address": address,
            "maps_url": maps_url,
        },
    )


@router.get("/analysis", response_class=HTMLResponse)
def analysis(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        "analysis.html",
        {
            "request": request,
            "settings": get_effective_settings(session),
        },
    )


@router.get("/menus", response_class=HTMLResponse)
def menus(request: Request, session: Session = Depends(get_session)):
    rows = session.query(Menu).order_by(desc(Menu.week_start.is_(None)), desc(Menu.week_start)).all()
    return templates.TemplateResponse(
        "menus.html",
        {
            "request": request,
            "settings": get_effective_settings(session),
            "menus": rows,
        },
    )


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "settings": get_effective_settings(session),
        },
    )


@router.get("/menu_image/{filename}")
def menu_image(filename: str):
    settings = get_settings()
    safe = (settings.menu_images_dir / filename).resolve()
    if settings.menu_images_dir.resolve() not in safe.parents and safe.parent != settings.menu_images_dir.resolve():
        raise HTTPException(status_code=400, detail="invalid path")
    if not safe.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(safe))


@router.get("/healthz")
def healthz():
    return {"ok": True}
