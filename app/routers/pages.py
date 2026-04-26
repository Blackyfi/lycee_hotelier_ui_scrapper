from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from sqlalchemy.orm import Session

from urllib.parse import quote

from ..config import get_settings
from ..db import get_session
from ..models import AvailableSlot, Menu, RestaurantInfo, ScrapeRun

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    settings = get_settings()
    open_slots = (session.query(AvailableSlot)
                  .filter(AvailableSlot.is_currently_available.is_(True))
                  .order_by(AvailableSlot.slot_date.asc(), AvailableSlot.service.asc())
                  .all())
    last_res = (session.query(ScrapeRun).filter(ScrapeRun.kind == "reservations")
                .order_by(desc(ScrapeRun.started_at)).first())
    current_menu = (session.query(Menu)
                    .order_by(desc(Menu.week_start.is_(None)), desc(Menu.week_start)).first())
    info = session.query(RestaurantInfo).first()
    address = info.address if info else None
    maps_url = (f"https://www.google.com/maps/search/?api=1&query={quote(address)}"
                if address else None)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "settings": settings,
        "open_slots": open_slots,
        "last_res": last_res,
        "current_menu": current_menu,
        "address": address,
        "maps_url": maps_url,
    })


@router.get("/analysis", response_class=HTMLResponse)
def analysis(request: Request):
    return templates.TemplateResponse("analysis.html", {
        "request": request,
        "settings": get_settings(),
    })


@router.get("/menus", response_class=HTMLResponse)
def menus(request: Request, session: Session = Depends(get_session)):
    rows = (session.query(Menu)
            .order_by(desc(Menu.week_start.is_(None)), desc(Menu.week_start)).all())
    return templates.TemplateResponse("menus.html", {
        "request": request,
        "settings": get_settings(),
        "menus": rows,
    })


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    return templates.TemplateResponse("users.html", {
        "request": request,
        "settings": get_settings(),
    })


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
