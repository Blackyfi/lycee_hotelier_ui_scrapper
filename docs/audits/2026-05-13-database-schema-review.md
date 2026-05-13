# Database, schema & migration safety review

Audit performed 2026-05-13 against `main` @ commit `6f8cca1`. Scope:
`app/db.py`, `app/models.py`, related bootstrap code.

## What's there

- **Engine**: SQLite at `${db_path}` via `create_engine(sqlite:///...,
  connect_args={check_same_thread:False, timeout:30})`.
- **Session**: sync `sessionmaker(autocommit=False, autoflush=False,
  expire_on_commit=False)` wrapped in a `session_scope()` context manager.
- **Schema management**: `models.Base.metadata.create_all(_engine)` — no
  Alembic, no migration history.
- **Bootstrap on `init_db()`**: backfills `User.command_token`,
  ensures `AppConfig(id=1)`, and conditionally seeds a default user from
  env vars.
- **Models**: `User`, `AvailableSlot`, `ScrapeRun`, `NotificationSent`,
  `RestaurantInfo`, `AppConfig`, `Menu`.

## Findings

### F1 — Datetime hygiene ✅

Every timestamp column is `DateTime(timezone=True)` with a default of
`datetime.now(UTC)`. `_local_today()` uses `ZoneInfo(settings.tz)` so a
`Date`-typed column tracks the *local* day (relevant for daily-reminder
dedup). Consistent and correct.

### F2 — Foreign keys + cascade ✅

`NotificationSent.user_id` and `slot_id` both declare
`ForeignKey(..., ondelete="CASCADE")`. Deleting a user purges their
notification history; same for a closed slot. Matches expected lifecycle.

### F3 — Unique constraints ✅

- `users.email` (single subscriber per address)
- `users.command_token` (token must uniquely identify a user)
- `available_slots(slot_date, service)` (one row per service per day)
- `menus.page_url`

All correct. The token uniqueness is critical because IMAP STOP/START
look up by token alone.

### F4 — Composite index matches hot query ✅

`Index("ix_notif_user_slot_kind_day", "user_id", "slot_id", "kind", "sent_day")`
is a perfect prefix match for `_already_sent_today` in
`app/scraper/dispatcher.py:122`, which filters on exactly those four
columns. Query stays index-only.

### F5 — `expire_on_commit=False` ⚠ low-risk

This avoids reloading attributes after every commit (a perf win) but
means objects can carry stale state across explicit commits inside a
single `session_scope` block. Acceptable in this codebase because:

1. `session_scope()` is short-lived per-request,
2. there's no inner-loop `session.commit()` pattern that I could find,
3. the alternative (re-querying after every commit) would hurt the
   scraper's batching.

No remediation needed; flagging so it's a known invariant.

### F6 — Native enum portability ✅

All enums declared with `native_enum=False`, length-bound (`length=10`).
SQLite stores them as strings; Postgres migration later won't require
type rewrites.

### F7 — No Alembic / migrations framework ⚠ medium

`init_db()` calls `Base.metadata.create_all(_engine)`, which only creates
*missing* tables and columns it doesn't already know about — **it never
ALTERs existing tables**. The codebase compensates with hand-written
backfills (`_backfill_command_tokens`, `_bootstrap_app_config`,
`_bootstrap_default_user`), but each new column on an existing table
needs a manual fix-up:

- Drop the SQLite file and re-bootstrap (loses data), OR
- Hand-write the ALTER TABLE in a startup hook (the current pattern)

For a single-user homelab app this is workable. The risk profile rises
sharply once anyone else's data lives in the DB or if non-trivial column
changes pile up.

**Recommendation:** When the next column change lands (or before any
serious data accumulates), introduce Alembic. The model definitions are
clean enough that `alembic init` → `alembic revision --autogenerate` will
produce a sensible baseline migration in one pass.

### F8 — SQLite single-writer ✅ for current use

`timeout=30` accommodates writer contention. APScheduler runs the
scraper in a single thread, the FastAPI app is single-process. No
concurrent-writer fan-out, so SQLite is appropriate.

### F9 — `_bootstrap_default_user` safety ✅

Only seeds a row when the table is empty *and* `bootstrap_user_email`
is set in settings. Notifications still respect the
`NOTIFICATIONS_DRY_RUN` switch (default on per `.env.example`), so the
seeded user can't accidentally page someone before the operator opts in.

## Summary

The schema design itself is clean: appropriate FKs, the dedup index
matches its query exactly, datetime/enum hygiene is correct.

The one real concern is **F7** — no migration framework. Today's
`create_all` + hand-rolled backfills will eventually paint the project
into a corner. Adopt Alembic before the next column change.
