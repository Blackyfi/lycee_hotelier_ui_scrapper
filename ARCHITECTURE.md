# Architecture

Visual reference for how the watcher is laid out. All diagrams are Mermaid —
GitHub renders them inline.

---

## 1. Database schema (ERD)

Six tables, two foreign keys. SQLite, single file at `/data/lycee.sqlite`.

```mermaid
erDiagram
    USER ||--o{ NOTIFICATION_SENT : "received via"
    AVAILABLE_SLOT ||--o{ NOTIFICATION_SENT : "about"

    USER {
        int id PK
        string name
        string email UK
        string gchat_webhook "nullable"
        enum preference "lunch|dinner|both|any"
        bool daily_reminder
        bool notify_email
        bool notify_gchat
        bool enabled
        string command_token UK "STOP/START token"
        datetime created_at
    }

    AVAILABLE_SLOT {
        int id PK
        date slot_date
        enum service "lunch|dinner"
        string times_csv "comma-joined HH:MM"
        datetime first_seen_at
        datetime last_seen_at
        datetime closed_at "nullable"
        bool is_currently_available
    }

    NOTIFICATION_SENT {
        int id PK
        int user_id FK
        int slot_id FK
        enum kind "open|reminder"
        string channel "email|gchat"
        datetime sent_at
        date sent_day
        bool success
        string error_message "nullable"
    }

    SCRAPE_RUN {
        int id PK
        string kind "reservations|menus"
        datetime started_at
        datetime finished_at "nullable"
        bool success
        string error_message "nullable"
        int n_yellow_days
        int n_new_slots
        int n_closed_slots
        int duration_ms "nullable"
    }

    MENU {
        int id PK
        string title
        date week_start "nullable"
        date week_end "nullable"
        string page_url UK
        string image_url "nullable"
        string image_filename "nullable"
        datetime first_seen_at
        datetime last_seen_at
    }

    RESTAURANT_INFO {
        int id PK "single-row table"
        string address "nullable"
        string source_url "nullable"
        datetime last_refreshed_at
    }
```

**Notes**
- `(slot_date, service)` is unique on `AVAILABLE_SLOT` — one row per
  date×service, lifecycled in place rather than re-inserted.
- `NOTIFICATION_SENT` is the dedup ledger: the daily-reminder job checks
  `(user_id, slot_id, kind=REMINDER, sent_day=today)` before sending.
- `SCRAPE_RUN`, `MENU`, `RESTAURANT_INFO` are standalone — no FKs.

---

## 2. Component & class relations (UML)

How the runtime modules wire together. Solid arrows = "uses", dashed = "reads
config from".

```mermaid
classDiagram
    direction LR

    class FastAPIApp {
        +lifespan()
        +mounts /static
    }
    class APIRouter {
        /api/users
        /api/slots
        /api/scrape/*
    }
    class WebRouter {
        /
        /menus
        /users
        /analysis
    }
    class Settings {
        Pydantic BaseSettings
        loads .env
    }
    class Scheduler {
        APScheduler
        +start_scheduler()
        +force_*()
    }
    class Dispatcher {
        +run_reservation_scrape()
        +reconcile_and_notify()
    }
    class ReservationsScraper {
        Playwright (Chromium)
        +scrape_reservations()
    }
    class MenusScraper {
        httpx + BeautifulSoup
        +scrape_menus()
    }
    class RestaurantInfoScraper {
        +refresh_restaurant_info()
    }
    class NotifPayload {
        dataclass
        +subject
        +html()
        +text()
    }
    class GmailSender {
        smtplib STARTTLS
        +send_email()
    }
    class GChatSender {
        httpx POST cardsV2
        +send_gchat()
    }
    class InboxPoller {
        imaplib IMAP4_SSL
        +poll_inbox()
    }
    class DBSession {
        SQLAlchemy 2.x
        sessionmaker
    }

    FastAPIApp --> APIRouter
    FastAPIApp --> WebRouter
    FastAPIApp --> Scheduler : starts on lifespan
    APIRouter --> DBSession
    WebRouter --> DBSession
    Scheduler --> Dispatcher : every SCRAPE_INTERVAL_MINUTES
    Scheduler --> MenusScraper : daily @ DAILY_REMINDER_HOUR
    Scheduler --> InboxPoller : every IMAP_POLL_INTERVAL_MINUTES
    Dispatcher --> ReservationsScraper
    Dispatcher --> DBSession
    Dispatcher --> NotifPayload : builds (one per user, batched)
    Dispatcher --> GmailSender
    Dispatcher --> GChatSender
    MenusScraper --> RestaurantInfoScraper : same job refreshes address
    InboxPoller --> DBSession : toggles User.enabled
    InboxPoller --> GmailSender : 1-line confirmation reply
    GmailSender ..> Settings
    GChatSender ..> Settings
    InboxPoller ..> Settings
    Scheduler ..> Settings
    Dispatcher ..> Settings
```

