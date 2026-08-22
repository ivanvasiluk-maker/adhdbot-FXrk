# SKILLER staging and production promotion

SKILLER keeps the existing single-process/SQLite stack. Staging and production must use different Telegram bots, environment files, and database files.

## Staging

1. Create a Telegram test bot and copy `.env.staging.example` to a secret environment file outside Git.
2. Set a unique `BOT_TOKEN`, `ADMIN_IDS`, and `DB_PATH=data/skiller-staging.db`. Never point this path at the production volume.
3. Deploy the `develop`/`staging` branch only.
4. Validate startup and run the bot:

```bash
set -a; . /secure/path/skiller-staging.env; set +a
python scripts/check_build_sanity.py
python scripts/required_regression_tests.py
BOT_STARTUP_CHECK=1 python bot.py
bash scripts/start_bot.sh
```

5. Manually smoke-test `/admin`: reset test user, start day, fake success/failure, Day 1 insight, prediction, offer paths, free exit, and lead form.

## Production promotion

Production is never deployed directly from an AI-generated commit.

1. Ensure staging smoke tests passed and the candidate commit is reviewed.
2. Open and review a PR from `develop`/`staging` to `main`.
3. Run the full suite on the exact candidate commit: `python -m pytest -q`.
4. Merge to `main` only after review.
5. Copy `.env.production.example` to the production secret store. Use the production Telegram token and `DB_PATH=data/skiller-production.db`; set `PAYMENT_ACCEPT_ANY=0` and `TEST_MODE=0`.
6. Back up the production database: `python scripts/backup_sqlite.py`.
7. Run `BOT_STARTUP_CHECK=1 python bot.py` against production configuration.
8. Deploy `main`, then smoke-test `/health`, `/whoami`, free navigation, and one real payment callback without modifying user history.
9. Roll back to the previous reviewed commit and database backup if startup, routing, or payment smoke checks fail.
