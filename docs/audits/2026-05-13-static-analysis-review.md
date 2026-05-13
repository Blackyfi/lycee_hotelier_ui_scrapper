# Static analysis & code-quality audit

Audit performed 2026-05-13 against `main` @ commit `37d1c39`. Three
toolchains run against the repo as it stands:

| Tool | Version | Result |
| --- | --- | --- |
| `ruff check` | 0.7.2 (pinned in requirements-dev.txt) | 2783 errors |
| `mypy app` | 1.13.0 (pinned) | 3 errors in 2 files |
| `pip-audit` | 2.7.3 (pinned) | 7 vulnerabilities across 4 packages |

The [[docker-deployment-review]] flagged F6/F7 (ruff debt + verify gate
swallowing exit codes) at a high level; this audit decomposes the
findings into actionable bites.

## F1 — Dependency vulnerabilities ⚠ high, 7 CVEs

`pip-audit -r requirements.txt --strict`:

| Package | Current | Fix | Notes |
| --- | --- | --- | --- |
| jinja2 | 3.1.5 | **3.1.6** | GHSA-cpwx-vrp4-4pq7 |
| python-multipart | 0.0.20 | **0.0.27** | three CVEs (GHSA-wp53, mj87, pp6c) |
| lxml | 5.3.0 | **6.1.0** | GHSA-vfmq-68hx-4jfw (major jump) |
| starlette | 0.41.3 | **0.49.1** | GHSA-2c2j + GHSA-7f5h (pulled in by fastapi) |

`starlette` and `python-multipart` are reachable from any HTTP request
(routing + multipart-form-parsing). The Jinja CVE is reachable from any
templated response.

**Recommendation:** Bump each in `requirements.txt`. `lxml 5 → 6` and
`starlette 0.41 → 0.49` may need light testing because of major-version
jumps — pin to the **patch** above each CVE first
(`jinja2==3.1.6`, `python-multipart==0.0.27`) and reserve the bigger
upgrades for a separate dedicated commit so it's easy to bisect a
regression.

## F2 — Mypy errors ⚠ real bugs, 3 errors

```
app/notifications/inbox.py:121 — Argument 1 to "message_from_bytes" has
   incompatible type "int | bytes"; expected "bytes | bytearray"
app/scraper/menus.py:70  — Missing return statement
app/scraper/menus.py:208 — Incompatible types in assignment (str → int)
```

All three are **real defects**, not noise:

- `inbox.py:121` — the IMAP `fetch()` return tuple is being unpacked
  loosely; on certain `BYE`/`NO` responses the second element can be an
  `int` rather than `bytes`, which would crash `message_from_bytes`.
  Worth a defensive isinstance check + an early return.
- `menus.py:70` — a branch falls off the end without `return`,
  producing an implicit `None` where the caller expects a value.
- `menus.py:208` — a variable typed `int` is reassigned to a `str`. The
  function will work right up until the `int` value is referenced.

**Recommendation:** Fix all three. They are individual, scoped patches —
nothing requires architectural surgery.

## F3 — Ruff debt ⚠ medium, 2783 errors

Breakdown (top 8 of 13 categories):

| Count | Rule | Description | Fix |
| --- | --- | --- | --- |
| 1815 | UP017 | `datetime.timezone.utc` → `datetime.UTC` | auto |
| 354 | I001 | unsorted imports | auto |
| 334 | F401 | unused imports | auto |
| 121 | UP037 | quoted annotations (legacy) | auto |
| 119 | UP035 | deprecated `typing` import (`Tuple`, `List`, …) | manual |
| 15 | E501 | line too long (>120) | manual |
| 10 | UP038 | non-PEP604 `isinstance` (`(int, str)` → `int | str`) | auto |
| 6 | UP006 | non-PEP585 (`List[int]` → `list[int]`) | auto |

About 98% (2752 of 2783) are reachable via `ruff check --fix .` with no
human review. The remaining 31 (`UP035` deprecated-import + `E501`
line-too-long + a couple of `B023` loop-variable closures) need eyeballs
but are individually trivial.

**Recommendation:** Land one *dedicated* `style:` commit applying the
auto-fixes — split from any behaviour change so reviewers can skim
file-touched count instead of trying to parse it. After that, the
[[docker-deployment-review]]'s `scripts/verify.sh` actually makes
ruff a useful gate.

## F4 — Repo hygiene ✅ (mostly)

- `.dockerignore` correctly excludes `.git`, `.env`, dev tooling.
- `.gitignore` masks `.env`, `.env.*`, `.envrc`, `__pycache__`,
  `.ruff_cache`, `.pytest_cache`. `runs/` is **not** in `.gitignore`
  even though it accumulates 200+ subdirectories — operationally fine
  if `runs/` is treated as ephemeral, but worth confirming.

## Summary

The codebase is small and focused; static analysis surfaces three
genuine categories of issue:

1. **Patch CVEs in jinja2, python-multipart, lxml, starlette.** Highest
   priority — these are reachable from request handling and templating.
2. **Fix the three mypy errors.** Two are in `app/scraper/menus.py`,
   one in IMAP parsing. All scoped.
3. **Apply ruff auto-fixes** in one stylistic commit, then keep the gate
   honest via `scripts/verify.sh` (already added by
   [[docker-deployment-review]]).

None of these block the running deployment, but together they're what
keep "Fix: Fix:" verification chains green when the next agent tries.
