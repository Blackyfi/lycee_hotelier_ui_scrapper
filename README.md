# Lycée Hôtelier Watcher

A self-hosted scraper + dashboard + notifier for the **Restaurant d'Application
du Lycée Hôtelier d'Occitanie** (Toulouse).

The restaurant opens reservations sporadically, often the same day with only a
~2-hour booking window. Their site is slow, ugly, and requires several clicks
to even reach the calendar. This tool watches the calendar every few minutes,
keeps a local mirror of available slots and weekly menus, and notifies you the
moment something opens.

> Personal/educational tool. Be respectful of the upstream site — keep the poll
> interval reasonable (default: every 5 minutes).

---

## Features

- **Reservation watcher** — every 5 min, a headless Chromium scrapes the
  Angular-based covermanager calendar across the next 6 months, identifies
  bookable days (yellow), and drills into each one to capture
  *Déjeuner* / *Dîner* and the available time slots.
- **Notifications** — email (Gmail SMTP via App Password) and/or Google Chat
  (incoming webhook) the moment a slot transitions from gray → yellow.
  Per-user preferences for *lunch only / dinner only / both*. Optional daily
  reminder while a slot remains available.
- **Menu mirror** — scrapes `/agenda/menus/`, downloads each week's menu image
  locally, and surfaces it on the dashboard.
- **Local web UI**
  - `/` — dashboard: open slots + last-check timestamp + this week's menu.
  - `/menus` — gallery of all known weekly menus.
  - `/users` — manage notification subscribers.
  - `/analysis` — history of scrapes, durations, openings over time (Chart.js).
- **Multi-user** — each subscriber has their own email, webhook, preference,
  and reminder setting.
- **Docker-first** — single `docker compose up` deploys everything with a
  persistent volume for the SQLite DB and cached menu images.

---

## Quick start

A pre-filled `.env` is included so you can boot without any edits:

```bash
docker compose up --build -d
```

Open <http://localhost:8000>.

The first reservation scrape runs in the background a few seconds after
startup; the daily menu scrape kicks off at the same time, then runs each
morning at the hour configured by `DAILY_REMINDER_HOUR`.

To watch logs:

```bash
docker compose logs -f
```

### Default credentials

The shipped `.env` includes:

- **`ADMIN_TOKEN`** — pre-generated random string. Find it in the `.env`
  file at the repo root. Use it as the `X-Admin-Token` header for any
  write operation. The `/users` page has a small input that stores it
  locally so you don't have to retype it.
- **Bootstrap notification user** — the address in `BOOTSTRAP_USER_EMAIL`
  is auto-inserted in the DB on first boot (only if the users table is
  empty). It's enabled with email notifications on, but `NOTIFICATIONS_DRY_RUN`
  defaults to `true`, so nothing is actually sent. Use *Tester* on the
  `/users` page to confirm the dispatch path works.

When you're ready to send for real:

1. Open `.env`.
2. Set `GMAIL_USERNAME=<your-gmail-address>` and
   `GMAIL_APP_PASSWORD=<the-16-char-app-password>` (see
   *Setting up Gmail* below).
3. Flip `NOTIFICATIONS_DRY_RUN=false`.
4. `docker compose restart` (or `docker compose up -d`).

---

## Environment variables

All settings live in `.env`. See [`.env.example`](./.env.example) for the
canonical list.

