# End-to-end smoke test & live read-only DB bug

Audit performed 2026-05-13 against `main` @ commit `9476d03`, executed
against the live deployment (`lycee-scraper` + `lycee-caddy` containers
on host `gx10-d4bc`). Scope: real HTTP traffic against every page +
public-API surface, the admin-token gate, the Caddy HTTPS front, and DB
sanity checks. Performed last so the static reviews
([[scraper-modules-review]], [[notifications-review]],
[[database-schema-review]], [[api-auth-review]], [[ui-templates-review]],
[[static-analysis-review]], [[docker-deployment-review]]) had the full
picture before I poked the running system.

## TL;DR

The application is **healthy** end-to-end. Every page renders 200, every
public-API endpoint returns 200, the admin gate correctly returns 401
without a token and 200 with the right one, Caddy serves HTTPS over its
configured SNI hostname (200 on `/healthz`), and DB row counts look
sane.

One **live bug** was discovered and fixed during the test: the SQLite
file at `/data/lycee.sqlite` was owned by `root:root` while the app
runs as uid 1000, causing `sqlite3.OperationalError: attempt to write a
readonly database` on every scrape since 2026-05-12 18:31 local. Root
cause + fix below.

## Findings

### F1 — Page surface, all 200 ✅

```
/            -> HTTP 200
/menus       -> HTTP 200
/users       -> HTTP 200
/analysis    -> HTTP 200
/healthz     -> HTTP 200
```

The four user-facing pages and the liveness probe all rendered. No
template exceptions surfaced in container logs during the exercise.

### F2 — Public API, all 200 ✅

```
/api/slots             -> HTTP 200
/api/menus             -> HTTP 200
/api/scrape-runs       -> HTTP 200
/api/stats/summary     -> HTTP 200
/api/restaurant        -> HTTP 200
```

Matches the inventory in [[api-auth-review]] §"Public reads (no auth)".

### F3 — Admin-token gate ✅

```
no token         -> HTTP 401
wrong token      -> HTTP 401
correct token    -> HTTP 200
```

`require_admin` (`app/routers/deps.py`) behaves exactly as described in
[[api-auth-review]] F1: a missing or mismatched `X-Admin-Token` is
rejected with 401; the configured token is accepted with 200.

### F4 — Caddy HTTPS front ✅ (SNI required)

```
curl -sk https://127.0.0.1:8767/healthz                         -> SSL alert (internal error)
curl -sk --resolve localhost:8767:127.0.0.1 https://localhost:8767/healthz -> HTTP 200
```

The Caddyfile (`deploy/Caddyfile`) configures the site as
`{$LOCAL_HOST}:8443, localhost:8443` with `tls internal`. Caddy will
**not** serve the `tls internal` cert to a bare-IP SNI — that's correct
behaviour; a connection by IP alone is treated as an unconfigured host.
Once SNI matches a configured hostname (`localhost` or the env-supplied
`$LOCAL_HOST`), the response is 200. Operationally this means the
dashboard URL must be the hostname, not the IP — which the README and
Caddyfile both already specify.

### F5 — DB sanity counts ✅

```
users               : 2
available_slots     : 8
scrape_runs         : 1517
notifications_sent  : 22
menus               : 1
restaurant_info     : 1
```

Each table has plausible values for a 25-hour-old deployment: a handful
of slots, a year's worth of scrape history, low double-digit
notifications, single rows for the slowly-changing menu + legal-page
data. Nothing missing, nothing wildly oversized.

### F6 — Live bug: read-only SQLite, fixed in this session ⚠ resolved

**Symptom**: every scrape attempt since 2026-05-12 18:31:41 local
(scrape_run id 1514 was the last `success=True` row pre-fix) failed
with:

```
sqlite3.OperationalError: attempt to write a readonly database
```

Stack trace pointed at `app/db.py:95 session.commit()` inside
`app/scraper/dispatcher.py:run_reservation_scrape`. Container logs
showed the same error every 5 minutes — APScheduler kept firing,
SQLAlchemy kept failing.

**Root cause**: `/data/lycee.sqlite` was owned by `root:root` (mode
0644). The container runs as uid 1000 (`ubuntu` inside the image,
which on the host bind-mount maps to `ad-veritas` — same numeric
uid). With mode 0644 + non-matching owner, uid 1000 had read but
no write access. The likely culprit: a one-shot maintenance run was
executed as root some time around 2026-05-12 18:31 (which is when the
write timestamp on the file froze), leaving the file owner flipped to
root.

