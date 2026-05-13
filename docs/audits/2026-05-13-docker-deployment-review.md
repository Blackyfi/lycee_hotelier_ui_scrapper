# Docker & deployment configuration review

Audit performed 2026-05-13 against `main` @ commit `5b5c9df`. Live cluster
(`lycee-scraper` + `lycee-caddy`) is up and healthy at audit time.

## Verification commands actually run

| Check | Command | Result |
| --- | --- | --- |
| Compose schema | `docker compose -f docker-compose.yml --env-file .env config -q` | exit 0 (valid) |
| Container health | `docker ps --filter name=lycee` | `lycee-scraper Up 24h (healthy)` |
| `/healthz` 200 | `python /app/scripts/healthcheck.py` (inside container) | exit 0, body `{"ok":true}` |
| `.env` not tracked | `git ls-files | grep ^\.env$` | empty |
| `.env` not in history | `git log --all --diff-filter=AM --pretty="" --name-only | grep ^\.env$` | empty |
| `.gitignore` masks `.env` | `grep -E "^\.env" .gitignore` | matches `.env`, `.env.*` |
| Ruff lint | `ruff check .` (via 0.7.2 sidecar container) | **2783 errors, 2752 auto-fixable** |

## Findings

### F1 — `.env` history hygiene ✅

`.env` is correctly excluded by `.gitignore`, not present in the working tree
copy that ships, and not in any commit on `main`. `.env.example` (3.8 KB) is
the documented template. No remediation needed.

### F2 — Image build ✅

`Dockerfile` is small, well-staged, and runs as non-root (uid 1000). Build
cache layers are reasonable: requirements before sources, sources at the
end. `.dockerignore` correctly excludes `.git`, `.env`, `data/`, `runs/`,
`tls/`, dev tooling, and editor noise — image stays slim.

Minor: `requirements.txt` is fully pinned; `requirements-dev.txt` (only
needed for tests/lint) is correctly excluded from the prod image.

### F3 — Compose configuration ✅

- App is exposed on `127.0.0.1` only (`"127.0.0.1:${APP_PORT:-8765}:8000"`)
  — public access goes through the Caddy reverse proxy on the LAN, not the
  app directly. Good.
- Caddy uses `tls internal` for ACME-free LAN HTTPS, bound to `$LOCAL_HOST`
  + `localhost`. Internal CA, no public cert exposure.
- Volumes are sensible: `./data:/data` for persistence, named volumes for
  Caddy state.
- Healthcheck wired through `scripts/healthcheck.py` (probes `/healthz` on
  loopback; auto-switches scheme if TLS env is set). 30 s interval, 5 s
  timeout, 5 retries, 30 s start period — reasonable defaults.
- `restart: unless-stopped` on both services — survives host reboots
  without fighting `docker stop`.

### F4 — Boot script ✅

`scripts/start.sh` uses `set -euo pipefail`, supports optional uvicorn TLS
when `TLS_CERT_FILE`/`TLS_KEY_FILE` are set (mutually-exclusive validation,
explicit file-exists checks), and uses `exec` so PID 1 is uvicorn (tini
wraps it from `ENTRYPOINT`). Signal handling is correct.

### F5 — Healthcheck endpoint ✅

`/healthz` lives in `app/routers/pages.py:101` and returns `{"ok": true}`
with HTTP 200. Verified live inside the running container.

### F6 — Ruff lint debt ⚠

`ruff check .` returns **2783 errors** against the configured rule set
(`E, W, F, I, UP, B`, py311 target). **2752 are auto-fixable** via
`ruff check --fix .`; another 13 are reachable via `--unsafe-fixes`.

Spot-checked patterns:

- `UP017` — `datetime.timezone.utc` → `datetime.UTC` (Python 3.11 alias)
- `I001` — import order

These are mechanical clean-ups, not behaviour bugs, but the volume means
any new code review is buried under unrelated diff. The "Fix: Fix:"
verification chain that spawned this audit kept failing partly because
the agent's edits couldn't compile cleanly against the lint gate.

**Recommendation:** Apply `ruff check --fix .` as a single dedicated commit
("style: bulk apply ruff auto-fixes (UP017, I001, …)") before the next
substantive PR. Keep `--unsafe-fixes` separate so reviewers can sanity-
check that handful.

### F7 — Verify pipeline not wired ⚠

There is no checked-in CI workflow, no `scripts/verify.sh`, and the agent
verification commands that were running per Argus were:

```
test -f pyproject.toml && (ruff check . && pytest -q) || true
```

The `|| true` clobbers the exit code, so a red lint is silently swallowed.

**Recommendation:** Add a `scripts/verify.sh` that exits non-zero on lint
or test failure:

```bash
#!/usr/bin/env bash
set -euo pipefail
ruff check .
pytest -q
```

…and wire it as the verify command for this project so future agent runs
get an honest signal.

## Summary

The deployment configuration itself (Dockerfile, compose, Caddy, start
script, healthcheck, secrets handling) is in good shape — no blocking
issues. The two soft findings (F6 ruff debt, F7 toothless verify gate)
explain *why* the "Fix: Fix:" chain on this task kept spinning: the agent
couldn't prove its work because verification was both noisy and
swallowing failures.

Suggested next action: apply `ruff check --fix .` as one stylistic
commit, add `scripts/verify.sh`, then the next audit run has a clean
signal to work with.