| Variable                  | Default                  | Purpose |
|---------------------------|--------------------------|---------|
| `APP_PORT`                | `8000`                   | Host port mapped to the container. |
| `ADMIN_TOKEN`             | `change-me-please`       | Shared secret for write APIs (user CRUD, force-scrape, test notif). |
| `SCRAPE_INTERVAL_MINUTES` | `5`                      | Reservation poll interval. |
| `LOOKAHEAD_MONTHS`        | `6`                      | Months ahead of "today" the calendar is paged through. |
| `SKIP_DAY_DETAIL`         | `false`                  | If `true`, only record that a day is yellow — don't drill into time slots (faster). |
| `PLAYWRIGHT_TIMEOUT_MS`   | `20000`                  | Per-page navigation timeout. |
| `GMAIL_SMTP_HOST`         | `smtp.gmail.com`         | Usually leave as-is. |
| `GMAIL_SMTP_PORT`         | `587`                    | STARTTLS port. |
| `GMAIL_USERNAME`          | —                        | Your Gmail address. |
| `GMAIL_APP_PASSWORD`      | —                        | **App password**, not your Google account password. See below. |
| `GMAIL_FROM_NAME`         | `Lycée Hôtelier Watcher` | Display name on outgoing emails. |
| `DAILY_REMINDER_HOUR`     | `8`                      | Local hour (0–23) for the daily menu refresh + reminder sweep. |
| `TZ`                      | `Europe/Paris`           | Container timezone, also used by the scheduler. |
| `LOG_LEVEL`               | `INFO`                   | `DEBUG` for verbose troubleshooting. |
| `NOTIFICATIONS_DRY_RUN`   | `false`                  | If `true`, no email/chat is actually sent — just logged. Useful for first runs. |
| `IMAP_ENABLED`            | `true`                   | Poll the Gmail inbox for `STOP <token>` / `START <token>` unsubscribe commands. |
| `IMAP_HOST` / `IMAP_PORT` | `imap.gmail.com` / `993` | IMAP4 over SSL endpoint. |
| `IMAP_FOLDER`             | `INBOX`                  | Mailbox scanned on each tick. |
| `IMAP_POLL_INTERVAL_MINUTES` | `2`                   | Poll cadence for unread mail. |

---

## Setting up Gmail (App Password)

1. Make sure 2-Step Verification is enabled on your Google Account.
2. Go to <https://myaccount.google.com/apppasswords>.
3. Create an app password named e.g. *"Lycée Hôtelier Watcher"*.
4. Copy the 16-character value (no spaces) into `GMAIL_APP_PASSWORD`.
5. Set `GMAIL_USERNAME` to the matching Gmail address.

> Don't reuse your real Google password — app passwords are revocable and
> scoped.

## Setting up Google Chat (Incoming webhook)

Per-user, set in the **Users** page of the UI:

1. In Google Chat, open a Space (create one if needed).
2. *Manage webhooks* → *Add webhook* → name it, save.
3. Copy the webhook URL.
4. In the dashboard, go to `/users`, add the user with that URL in the
   "Google Chat webhook URL" field.

---

## Adding subscribers

