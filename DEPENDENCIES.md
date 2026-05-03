# Dependencies

This document lists every third-party component this project pulls in
(direct dependencies only — transitive deps are pulled by pip/Playwright
according to each package's own metadata), what it is used for, and its
license. SPDX identifiers are used where possible.

> There is no single industry-standard filename for this. We use
> `DEPENDENCIES.md` because it is searchable on GitHub. The closest
> formal cousin is the [SPDX SBOM](https://spdx.dev/) format — generate
> one with `pip install pip-licenses && pip-licenses --format=markdown
> --with-urls` if you need a machine-readable copy.

## Python (pip — `requirements.txt`)

| Package             | Version    | License             | Purpose in this project                                            |
|---------------------|------------|---------------------|--------------------------------------------------------------------|
| fastapi             | 0.115.6    | MIT                 | HTTP framework for the API + page routes.                          |
| uvicorn[standard]   | 0.34.0     | BSD-3-Clause        | ASGI server (with `httptools`, `uvloop`, `websockets`).            |
| jinja2              | 3.1.4      | BSD-3-Clause        | Server-side HTML templates.                                        |
| python-multipart    | 0.0.20     | Apache-2.0          | Form parsing for FastAPI.                                          |
| pydantic            | 2.10.4     | MIT                 | Request/response models, validation.                               |
| pydantic-settings   | 2.7.0      | MIT                 | `.env` → settings binding.                                         |
| sqlalchemy          | 2.0.36     | MIT                 | ORM + engine for SQLite persistence.                               |
| alembic             | 1.14.0     | MIT                 | DB migrations (reserved — current schema uses `create_all`).       |
| apscheduler         | 3.11.0     | MIT                 | Background interval / cron jobs (poll, daily reminders).           |
| playwright          | 1.49.1     | Apache-2.0          | Headless Chromium for the Angular calendar (covermanager).         |
| httpx               | 0.28.1     | BSD-3-Clause        | HTTP client (menu scraping, GChat webhook).                        |
| beautifulsoup4      | 4.12.3     | MIT                 | HTML parsing for the menu listing pages.                           |
| lxml                | 5.3.0      | BSD-3-Clause        | Fast parser backend used by BeautifulSoup.                         |
| python-dateutil     | 2.9.0.post0| Apache-2.0 / BSD-3-Clause (dual) | Date utilities.                                       |

### Bundled at runtime

| Component  | Source                                         | License                       | Notes                                                  |
|------------|------------------------------------------------|-------------------------------|--------------------------------------------------------|
| Chromium   | downloaded by Playwright on install            | BSD-3-Clause + others         | Headless browser used by `app.scraper.reservations`.   |
| Python 3   | base image `mcr.microsoft.com/playwright/python:v1.49.1-noble` | PSF-2.0 | Container interpreter.                              |
| Ubuntu Noble (24.04) | base image                          | various (mostly MIT/BSD/GPL)  | Container OS.                                          |

## Front-end (loaded over CDN — no build step)

| Asset            | Version  | License        | URL                                                            |
|------------------|----------|----------------|----------------------------------------------------------------|
| Tailwind CSS     | Play CDN | MIT            | `https://cdn.tailwindcss.com`                                  |
| HTMX             | 1.9.12   | 0BSD           | `https://unpkg.com/htmx.org@1.9.12`                            |
| Chart.js         | 4.4.7    | MIT            | `https://cdn.jsdelivr.net/npm/chart.js@4.4.7/.../chart.umd.min.js` |

If you'd rather vendor these instead of using CDNs, drop the files into
`app/static/` and update the `<script>` / `<link>` tags in
`app/templates/base.html` (and `analysis.html` for Chart.js).

## License compatibility

This project is distributed under **GPL-3.0-or-later** (see `LICENSE`).
All dependencies above are GPL-compatible (MIT, BSD-2/3, Apache-2.0, 0BSD).
There are no GPL-incompatible runtime requirements.

## Regenerating this file

After changing `requirements.txt`:

```bash
docker compose run --rm app pip install pip-licenses
docker compose run --rm app pip-licenses --format=markdown \
    --with-urls --with-license-file=false
```

…and update the table above. (We don't auto-generate it because the
"Purpose in this project" column is hand-written.)
