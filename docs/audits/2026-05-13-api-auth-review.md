# API routes, auth middleware & dependency injection review

Audit performed 2026-05-13 against `main` @ commit `56066cf`. Scope:
`app/routers/api.py`, `app/routers/pages.py`, `app/routers/deps.py`, and
how `app/main.py` wires them.

## Route inventory

### Public reads (no auth)

| Method | Path | Reads |
| --- | --- | --- |
| GET | `/api/slots` | open + recently-closed slots |
| GET | `/api/menus` | scraped menus |
| GET | `/api/scrape-runs` | scrape history |
| GET | `/api/stats/summary` | counts + last-scrape success/timestamps |
| GET | `/api/restaurant` | address + maps URL |
| GET | `/`, `/menus`, `/analysis`, `/menu_image/{f}`, `/healthz` | HTML / static |

### Admin-gated (`X-Admin-Token` header)

| Method | Path | Effect |
| --- | --- | --- |
| GET/PUT | `/api/admin/settings` | read / update scrape interval |
| GET | `/api/users` | list subscribers |
| POST | `/api/users` | create subscriber |
| PATCH | `/api/users/{id}` | update subscriber |
| DELETE | `/api/users/{id}` | delete subscriber |
| POST | `/api/users/{id}/regenerate-token` | mint new command_token |
| POST | `/api/users/{id}/test-notification` | dispatch test mail/gchat |
| GET | `/api/notifications` | sent-notification history |
| POST | `/api/scrape/reservations` | force a scrape (202) |
| POST | `/api/scrape/menus` | force a scrape (202) |

Every mutating endpoint and every PII-adjacent read goes through
`Depends(require_admin)` — verified by grepping each `@router` decorator
+ signature.

## Findings

### F1 — `require_admin` semantics ✅

`deps.py:require_admin` does the right thing on the boring edges:

- if `settings.admin_token` is empty, the dependency raises HTTP 500
  rather than authenticating against the literal `None` header default
  (i.e., never accidentally "everyone's an admin in dev");
- if the header is missing or wrong, HTTP 401 with a clear `detail`.

This is the exact failure mode you want: an unconfigured deployment
refuses operations instead of opening up.

### F2 — Token comparison is not constant-time ⚠ low

```python
if x_admin_token != settings.admin_token:
    raise HTTPException(...)
```

`!=` short-circuits on first byte mismatch, so a remote attacker could
in principle infer the token byte-by-byte from response timing. In
practice:

- The deployment binds to `127.0.0.1:8765` and is reached only through
  Caddy on the LAN.
- LAN latency variance dwarfs the per-byte cost.
- Tokens are operator-set 32+ char secrets, not 4-digit PINs.

So realistic exploitability is essentially zero. Still, swapping to
`secrets.compare_digest(x_admin_token or "", settings.admin_token)` is
two lines and removes the entire class of concern.

### F3 — No rate limiting on `/api/users` mutations ⚠ low

`main.py` mounts no middleware. `require_admin` is the only gate. An
attacker who guessed the admin token could enumerate the user list,
delete subscribers, or queue scrapes without throttling.

In context: the LAN-only deployment + 32+ char token keeps this
theoretical. If the app ever fronts a public IP, plug a rate-limit
middleware (`slowapi`, `fastapi-limiter`, or Caddy's `rate_limit`)
in front of `/api/users*` and `/api/scrape/*`.

### F4 — DI is FastAPI-idiomatic ✅

Three dependencies, all clean:

- `get_settings` — cached `BaseSettings` instance from
  `app.config:get_settings`.
- `get_session` — yields a `Session` via the `session_scope()` context
  manager (`app/db.py:113`). FastAPI's `yield` dep protocol means
  `session.commit()` runs on normal return, `session.rollback()` runs
  on exception — exactly the lifecycle the rest of the code expects.
- `require_admin` — pure check, no resource it has to release.

No god-objects in deps, no implicit globals smuggled through `Request`.
The pattern is uniform across every endpoint.

### F5 — Mutating endpoints inside their own `session_scope` ✅

Because `get_session()` opens and commits its own scope, the
endpoint never needs to wrap calls in another `with session_scope()`.
All `session.add(...)` / `session.flush()` calls land in the
request-scoped session and commit at exit. No mixed scopes, no leaked
sessions.

### F6 — Public reads are appropriately public ✅

`/api/slots`, `/api/menus`, `/api/restaurant`, `/api/stats/summary`,
`/api/scrape-runs`, and the dashboard pages expose operational data
(when did we last scrape, what's currently open, where's the
restaurant). For a single-user homelab watcher served only over the
LAN, this is intentional and matches the README's stated threat model
("private network + admin token"). Flagging only so future operators
know the boundary.

### F7 — `force_*_scrape` is fire-and-forget ⚠ medium-low

```python
@router.post("/scrape/reservations", status_code=202)
def trigger_reservation_scrape(_=Depends(require_admin)) -> dict:
    force_reservation_scrape()
    return {"status": "queued"}
```

`force_reservation_scrape()` is sync but kicks off async work in the
APScheduler. There's no way to tell from the response whether the
queue is wedged. Two failure modes worth knowing about:

- If the scheduler crashed on a prior scrape, `force_*` silently
  succeeds and queues nothing.
- If a scrape is *already* running, current behaviour is "stack a
  second one" — depending on the scraper's reentrancy.

Recommendation (low priority): return the `ScrapeRun.id` that's about
to start (or `None` if the queue is congested) so the dashboard can
poll for completion. Out of scope for this audit but a useful follow-up.

### F8 — `Depends` ordering convention ✅ readable

Most endpoints write deps as:

```python
def foo(payload: …, session: Session = Depends(get_session), _=Depends(require_admin)):
```

…with `payload`/`path-params` first, `session` second, `_=require_admin`
last. Consistent across every admin-gated handler — the eye reaches
the auth gate in the same column on every line, which made the
"is this protected?" grep above trivial.

## Summary

The auth model is small, idiomatic, and correctly applied to every
mutation. The DI graph is three clean dependencies that never escape
the request scope. Public reads are intentionally open and appropriate
for a LAN-only watcher.

The only changes worth making:

- **F2**: swap `!=` for `secrets.compare_digest` (2 LOC, kills a
  theoretical timing-attack class).
- **F3**: if/when this app ever fronts a public IP, add rate limiting
  on `/api/users*` and `/api/scrape/*`.

Nothing here blocks shipping; the system is in good shape.