1. Open `/users`.
2. Paste your `ADMIN_TOKEN` and click *Sauvegarder* (it's stored in
   `localStorage` so you don't retype it).
3. Add each subscriber with their email, optional GChat webhook, and
   preference (*Déjeuner* / *Dîner* / both).
4. Use *Tester* on a row to send a fake notification and verify the channels
   work end-to-end.

---

## Unsubscribe by email (IMAP STOP / START)

The server doesn't need to be reachable from the public Internet. Each user
has a unique **command token** (visible in `/users`), and the server polls the
`GMAIL_USERNAME` inbox at `IMAP_POLL_INTERVAL_MINUTES` looking for unread
messages whose **Subject** contains:

```
STOP <token>      → disables the user (no more notifications)
START <token>     → re-enables the user
```

**Important UX detail — don't use "Reply":** pressing *Reply* auto-prefixes the
subject with `Re:` (no command keyword) *and* quotes the original notification,
whose own footer already contains a `STOP <token>` line. So subscribers must
either **compose a new email** to `GMAIL_USERNAME` with the command as subject,
or **forward** an existing notification and overwrite the subject. The server
deliberately scans only the Subject header to avoid acting on quoted footers.

After applying the command, the server sends a 1-line confirmation email and
marks the message read.

- Token lookup is scoped to the `command_token` column — a user can send the
  command from *any* email account.
- Rotate a compromised token from `/users` (the ↻ button next to each token);
  old STOP/START emails referencing the previous token stop working.
- If you don't want this feature, set `IMAP_ENABLED=false`.

---

## Architecture

```
┌────────────────────────────────────────────────────┐
│ docker compose                                     │
│                                                    │
│  ┌─────────────────────────────────────────────┐   │
│  │ FastAPI (uvicorn)                           │   │
│  │   ├─ /                  (Jinja2 templates)  │   │
│  │   ├─ /menus, /users, /analysis              │   │
│  │   └─ /api/*             (JSON)              │   │
│  │                                             │   │
│  │ APScheduler (BackgroundScheduler)           │   │
│  │   ├─ every 5 min  → reservations scrape     │   │
│  │   └─ daily 08:05  → menu scrape             │   │
│  │                                             │   │
│  │ Scrapers                                    │   │
│  │   ├─ menus    : httpx + BeautifulSoup       │   │
│  │   └─ reservs  : Playwright (Chromium)       │   │
│  │                                             │   │
│  │ Notifications                               │   │
│  │   ├─ Gmail SMTP                             │   │
│  │   └─ Google Chat webhook                    │   │
│  │                                             │   │
│  │ SQLite (SQLAlchemy 2.x) — /data/lycee.sqlite│   │
│  └─────────────────────────────────────────────┘   │
│                                                    │
│  Volume: ./data → /data (DB + cached menu PNGs)    │
└────────────────────────────────────────────────────┘
```

Stack: **FastAPI** · **SQLAlchemy 2** · **APScheduler** · **Playwright** ·
**httpx** · **BeautifulSoup** · **Jinja2** · **Tailwind CDN** · **Chart.js**.

The base image is `mcr.microsoft.com/playwright/python` so all the system
dependencies for headless Chromium are pre-installed.

---

## Notification logic

After every reservation scrape, the dispatcher diffs against the DB:

| Transition                          | What happens                                     |
|-------------------------------------|--------------------------------------------------|
| New `(date, service)` becomes yellow | "Open" notification to every matching enabled user. |
| Was yellow, now gone                 | Slot marked closed. No notification.             |
| Still yellow, was already known      | Once per day, a "reminder" notification per matching user with `daily_reminder=true`. |

Per-user filtering by **lunch / dinner / both** is applied before sending.
Notifications are recorded in the `notifications_sent` table so reminders
don't fire twice the same day.

---

## API reference (short)

All `POST/PATCH/DELETE` endpoints require `X-Admin-Token: <ADMIN_TOKEN>`.

| Method | Path                                  | Purpose |
|-------:|---------------------------------------|---------|
| GET    | `/api/slots`                          | Currently available slots (or all recent). |
| GET    | `/api/menus`                          | Known weekly menus. |
| GET    | `/api/scrape-runs`                    | History of scrape runs. |
| GET    | `/api/stats/summary`                  | Last-check / counts summary. |
| GET    | `/api/notifications`                  | Notification audit log. |
| GET    | `/api/users`                          | List users *(admin)*. |
| POST   | `/api/users`                          | Create user *(admin)*. |
| PATCH  | `/api/users/{id}`                     | Update user *(admin)*. |
| DELETE | `/api/users/{id}`                     | Delete user *(admin)*. |
| POST   | `/api/users/{id}/test-notification`   | Send a fake "open" notification *(admin)*. |
| POST   | `/api/scrape/reservations`            | Trigger a one-shot reservation scrape *(admin)*. |
| POST   | `/api/scrape/menus`                   | Trigger a one-shot menu scrape *(admin)*. |
| GET    | `/healthz`                            | Liveness probe. |

---

## Troubleshooting

- **Calendar scrape returns zero days** — covermanager occasionally rate-limits
  or pushes a captcha. Check container logs (`docker compose logs -f`) for
  Playwright timeouts. Lower the poll frequency or set `LOG_LEVEL=DEBUG` to
  see what selectors are missing.
- **No times captured even though days are yellow** — covermanager renders
  service blocks (Déjeuner/Dîner) lazily. Increase `PLAYWRIGHT_TIMEOUT_MS` or
  set `SKIP_DAY_DETAIL=true` if you only need to know "a slot opened".
- **Gmail rejects the password** — confirm App Passwords are enabled on the
  account (requires 2FA), and that you pasted the 16-char value with no
  spaces.
- **GChat webhook 403** — re-create the webhook in the Space's "Manage
  webhooks" panel; old URLs can be revoked.
- **Tailwind warning in console** — we use the Tailwind Play CDN for zero-
  build simplicity. It's fine for personal use.

---

## License

GNU GPL v3 — see [`LICENSE`](./LICENSE). Third-party attributions in
[`NOTICE`](./NOTICE) and [`DEPENDENCIES.md`](./DEPENDENCIES.md).
