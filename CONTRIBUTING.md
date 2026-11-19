# Contributing

Thanks for your interest in Lycée Hôtelier Watcher. This is a small personal
project, but bug reports, ideas and pull requests are welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on
  the approach before you spend time on it.
- Be gentle with the upstream site. Changes that poll it more aggressively
  (shorter intervals, extra page loads per tick) will not be merged.
- Never commit secrets: `.env`, `secrets/`, database files and TLS material are
  git-ignored on purpose. Use `.env.example` to document new settings.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium
cp .env.example .env   # then edit it
```

Set `NOTIFICATIONS_DRY_RUN=true` in your `.env` while developing so nothing is
actually emailed or posted to Google Chat.

## Checks

Run these before opening a pull request:

```bash
ruff check .
mypy app
./scripts/verify.sh
```

## Pull requests

- Keep each PR focused on one change.
- Write commit messages in the imperative, optionally with a conventional
  prefix (`feat:`, `fix:`, `docs:`, `chore:`...).
- If you add a setting, document it in both `.env.example` and the README
  configuration table.
- If you change the database schema, explain the migration steps in the PR:
  the live database is SQLCipher-encrypted and is not migrated automatically.
- Update `ARCHITECTURE.md` when you change how modules fit together.

## License

By contributing, you agree that your contributions are licensed under the
GNU GPL v3, like the rest of the project (see [`LICENSE`](./LICENSE)).
