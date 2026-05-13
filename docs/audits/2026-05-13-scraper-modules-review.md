# Scraper modules review (reservations, menus, restaurant_info)

Audit performed 2026-05-13 against `main` @ commit `c69e99a`. Scope:
`app/scraper/{runner,reservations,menus,restaurant_info,dispatcher}.py`.

## Architecture

| Module | Surface | Tooling |
| --- | --- | --- |
| `reservations.py` | covermanager Angular calendar | Playwright (Chromium, sync API) |
| `menus.py` | weekly menu listing + JPEG | `httpx` + BeautifulSoup |
| `restaurant_info.py` | static legal-mentions page | `httpx` + BeautifulSoup |
| `runner.py` | APScheduler triggers, reentrancy locks | `apscheduler.BackgroundScheduler` |
| `dispatcher.py` | reconcile scraped slots vs DB, fan out notifications | SQLAlchemy + notifications layer |

Right tool per surface: only the Angular reservation grid actually
needs a real browser; the menu list and legal page are server-rendered
HTML and stay cheap.

## Findings

### F1 — Three-layer reentrancy guard ✅

A scrape cannot run concurrently with itself, courtesy of:

1. **APScheduler `max_instances=1` + `coalesce=True`** on every job
   (`runner.py:80–99`) — the scheduler won't fire a second instance
   even if the previous is still running.
2. **Thread `Lock` per job** (`_res_lock`, `_menu_lock`) acquired
   non-blockingly inside the safe-wrapper; logs a warning and returns
   on lock contention.
3. **`misfire_grace_time=60`** on the interval job — if the scheduler
   is delayed more than 60s (process pause, host load), the missed
   tick is **dropped** rather than stacked.

Two of these would have been enough; three is paranoid-defensive in
a good way for a scheduler whose ticks land every few minutes.

### F2 — Retry policy on reservations ✅

`scrape_reservations()` retries the full Playwright session up to **3
times** with backoff `5s, 10s, 15s` between attempts. Final failure
re-raises the last exception, which the dispatcher catches and
records in `ScrapeRun(success=False, error_message=...)`.

For a flaky upstream + a 5-min cadence this is the right shape:
fast-failing on a single transient error would mark every minor blip
as a scrape failure; infinite retries would hold the lock forever.

### F3 — Timezone handling is consistent ✅

- `BackgroundScheduler(timezone=settings.tz)` — top-level tz set once.
- `CronTrigger(..., timezone=settings.tz)` on the daily menu/reminder
  job.
- `_local_today()` in `dispatcher.py` matches the `Date` columns'
  semantics (which represent the *local* day for dedup).
- `datetime.now(UTC)` everywhere a `DateTime(timezone=True)` column is
  written.

No drift between the scheduler's idea of "today" and the database's.

### F4 — Reconcile-then-dispatch shape ✅

`dispatcher.py:147–193` (run_reservation_scrape):

1. Pull `currently_open` slots from DB.
2. Mark any that scrape no longer reports as `is_currently_available =
   False, closed_at = now`.
3. For each scraped slot: insert if new, refresh `last_seen_at` if
   existing, flip `was_closed → open` if reopened.
4. Hand off to dispatcher for fan-out.

Two properties this gets for free:

- The dedup index `ix_notif_user_slot_kind_day` (see
  [[database-schema-review]] F4) is consulted *after* slot state has
  settled — so a reopen won't double-notify same-day.
- `ScrapeRun.n_new_slots` / `n_closed_slots` / `duration_ms` end up
  on the row, surfaced on the dashboard.

### F5 — Date parsing is heuristic but bounded ⚠ low

`menus._DATE_RANGE_RE` matches French phrases like
`"du 13 au 17 avril 2026"`. `_FR_MONTHS` covers the 12 months with
accented + unaccented variants ("août"/"aout"). If the upstream HTML
ever drops the date range from menu titles, `_parse_date_range`
returns `(None, None)` and the menu still persists — just without
`week_start`/`week_end`. Graceful.

`restaurant_info._ADDR_RE` parses French postal addresses with the
shape `"<street>, <5-digit>, <city>"`. Fails silently to `None` on
non-matching text; the dashboard renders that as "address unknown".

Both regexes will probably need a tweak if the upstream site is ever
redesigned. Not a defect today.

### F6 — Mypy errors live here ⚠

Two of the three mypy errors flagged in
[[static-analysis-review]] F2 are in `menus.py`:

- `menus.py:70` — branch falls off without `return`, producing
  implicit `None` where the caller expects a value.
- `menus.py:208` — `int` variable reassigned to `str`.

Both are real, both are local fixes. The third mypy error in
`notifications/inbox.py:121` is unrelated to scraping.

### F7 — Playwright runs in `sync_api` ✅ pragmatic

`reservations.py` uses `playwright.sync_api`. The runner already
isolates the scraper in its own thread (via APScheduler), so the sync
API + reentrancy locks is the simpler model — `async_api` would
require either an event loop just for the scraper or a manual
`asyncio.run(...)` per attempt.

### F8 — Httpx requests don't set explicit User-Agent or timeout ⚠ low

`menus.py` and `restaurant_info.py` use bare `httpx.get(url)` /
`httpx.Client()` without specifying `timeout=` or `headers=`. Two
implications:

- **Timeout**: httpx defaults to 5s connect + 5s read. Probably fine
  for the targets; worth pinning explicitly to `timeout=15.0` (matching
  the GChat dispatch convention) for consistency.
- **User-Agent**: defaults to `python-httpx/<version>`. Some hosts
  block obvious bot UAs. The target appears not to, but if scrapes
  ever start 403-ing, adding a polite UA + contact email is the first
  knob.

### F9 — Playwright `wait_for_selector(state="attached")` ✅

`reservations.py:_wait_for_calendar` waits for the calendar to be
*attached* to the DOM (not necessarily visible). Right choice for the
covermanager Angular UI which can render the calendar off-screen
during initial bind, then animate it in. The follow-up
`wait_for_timeout(400)` gives Angular a moment to wire up event
handlers.

### F10 — Calendar pagination via "next-month" click ✅

`_click_next_month` tries three selectors in order — covers both
jQuery-UI datepicker (`ui-datepicker-next`) and Angular variants
(`[ng-click*='next']`). Returns `False` on no-op (probably the month
arrow is disabled = end of bookable horizon), and the caller stops
iterating. Robust against minor upstream changes.

## Summary

The scrapers are well-built: right tool per surface, three independent
guards against concurrent runs, retry-with-backoff on the flaky
Playwright path, timezone correctness throughout, reconcile-before-
dispatch so notification dedup doesn't fight slot reopens.

Two concrete follow-ups, neither blocking:

- **F6**: fix the two mypy errors in `menus.py` (and the third in
  `inbox.py`).
- **F8**: pin `httpx` timeout and User-Agent on the menu/legal scrapers
  for consistency with the rest of the codebase.

See [[database-schema-review]], [[notifications-review]], and
[[static-analysis-review]] for the storage, fan-out, and lint
findings that overlap with this surface.
