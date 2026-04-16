"""APScheduler integration."""
from __future__ import annotations

import logging
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import get_settings
from ..notifications.inbox import poll_inbox
from .dispatcher import run_reservation_scrape
from .menus import scrape_menus

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_res_lock = Lock()
_menu_lock = Lock()


def _safe_inbox_job() -> None:
    try:
        result = poll_inbox()
        if result.get("scanned") or result.get("matched") or result.get("errors"):
            log.info("imap poll: %s", result)
    except Exception:
        log.exception("imap poll job crashed")


def _safe_reservation_job() -> None:
    if not _res_lock.acquire(blocking=False):
        log.warning("reservation scrape already running, skipping this tick")
        return
    try:
        result = run_reservation_scrape()
        log.info("reservation scrape: %s", result)
    finally:
        _res_lock.release()


def _safe_menu_job() -> None:
    if not _menu_lock.acquire(blocking=False):
        log.warning("menu scrape already running, skipping")
        return
    try:
        result = scrape_menus()
        log.info("menu scrape: %s", result)
    finally:
        _menu_lock.release()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    s = get_settings()
    sched = BackgroundScheduler(timezone=s.tz)

    sched.add_job(
        _safe_reservation_job,
        IntervalTrigger(minutes=s.scrape_interval_minutes),
        id="reservation_scrape",
        next_run_time=None,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    sched.add_job(
        _safe_menu_job,
        CronTrigger(hour=s.daily_reminder_hour, minute=5, timezone=s.tz),
        id="menu_scrape_daily",
        max_instances=1,
        coalesce=True,
    )
    if s.imap_enabled:
        sched.add_job(
            _safe_inbox_job,
            IntervalTrigger(minutes=s.imap_poll_interval_minutes),
            id="imap_poll",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        sched.add_job(_safe_inbox_job, id="imap_initial", max_instances=1)

    # First-run kicks (in background) so the dashboard isn't empty after fresh boot.
    sched.add_job(_safe_reservation_job, id="reservation_initial", max_instances=1)
    sched.add_job(_safe_menu_job, id="menu_initial", max_instances=1)

    sched.start()
    _scheduler = sched
    log.info("scheduler started (poll=%s min, lookahead=%s months, imap=%s)",
             s.scrape_interval_minutes, s.lookahead_months,
             f"every {s.imap_poll_interval_minutes} min" if s.imap_enabled else "disabled")
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def force_reservation_scrape() -> None:
    """Trigger a one-shot reservation scrape outside the regular interval."""
    if _scheduler:
        _scheduler.add_job(_safe_reservation_job, id=f"force_res_{__import__('time').time_ns()}")
    else:
        _safe_reservation_job()


def force_menu_scrape() -> None:
    if _scheduler:
        _scheduler.add_job(_safe_menu_job, id=f"force_menu_{__import__('time').time_ns()}")
    else:
        _safe_menu_job()
