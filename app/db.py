from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

import sqlcipher3.dbapi2 as sqlcipher
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()


def _load_db_key() -> str:
    key = _settings.db_key.strip()
    if not key and _settings.db_key_file.is_file():
        key = _settings.db_key_file.read_text().strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
        raise RuntimeError(
            "database encryption key missing or malformed: provide 64 hex chars "
            f"in {_settings.db_key_file} (DB_KEY_FILE) or DB_KEY"
        )
    return key


_DB_KEY = _load_db_key()


def _connect() -> sqlcipher.Connection:
    # Key the connection before SQLAlchemy touches it; the probe query fails fast on a wrong key.
    conn = sqlcipher.connect(str(_settings.db_path), check_same_thread=False, timeout=30)
    conn.execute(f"PRAGMA key = \"x'{_DB_KEY}'\"")
    conn.execute("SELECT count(*) FROM sqlite_master")
    return conn


_engine = create_engine(
    f"sqlite:///{_settings.db_path}",
    module=sqlcipher,
    creator=_connect,
    future=True,
)

SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  ensure models are registered

    models.Base.metadata.create_all(_engine)
    _bootstrap_app_config()
    _backfill_command_tokens()
    _bootstrap_default_user()


def _backfill_command_tokens() -> None:
    """For pre-existing rows from older schemas, fill missing command_token values."""
    from . import models

    with session_scope() as s:
        for u in (
            s.query(models.User)
            .filter(
                (models.User.command_token == None) | (models.User.command_token == "")  # noqa: E711
            )
            .all()
        ):
            u.command_token = models._new_token()


def _bootstrap_app_config() -> None:
    from . import models

    with session_scope() as s:
        if s.get(models.AppConfig, 1) is None:
            s.add(
                models.AppConfig(
                    id=1,
                    scrape_interval_minutes=_settings.scrape_interval_minutes,
                )
            )


def _bootstrap_default_user() -> None:
    """Seed a starter notification subscriber if the table is empty and env vars are set.
    Notifications are disabled by default for safety; toggle them in the UI after testing."""
    from . import models

    settings = _settings
    if not settings.bootstrap_user_email:
        return
    with session_scope() as s:
        if s.query(models.User).count() > 0:
            return
        pref_raw = (settings.bootstrap_user_preference or "both").lower()
        try:
            pref = models.Preference(pref_raw if pref_raw != "any" else "both")
        except ValueError:
            pref = models.Preference.BOTH
        # email is on by default — safe because NOTIFICATIONS_DRY_RUN defaults to true
        # in the generated .env, so nothing is actually sent until you flip it.
        s.add(
            models.User(
                name=settings.bootstrap_user_name or "Admin",
                email=settings.bootstrap_user_email,
                gchat_webhook=settings.bootstrap_user_gchat_webhook or None,
                preference=pref,
                notify_email=True,
                notify_gchat=bool(settings.bootstrap_user_gchat_webhook),
                enabled=True,
            )
        )


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    with session_scope() as s:
        yield s


def get_engine() -> Engine:
    return _engine