**Single source of truth:** every module reads `Settings` (lazy-cached via
`get_settings()`), so `.env` changes only matter on container restart.

---

## 3. AvailableSlot lifecycle (state machine)

The core state machine. Each `(date, service)` pair owns a single DB row that
moves between these states across scrape ticks.

```mermaid
stateDiagram-v2
    [*] --> Untracked : row does not exist

    Untracked --> NewlyOpen : scraper sees yellow day<br/>INSERT row<br/>is_currently_available = true

    NewlyOpen --> StillOpen : next tick, still yellow<br/>last_seen_at refreshed
    NewlyOpen --> Closed : disappeared from scrape<br/>closed_at = now

    StillOpen --> StillOpen : seen again<br/>(daily REMINDER once per UTC day,<br/>only if user.daily_reminder)
    StillOpen --> Closed : disappeared from scrape<br/>is_currently_available = false<br/>closed_at = now

    Closed --> NewlyOpen : reappears later<br/>closed_at = NULL<br/>is_currently_available = true

    note right of NewlyOpen
        Triggers OPEN notification
        for users whose preference matches
        the slot's service. One batched
        email per user covers all newly-open
        slots from this tick.
    end note

    note right of Closed
        Row is kept for analytics
        (history of openings/closings).
    end note
```

**Reminder dedup:** the `Closed → NewlyOpen` transition resets the day
counter, so a slot that closed and reopened the same day can re-trigger an
OPEN notif but the daily REMINDER is still capped to once per `sent_day`.

---

## 4. Runtime: one reservation-scrape tick

How a single scheduler tick flows from "wake up" to "DB committed". The
menu-scrape and IMAP-poll jobs follow the same lock-then-work shape; only the
reservation tick is detailed here.

```mermaid
flowchart TD
    Tick([APScheduler fires<br/>every 5 min]) --> Lock{_res_lock<br/>acquirable?}
    Lock -- no --> Skip[skip tick<br/>previous still running]
    Lock -- yes --> Scrape[Playwright launches Chromium<br/>loads CoverManager SPA]
    Scrape --> Walk[Walk LOOKAHEAD_MONTHS months<br/>collect td.has-color-availability cells]
    Walk --> Detail{SKIP_DAY_DETAIL?}
    Detail -- yes --> ServBlock[Per yellow day: emit<br/>lunch + dinner with empty times]
    Detail -- no --> Drill[Click yellow cell<br/>read each service section's<br/>time-slot buttons]
    ServBlock --> Reconcile
    Drill --> Reconcile[Reconcile against DB:<br/>• close slots that disappeared<br/>• insert brand-new slots<br/>• refresh last_seen_at on still-open]
    Reconcile --> Loop[For each enabled user]
    Loop --> Filter[Filter slots by user.preference<br/>lunch / dinner / both]
    Filter --> NewOpen{Any newly-open<br/>matching slots?}

    NewOpen -- yes --> BuildOpen[Build NotifPayload<br/>• kind=OPEN<br/>• all slots batched<br/>• booking + maps URL<br/>• unsubscribe token]
    BuildOpen --> SendOpen[send_email + send_gchat<br/>insert NotificationSent rows]
    NewOpen -- no --> Reminder
    SendOpen --> Reminder{daily_reminder + still-open<br/>+ not sent today?}

    Reminder -- yes --> BuildRem[Build NotifPayload<br/>kind=REMINDER, batched]
    BuildRem --> SendRem[send_email + send_gchat<br/>insert NotificationSent rows]
    Reminder -- no --> NextUser
    SendRem --> NextUser[next user]
    NextUser -.-> Loop
    NextUser --> Persist([Insert ScrapeRun row<br/>release lock])
    Skip --> Persist
```

**Concurrency:** `_res_lock`, `_menu_lock`, `_inbox_lock` (one per job kind)
prevent overlapping runs. Cross-job overlap is allowed — IMAP polling can run
mid-scrape without contention because each one holds its own SQLAlchemy
session.

---

## Side channels

Two side processes sit outside the main scrape loop:

```mermaid
flowchart LR
    subgraph User-facing
        UI[Browser dashboard<br/>Tailwind + HTMX + Chart.js]
    end
    subgraph Container
        API[FastAPI /api/*]
        WEB[FastAPI /, /menus, /users, /analysis]
        SCH[APScheduler]
    end
    subgraph External
        CM[CoverManager SPA<br/>booking calendar]
        SITE[hoteloccitanietoulouse.com<br/>menus + legal page]
        GMAIL[Gmail SMTP + IMAP]
        GCHAT[Google Chat webhook]
    end

    UI --> API
    UI --> WEB
    SCH -->|every 5 min| CM
    SCH -->|daily| SITE
    SCH -->|every 2 min| GMAIL
    SCH -->|on new slot| GMAIL
    SCH -->|on new slot| GCHAT
    GMAIL -.STOP/START.-> SCH
```
