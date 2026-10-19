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

> See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for diagrams of the schema, module
> wiring, slot lifecycle, and one-tick runtime flow.

---

## Features

- **Reservation watcher** — every 5 min, a headless Chromium scrapes the
  Angular-based covermanager calendar across the next 6 months, identifies
  bookable days (yellow), and drills into each one to capture
  *Déjeuner* / *Dîner* and the available time slots.
- **Notifications** — email (Gmail SMTP via App Password) and/or Google Chat
  (incoming webhook) the moment a slot transitions from gray → yellow.
  Per-user preferences for *lunch only / dinner only / both*.
- **Welcome calendar** — a new subscriber (or one re-enabled with `START`)
  immediately gets every upcoming service matching their preference: open
  ones highlighted, fully booked ones shown red and dashed, plus a random GIF
  from `app/assets/gifs/` in the email. Nothing is sent when nothing is bookable.
- **Menus** — reads the menu posts at `/agenda/do/tag/menus/`. Each post links a
  PDF and/or an Adobe Acrobat share link (opened in headless Chromium to get the
  PDF). Every PDF is parsed into per-day lunch/dinner dishes (`menu_days` table),
  which are shown on the dashboard and added under each service in alerts, with
  a link to the full menu. Older posts with a weekly image still work.
- **Local web UI**
  - `/` — dashboard: open slots + last-check timestamp + this week's menu.
  - `/menus` — gallery of all known weekly menus.
  - `/users` — manage notification subscribers.
  - `/analysis` — history of scrapes, durations, openings over time (Chart.js).
- **Multi-user** — each subscriber has their own email, webhook, and
  preference.
- **Docker-first** — single `docker compose up` deploys everything with a
  persistent volume for the SQLite DB and cached menu images.

---

## Quick start

A pre-filled `.env` is included. The database is encrypted with SQLCipher, so
create its key once before the first boot (the app refuses to start without it):

```bash
(umask 077; mkdir -p secrets; python3 -c "import secrets;print(secrets.token_hex(32))" > secrets/db_key)
docker compose up --build -d
```

### Database encryption

- `data/lycee.sqlite` (and its backups) are SQLCipher 4 files (AES-256). Without
  `secrets/db_key` they are unreadable random bytes.
- The key is mounted into the container as the Docker secret
  `/run/secrets/db_key`. It lives outside `data/` on purpose: a copy of the data
  directory alone is useless.
- **Back up `secrets/db_key` somewhere safe (password manager).** Lose it and the
  database is gone for good.
- Inspect or migrate the DB with the app image, e.g.
  `docker compose exec app python -c "from app.db import session_scope; ..."`.
  The plain `sqlite3` CLI cannot open it.
- `scripts/encrypt_db.py <plain> <encrypted> <key_file>` converts a plaintext
  SQLite file (verified copy, source untouched).

Open <http://localhost:8000>.

If your browser or another local proxy upgrades this host to HTTPS, configure
`TLS_CERT_FILE` + `TLS_KEY_FILE` and open `https://<your-host>:<APP_PORT>`
instead. When reusing the manga app's `gx10-d4bc` certificate, use the
`gx10-d4bc` hostname, not `localhost`, because certificate validation is tied
to the hostname.

The first reservation scrape runs in the background a few seconds after
startup; the daily menu scrape kicks off at the same time, then runs each
morning at the hour configured by `DAILY_MENU_SCRAPE_HOUR`.

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
| `PUBLIC_BASE_URL`         | —                        | Optional externally reachable base URL used in Google Chat dashboard links, e.g. `https://gx10-d4bc:8765`. |
| `ADMIN_TOKEN`             | `change-me-please`       | Shared secret for write APIs (user CRUD, force-scrape, test notif). |
| `TLS_MOUNT_SOURCE`        | `./tls`                  | Host directory mounted read-only into the container at `/tls`. |
| `TLS_CERT_FILE`           | —                        | Optional certificate file path inside the container. Enables HTTPS when paired with `TLS_KEY_FILE`. |
| `TLS_KEY_FILE`            | —                        | Optional private key path inside the container. Enables HTTPS when paired with `TLS_CERT_FILE`. |
| `DATA_DIR`                | `./data` locally         | Directory holding the SQLite DB and cached menu images. Docker sets this to `/data`. |
| `DB_KEY_FILE`             | `/run/secrets/db_key`    | File holding the SQLCipher key (64 hex chars). Docker mounts `secrets/db_key` there. |
| `DB_KEY`                  | —                        | The key itself, instead of a file (handy outside Docker). |
| `SCRAPE_INTERVAL_MINUTES` | `5`                      | Reservation poll interval. |
| `LOOKAHEAD_MONTHS`        | `6`                      | Months ahead of "today" the calendar is paged through. |
| `SKIP_DAY_DETAIL`         | `false`                  | If `true`, only record that a day is yellow — don't drill into time slots (faster). |
| `PLAYWRIGHT_TIMEOUT_MS`   | `20000`                  | Per-page navigation timeout. |
| `GMAIL_SMTP_HOST`         | `smtp.gmail.com`         | Usually leave as-is. |
| `GMAIL_SMTP_PORT`         | `587`                    | STARTTLS port. |
| `GMAIL_USERNAME`          | —                        | Your Gmail address. |
| `GMAIL_APP_PASSWORD`      | —                        | **App password**, not your Google account password. See below. |
| `GMAIL_FROM_NAME`         | `Lycée Hôtelier Watcher` | Display name on outgoing emails. |
| `DAILY_MENU_SCRAPE_HOUR`  | `8`                      | Local hour (0–23) for the daily menu refresh scrape. |
| `TZ`                      | `Europe/Paris`           | Container timezone, also used by the scheduler. |
| `LOG_LEVEL`               | `INFO`                   | `DEBUG` for verbose troubleshooting. |
| `NOTIFICATIONS_DRY_RUN`   | `false`                  | If `true`, no email/chat is actually sent — just logged. Useful for first runs. |
| `IMAP_ENABLED`            | `true`                   | Poll the Gmail inbox for `STOP <token>` / `START <token>` unsubscribe commands. |
| `IMAP_HOST` / `IMAP_PORT` | `imap.gmail.com` / `993` | IMAP4 over SSL endpoint. |
| `IMAP_FOLDER`             | `INBOX`                  | Mailbox scanned on each tick. |
| `IMAP_POLL_INTERVAL_MINUTES` | `2`                   | Poll cadence for unread mail. |

---

## Reusing an Existing Trusted Cert

If you already trust the manga app's `gx10-d4bc` certificate on your devices,
reuse it here instead of creating another self-signed cert:

```dotenv
APP_PORT=8765
TLS_MOUNT_SOURCE=/var/lib/docker/volumes/manga_manhwa_upscaling_library_caddy_data/_data/caddy/certificates/local/gx10-d4bc
TLS_CERT_FILE=/tls/gx10-d4bc.crt
TLS_KEY_FILE=/tls/gx10-d4bc.key
```

Then restart:

```bash
docker compose up --build -d
```

Open `https://gx10-d4bc:8765`.

If `TLS_CERT_FILE` / `TLS_KEY_FILE` are unset, the app stays on plain HTTP.

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
   preference (*Déjeuner* / *Dîner* / both). Each field has a *?* bubble
   explaining where the value comes from. Saving an enabled user sends them
   the welcome calendar right away.
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
START <token>     → re-enables the user (and re-sends the welcome calendar)
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
| Still yellow, was already known      | Nothing — the open notification already fired.   |

Per-user filtering by **lunch / dinner / both** is applied before sending.
Notifications are recorded in the `notifications_sent` table.

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