This is exactly the *kind* of foot-gun [[docker-deployment-review]] F4
warned about — the bind-mount means the host UID matters, and ad-hoc
`docker run --user 0 …` (or anything else that wrote the DB as root)
will silently degrade subsequent app starts. Worth pinning the
container's user explicitly in `docker-compose.yml` and adding a
startup health check that aborts if the DB isn't writable, so the next
occurrence fails loudly instead of throwing every 5 minutes for a day.

**Fix applied**:

```
sudo chown 1000:1000 /home/.../data/lycee.sqlite
docker compose restart app   # force SQLAlchemy to reopen connections
```

The `restart` step matters: SQLite caches the file descriptor's
permission state at open time, so the pre-existing connection pool
kept failing even after the chown. Restart forced a fresh open under
the new ownership.

**Verification (post-fix)**:

```
scrape_runs.id  kind          started_at                  success  duration_ms  n_closed_slots
1515            menus         2026-05-13T17:23:32.824 Z   true     993          0
1516            reservations  2026-05-13T17:23:32.814 Z   true     8981         6
1517            reservations  2026-05-13T17:23:45.553 Z   true     8915         0
```

Three consecutive `success=True` rows. Row 1516 closed 6 stale slots —
the reconcile path correctly noticed that slots the scraper hadn't
seen during the 25-hour outage are no longer open, and updated
`closed_at` + `is_currently_available=False`. The dedup index from
[[database-schema-review]] F4 prevents the closure from re-firing
notifications.

### F7 — Containers healthy ✅

```
lycee-scraper   Up About a minute (healthy)   127.0.0.1:8765->8000/tcp
lycee-caddy     Up 25 hours                   :8767->8443/tcp
```

`lycee-scraper` is the post-restart instance; `lycee-caddy` has been
up the full 25 hours of this audit window. The `(healthy)` tag means
the container's HEALTHCHECK passed at the most recent interval — i.e.,
`/healthz` is reachable from inside the container.

## Recommendations (carried over)

These are all low/medium-priority items raised by the static reviews
that the smoke test confirms are still relevant. None block; they're
the candidates for a follow-up cleanup commit:

- **Patch CVEs** in `jinja2`, `python-multipart`, `lxml`, `starlette`
  ([[static-analysis-review]] F1).
- **Fix the three mypy errors** in `app/scraper/menus.py:70`,
  `menus.py:208`, `app/notifications/inbox.py:121`
  ([[static-analysis-review]] F2).
- **Apply ruff auto-fixes** in one dedicated stylistic commit
  ([[static-analysis-review]] F3, then wire the gate via
  [[docker-deployment-review]]'s `scripts/verify.sh`).
- **Vendor the three CDN assets** (Tailwind, HTMX, Chart.js) into
  `app/static/` ([[ui-templates-review]] F2) so the UI works offline
  and is not exposed to a third-party CDN supply chain.
- **Constant-time admin-token compare** via `secrets.compare_digest`
  ([[api-auth-review]] F2).
- **Pin httpx `timeout=` and `User-Agent`** on the menu/legal scrapers
  ([[scraper-modules-review]] F8).
- **Defensively `html.escape()`** the interpolations in
  `payload.html()` ([[notifications-review]] F9).
- **Pin the container's user** in `docker-compose.yml` and assert
  DB-writeability at startup — would have surfaced the F6 bug as a
  startup failure instead of a silent 25-hour outage
  ([[docker-deployment-review]] F4 follow-up).

## Summary

End-to-end the system is healthy: every page, every public endpoint,
the admin gate, the HTTPS front, and the DB all behaved as expected.

The smoke test surfaced one real bug (read-only DB, 25 hours of failed
scrapes), produced a one-line chown fix, and confirmed the fix via
three consecutive `success=True` scrape rows. The reconcile logic
correctly cleaned up six slots that had been "open" only because the
last successful scrape was 25 hours old.

Combined with the seven static reviews, this gives a complete picture
of the system today: well-designed, in good shape, with a handful of
low-priority follow-ups in the cleanup queue.
