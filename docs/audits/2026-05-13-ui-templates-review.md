# UI templates & frontend correctness review

Audit performed 2026-05-13 against `main` @ commit `dd8b861`. Scope:
`app/templates/*.html`, `app/routers/pages.py`, the assets they load,
and how user-supplied data flows into the rendered HTML.

## Surface

| Page | Template | Auth | Notes |
| --- | --- | --- | --- |
| `/` | `dashboard.html` (173 LOC) | none | Open slots, last scan summary, force-scan buttons |
| `/menus` | `menus.html` (36 LOC) | none | Scraped lunch/dinner menus, links to image |
| `/users` | `users.html` (313 LOC) | bearer-token (`X-Admin-Token`) for mutations | Subscriber list + CRUD, polling-interval form |
| `/analysis` | `analysis.html` (155 LOC) | none | Chart.js dashboards over scrape history |
| Shared | `base.html` (57 LOC) | — | Tailwind + HTMX shell |

Total: 734 LOC of template, no separate JS bundle, no per-page CSS.

## Findings

### F1 — Jinja2 autoescape is ON ✅

`fastapi.templating.Jinja2Templates` defaults to `autoescape=True`
(verified at runtime via the live container). `grep` finds **zero**
`|safe` filters and zero `Markup(...)` calls in templates or render
sites. Every `{{ … }}` interpolation is HTML-entity-escaped before it
hits the response.

Notable interpolated values: scraped restaurant address, slot times,
user names, user emails, error messages from `last_res.error_message`.
All safely escaped.

### F2 — External CDN assets ⚠ medium

`base.html` and `analysis.html` load three third-party scripts from
public CDNs:

```html
<script src="https://cdn.tailwindcss.com"></script>          <!-- base.html -->
<script src="https://unpkg.com/htmx.org@1.9.12"></script>    <!-- base.html -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>  <!-- analysis.html -->
```

Two implications:

- **Offline / no-internet operation breaks the UI.** The Caddy reverse
  proxy + app container can serve the HTML, but the browser will fail
  to fetch JS/CSS if egress to `cdn.tailwindcss.com` /
  `unpkg.com` / `cdn.jsdelivr.net` is blocked.
- **Supply-chain surface**: any of those origins can ship arbitrary JS
  to a logged-in admin. No SRI hashes (`integrity=` attributes) are
  used. A compromise of one of the CDNs would pop the admin token from
  the `localStorage` on `/users`.

Tailwind in particular ships *huge* runtime — the project would benefit
from a small precomputed CSS file regardless.

**Recommendation:** Vendor these three assets into `app/static/`
(mount the static dir in `pages.py`). HTMX is ~13 KB, Chart.js UMD is
~200 KB, Tailwind via the `@tailwindcss/cli` build is typically <20 KB
for a site this size. The whole vendoring closes both findings.

### F3 — `app/static/` is empty ✅ but unused

The directory exists; `pages.py` doesn't mount it. Either delete the
directory or wire it up while doing F2's vendoring.

### F4 — Admin auth model ✅ (with caveat)

`/users` page UI requires the operator to paste `X-Admin-Token` and
stores it in `localStorage` via `saveToken()`. Mutation requests
(POST/DELETE on `/api/users/*`, polling interval changes) attach the
token as a header.

This is a **bearer-token model**, not cookie-session, so it's
**immune to CSRF** in the classic sense (no ambient credentials a
malicious site can ride). Combined with:

- the app binding to `127.0.0.1:${APP_PORT}` (Docker compose),
- Caddy fronting it with `tls internal` on the LAN,

…the threat model is "anyone on the LAN with the admin token". For a
homelab tool, that's appropriate.

**Caveat**: `localStorage` survives reboots and is readable by any JS
running on the same origin. If a future change loads third-party JS
that gets compromised (see F2), the token is trivially exfiltrated.
This argues for vendoring the CDN assets even more strongly.

### F5 — Read endpoints are unauthenticated ⚠ low

`/`, `/menus`, `/analysis`, and the JSON endpoints those pages call
have no auth at all. Anyone on the LAN can read the slot history,
restaurant address, notification log. For a single-tenant homelab
watcher this is fine; flagging because the same templates surface
`last_res.error_message` raw (escaped, but visible), which can leak
internal stack-trace fragments if the scraper throws.

### F6 — Active-link styling driven off `request.url.path` ✅

`base.html` highlights the current nav item via `{% if
request.url.path == '/' %}…`. Clean, no JS needed, accessible. Works
for the four top-level routes; would need updates if sub-routes ever
appear.

### F7 — Timezone display ✅ thoughtfully done

Datetimes are emitted as `… UTC` *and* tagged with `data-iso="…"` so the
in-page `js-paris-datetime` script can render them in `Europe/Paris`
client-side. Dashboard explicitly says "Heure affichée : Europe/Paris
(CET/CEST)." Good UX for a French audience.

### F8 — HTMX present but lightly used ✅

`base.html` loads HTMX 1.9.12 for the whole site, but inspection shows
most interaction is plain `onclick`/`fetch` JavaScript inline in
templates. That's OK — HTMX is small and present for the few places
that use it (mostly under `/users`). Could probably be removed entirely
if no template still uses `hx-*` attributes; a quick grep would confirm.

### F9 — Inline JavaScript inside templates ✅ acceptable scope

`users.html` and `analysis.html` carry meaningful inline `<script>`
blocks (token-saving, chart wiring, table rendering). At 313/155 LOC
each, this is still readable and avoids a build step. Worth keeping
as-is unless the project grows beyond a few hundred lines of UI logic.

## Summary

The templates themselves are clean: autoescape is on, no `|safe`
shortcuts, no obvious XSS sinks, the bearer-token auth model is
appropriate for the deployment shape.

The one upgrade worth making is **F2/F3: vendor the three CDN assets**
locally. It closes both an offline-fragility issue and a (currently
small) supply-chain risk in one commit, since `app/static/` already
exists and just needs to be wired into `pages.py`.

The rest of the findings are notes, not asks. The UI is in good shape.
