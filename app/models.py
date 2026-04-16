from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Service(str, enum.Enum):
    LUNCH = "lunch"
    DINNER = "dinner"


class Preference(str, enum.Enum):
    LUNCH = "lunch"
    DINNER = "dinner"
    BOTH = "both"
    ANY = "any"  # alias of both — kept for flexibility


class NotificationKind(str, enum.Enum):
    OPEN = "open"
    REMINDER = "reminder"


def _new_token() -> str:
    import secrets
    return secrets.token_urlsafe(9)  # ~12 chars


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    gchat_webhook: Mapped[str | None] = mapped_column(Text, nullable=True)
    preference: Mapped[Preference] = mapped_column(
        Enum(Preference, native_enum=False, length=10), default=Preference.BOTH, nullable=False
    )
    daily_reminder: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_gchat: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    command_token: Mapped[str] = mapped_column(String(32), unique=True, default=_new_token, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AvailableSlot(Base):
    __tablename__ = "available_slots"
    __table_args__ = (
        UniqueConstraint("slot_date", "service", name="uq_slot_date_service"),
        Index("ix_slot_currently_available", "is_currently_available"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slot_date: Mapped[date] = mapped_column(Date, nullable=False)
    service: Mapped[Service] = mapped_column(
        Enum(Service, native_enum=False, length=10), nullable=False
    )
    times_csv: Mapped[str] = mapped_column(Text, default="", nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_currently_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def times(self) -> list[str]:
        return [t for t in self.times_csv.split(",") if t]

    @times.setter
    def times(self, values: list[str]) -> None:
        self.times_csv = ",".join(sorted(set(values)))


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # "reservations" | "menus"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    n_yellow_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_new_slots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_closed_slots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class NotificationSent(Base):
    __tablename__ = "notifications_sent"
    __table_args__ = (
        Index("ix_notif_user_slot_kind_day", "user_id", "slot_id", "kind", "sent_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    slot_id: Mapped[int] = mapped_column(ForeignKey("available_slots.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[NotificationKind] = mapped_column(
        Enum(NotificationKind, native_enum=False, length=10), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # "email" | "gchat"
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    sent_day: Mapped[date] = mapped_column(Date, default=lambda: _utcnow().date(), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship()
    slot: Mapped[AvailableSlot] = relationship()


class RestaurantInfo(Base):
    """Single-row table holding restaurant metadata scraped from the legal page."""
    __tablename__ = "restaurant_info"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Menu(Base):
    __tablename__ = "menus"
    __table_args__ = (UniqueConstraint("page_url", name="uq_menu_page_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    week_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    week_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    page_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
