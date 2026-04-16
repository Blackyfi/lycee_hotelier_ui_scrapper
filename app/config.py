from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_port: int = 8000
    admin_token: str = "change-me-please"
    log_level: str = "INFO"
    tz: str = "Europe/Paris"

    data_dir: Path = Path("/data")

    scrape_interval_minutes: int = 5
    lookahead_months: int = 6
    skip_day_detail: bool = False
    playwright_timeout_ms: int = 20_000

    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587
    gmail_username: str = ""
    gmail_app_password: str = ""
    gmail_from_name: str = "Lycée Hôtelier Watcher"

    daily_reminder_hour: int = 8

    notifications_dry_run: bool = False

    # ─── IMAP-controlled unsubscribe ─────────────────────────────────────
    # Polls the GMAIL_USERNAME inbox for unread emails containing
    # "STOP <token>" or "START <token>" and applies the action.
    imap_enabled: bool = True
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_folder: str = "INBOX"
    imap_poll_interval_minutes: int = 2

    # Bootstrap: if set on first run (no users in DB), create a starter user.
    # Notifications are off by default for safety — flip them in /users once you've tested.
    bootstrap_user_name: str = ""
    bootstrap_user_email: str = ""
    bootstrap_user_gchat_webhook: str = ""
    bootstrap_user_preference: str = "both"

    menus_index_url: str = "https://www.hoteloccitanietoulouse.com/agenda/menus/"
    reservation_url: str = (
        "https://www.covermanager.com/reserve/module_restaurant/"
        "restaurant-restaurant-d-application-d-occitanie/french"
    )
    site_root: str = "https://www.hoteloccitanietoulouse.com"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lycee.sqlite"

    @property
    def menu_images_dir(self) -> Path:
        return self.data_dir / "menu_images"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.menu_images_dir.mkdir(parents=True, exist_ok=True)
    return s
