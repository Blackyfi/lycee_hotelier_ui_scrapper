from __future__ import annotations

from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import session_scope
from .models import AppConfig


def _ensure_app_config(session: Session) -> AppConfig:
    row = session.get(AppConfig, 1)
    if row is None:
        row = AppConfig(id=1, scrape_interval_minutes=get_settings().scrape_interval_minutes)
        session.add(row)
        session.flush()
    return row


def get_scrape_interval_minutes(session: Session | None = None) -> int:
    if session is not None:
        return _ensure_app_config(session).scrape_interval_minutes

    with session_scope() as db:
        return _ensure_app_config(db).scrape_interval_minutes


def set_scrape_interval_minutes(minutes: int, session: Session | None = None) -> int:
    if session is not None:
        row = _ensure_app_config(session)
        row.scrape_interval_minutes = minutes
        get_settings().scrape_interval_minutes = minutes
        return minutes

    with session_scope() as db:
        row = _ensure_app_config(db)
        row.scrape_interval_minutes = minutes

    get_settings().scrape_interval_minutes = minutes
    return minutes


def get_effective_settings(session: Session | None = None) -> Settings:
    effective = get_settings().model_copy(deep=True)
    effective.scrape_interval_minutes = get_scrape_interval_minutes(session)
    return effective
