# Notification system review (Gmail, Google Chat, inbox)

Audit performed 2026-05-13 against `main` @ commit `6f8cca1`. Scope:
`app/notifications/{payload,gmail,gchat,inbox}.py` and the dispatcher in
`app/scraper/dispatcher.py`.

## Surface

| Channel | Outbound | Inbound | Transport |
| --- | --- | --- | --- |
| Gmail | `app/notifications/gmail.py:send_email` | n/a | SMTP+STARTTLS to `gmail_smtp_host:gmail_smtp_port`, login with Gmail App Password |
| Google Chat | `app/notifications/gchat.py:send_gchat` | n/a | `httpx.post(webhook_url, json=...)`, `cardsV2` payload |
| Inbox (control plane) | `app/notifications/inbox.py:_send_confirmation` | `poll_inbox()` | IMAP for STOP/START parsing; SMTP for confirmation |

## Findings

### F1 — Dry-run-default is correct ✅

`settings.notifications_dry_run` defaults true in the generated `.env`.
Both `send_email` and `send_gchat` short-circuit before any network call,
log the would-be payload, and return success. New deployments physically
cannot spam anyone until an operator flips the switch.

### F2 — Per-slot per-day dedup ✅

`dispatcher._already_sent_today` checks `NotificationSent` for
`(user_id, slot_id, kind, sent_day, success=True)` before sending, and
the dispatcher records a row per slot per channel after every send. The
composite index `ix_notif_user_slot_kind_day` matches this filter
prefix-perfectly (see [[database-schema-review]] F4). Even on retry
storms (the scrape loop runs every few minutes), each user gets at most
one OPEN and one REMINDER per slot per day.

### F3 — Failure rows preserved ✅

Failed sends record a `NotificationSent` row with `success=False` and
`error_message` populated. Two consequences:

- The dedup query filters `success=True`, so a failed send doesn't
  block a retry on the next scrape cycle.
- Operators can `SELECT * FROM notifications_sent WHERE success=0` to
  audit transient errors without grepping logs.

### F4 — Inbox subject-only parsing ✅

`inbox._extract_subject` deliberately ignores the message body. The
docstring and module header both explain why: replies quote the original
notification's footer, which contains a literal `STOP <token>` line, so
body-matching would flip every reply into an unsubscribe. Subject-only
parsing forces the user to compose a new subject. Important foot-gun,
correctly avoided.

### F5 — Per-user `command_token` ✅

Tokens are 12-ish characters from `secrets.token_urlsafe(9)`, stored
`unique=True`, regenerated for legacy rows on startup
(`_backfill_command_tokens`). The token is the only thing tying an
inbound STOP to a user, so unique + cryptographically random matters
here.

### F6 — Gmail App Password whitespace tolerance ✅

`"".join(settings.gmail_app_password.split())` accepts Google's "xxxx
xxxx xxxx xxxx" display format alongside the bare 16-char form. Minor
but exactly the kind of paper cut that bites operators on first setup.

### F7 — Network timeouts present ✅

- SMTP: `timeout=20` (Gmail + inbox confirmation)
- httpx GChat: `timeout=15.0`

Both bounded; no chance of a wedged socket hanging the scheduler thread.
The IMAP path isn't inspected here but the threading `Lock()` ensures
only one polling cycle runs at a time.

### F8 — Webhook URL logging hygiene ✅

Dry-run logs truncate `webhook_url[:48] + "..."`. GChat webhook URLs
contain a key in the path that's effectively a bearer token; truncating
keeps it out of full-line logs.

### F9 — HTML escaping in `payload.html()` ⚠ low

`NotifPayload.html()` builds the email body via f-strings and string
concatenation. Several fields are interpolated raw:

- `s.times` (scraped time chips, e.g. `"12:30"`)
- `self.address` (scraped postal address)
- `self.unsubscribe_token` (controlled by us; safe)
- `self.booking_url`, `self.maps_url` (server-known constants; safe)

All current sources are constrained:

- `times` comes from a regex extraction of the scraped reservation grid
  — values look like `"12h00"`, no HTML expected.
- `address` comes from `RestaurantInfo.address`, scraped from the
  restaurant's legal page.

**Today**, none of these are operator- or visitor-controlled, so this is
theoretical. But the moment a future change lets a value flow through
that *isn't* numeric/short-string-shaped, the email body would render
that HTML literally. Two practical defences:

- Wrap interpolations with `html.escape(...)` in `payload.html()`.
- Or assert/validate inputs at the dataclass boundary (e.g. regex on
  `times`, length cap on `address`).

The text/plain alternative path (`payload.text()`) is unaffected.

### F10 — `_send_confirmation` opens a fresh SMTP connection per call ✅

Acceptable: STOP/START is rare (manual user action), one extra TLS
handshake per command is fine. Reusing a long-lived SMTP connection
would force re-auth handling on disconnect and isn't worth the
complexity here.

## Summary

The notification path is unusually well-thought-out for a homelab
project: dry-run default, real per-slot dedup with a matching index, a
deliberate parse-subject-not-body rule that sidesteps the obvious
inbox-loop trap, and tokenized opt-out that works without a public
dashboard.

One soft finding: **F9** — defensively escape HTML interpolations in
`payload.html()` before a future scraper change accidentally lets
markup through. Otherwise the system is in good shape.

See [[database-schema-review]] for the storage-layer notes that this
review depends on.
