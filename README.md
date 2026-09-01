# ADHD Self-Regulation Trainer Bot

Telegram bot that guides users through a 28-day self-regulation practice with three trainer personas (Skinny, Marsha, Beck). Built on aiogram 3, stores state in SQLite, and can optionally use OpenAI for analysis and Whisper transcription.

## Features
- Onboarding flow with trainer selection and multiple diagnostic modes (text, voice via Whisper, quick test).
- Daily skill plan with short practice loops, progress logging, and crisis flow entry from any stage.
- Optional AI analysis (OpenAI Chat + Whisper); falls back to scripted responses when API keys are absent.
- SQLite persistence with automatic init/migrations at startup.
- Dockerfile for containerized runs.

## Requirements
- Python 3.11
- Telegram bot token
- (Optional) OpenAI API key for AI analysis and voice transcription

## Setup
1) Clone the repo and create a virtualenv (Windows example):
```powershell
python -m venv venv
./venv/Scripts/Activate.ps1
```
2) Install dependencies:
```powershell
pip install -r requirements.txt
```
3) Copy `.env.example` to `.env` and fill values (see below).
4) Run the bot:
```powershell
python bot.py
```

## Environment variables
Create a `.env` file in the project root:
```
BOT_TOKEN=your-telegram-bot-token
OPENAI_API_KEY=your-openai-key-optional
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_WHISPER_MODEL=whisper-1
DB_PATH=bot.db
PAYMENT_URL=
PAYMENT_URL_DISCOUNT=
PAYMENT_URL_FULL=
PAYMENT_URL_MONTH_1498=
PAYMENT_MONTH_URL=
PAYMENT_TEST_URL=
PAYMENT_ACCEPT_ANY=0
CURATOR_TELEGRAM_ID=312112015
CURATOR_USERNAME=Ivan_Vasiliuk
ENABLE_PAYMENTS=0
SHEETS_WEBHOOK_URL=
SHEETS_SYNC_ENABLED=true
SHEETS_SYNC_INTERVAL_SECONDS=60
SHEETS_SYNC_BATCH_SIZE=50
ANALYTICS_ID_SALT=
SKILL_LIBRARY_SOURCE_URL=https://docs.google.com/spreadsheets/d/19A4NkJzZJj7mVCqSq5jmY5t1pqD8BrDb/edit
INCLUDE_REVIEWED_SKILLS_FOR_TESTERS=false
SKILL_REGISTRY_ENABLED=0
SKILL_LIBRARY_ALLOWED_STATUSES=production
SKILL_LIBRARY_PATH=data/skills
SKILL_LIBRARY_FAIL_CLOSED=1
SKILL_LIBRARY_COHORT_PERCENT=0
SKILL_LIBRARY_MANIFEST_PATH=data/skills_manifest.json
TEST_MODE=0
TEST_CHEAT_CODE=SKILLER_TEST_1498
LEARNING_ENGINE_ENABLED=false
RANKING_ENGINE_ENABLED=false
ACTIVE_SKILL_QUALITY_LEVEL=validated
BASE_OFFER_EUR=4.99
OFFER_EARLIEST_DAY=3
NEW_ARCHITECTURE_ENABLED=false
NEW_ARCHITECTURE_TEST_COHORT_ENABLED=true
NEW_ARCHITECTURE_COHORT_IDS=
BOT_STARTUP_CHECK=0
ADMIN_IDS=
```
Notes:
- Leave `OPENAI_API_KEY` empty to run without AI features.
- Product development is governed by [the Product Constitution](docs/PRODUCT_CONSTITUTION.md). New user-facing scenarios must pass its executable feature gate.
- Set `TEST_MODE=1` to skip paywalls and unlock full flow during testing.
- For cheap payment-link QA, set `PAYMENT_TEST_URL` to the €1 link and `PAYMENT_ACCEPT_ANY=1`. In this mode the offer uses the test link when available, and `/confirm_payment` or the “✅ Я оплатил(а) — тест” button manually marks the user as paid for 30 days. Turn `PAYMENT_ACCEPT_ANY` off before production.
- Production payment links do not verify provider events automatically. After paying, the user can request a manual check; the bot sends the request to `CURATOR_TELEGRAM_ID`, and the curator grants access with `/mark_paid <user_id>`. Do not promise automatic activation until a signed provider webhook is deployed.
- Set `TEST_CHEAT_CODE` to a private code; entering `/test_access <code>` or the code as a plain message enables per-user QA helpers, including `/force_next_day` and `/set_day 3` (both immediately open that day’s training) plus `/show_offer`. Destructive/admin operations such as payment marking, stats, and Sheets sync stay ADMIN-only.
- Set `BOT_STARTUP_CHECK=1` only for deploy/build sanity checks; in this mode the bot initializes and exits without starting Telegram polling.
- `DB_PATH` points to the SQLite file; it is auto-created/migrated on start. Treat this file as persistent production data: deploy scripts must mount/keep it and must not delete or recreate it, otherwise users lose their current scenario step.
- User state is stored in the `users` table and migrated additively. The durable resume columns are `telegram_id`, `day_number`, `current_step`, `access_status`, `trainer`, `mode`, `created_at`, `updated_at`, and `schema_version`; legacy bot fields are kept in sync for compatibility.

## Mandatory regression gate

PATCH-00 through PATCH-17 follow the ordered, one-patch-per-commit rollout contract in
[`docs/PATCH_ROLLOUT.md`](docs/PATCH_ROLLOUT.md). Run a patch's dedicated acceptance command before
the complete gate; CI rejects missing/out-of-order contracts and post-baseline commits without one
`PATCH-XX:` owner.

Run the offline merge gate for every patch:

```bash
python scripts/regression_gate.py
```

It runs unit and golden scenarios, legacy required regressions, state-machine restart/stale checks,
crisis/global/offer/trainer/persistent-state/day-flow smoke tests, privacy/build assertions, and a
`BOT_STARTUP_CHECK=1` boot against a temporary SQLite database. OpenAI is deliberately disabled.

## Versioned skill library

Canonical reviewed cards belong in `data/skills/*.json`; SQLite remains the source for user history,
not card text. `python scripts/import_skills.py` is a dry run by default and exports legacy cards only
with `--write`; every exported card stays `experimental` with `migration_confidence=low`.

To inspect the configured Google workbook, run `python scripts/import_skills.py --google --inspect`.
After checking its headers, run `python scripts/import_skills.py --google --output data/skills/import.json`
for a dry run and add `--write` only to save a local review snapshot. The importer uses the configured
Google URL, converts it to the official XLSX export endpoint, accepts Russian or English column names,
and refuses incomplete or unreviewed production rows. If Google link access is unavailable, pass a
downloaded workbook through `--source /path/to/skills.xlsx`.

Run `python scripts/validate_skills.py --write-manifest` after review to validate taxonomy, safety,
references and fallback graphs and to create `data/skills_manifest.json`. Use
`python scripts/skills_diff.py OLD_MANIFEST NEW_MANIFEST` in review: changing a card hash without a
version bump fails. `SKILL_REGISTRY_ENABLED=0` preserves the legacy flow. When enabled,
`SKILL_LIBRARY_FAIL_CLOSED=1` makes an invalid or empty production library fail before polling without
changing SQLite. Placeholder cards are deliberately not generated or counted as library content.

The production personalization chain is cohort-gated. Enable `RANKING_ENGINE_ENABLED=1`,
`LEARNING_ENGINE_ENABLED=1`, and either `NEW_ARCHITECTURE_ENABLED=1` or a test cohort. For those users,
day selection is made by the deterministic Ranking Engine, a normalized behavioral experiment is
created before delivery, and completed feedback is processed through Learning Engine and the
post-experiment policy. The persisted decision includes `reason_code`, `policy_version`,
`ranking_version`, and `skill_version`. With the flags off, the legacy flow and SQLite data are left
unchanged.

Post-action UX uses an explicit `post_action_reflection` step: the bot names the observed action,
interprets the tested barrier without generic praise, stores one session-specific memory anchor, and
only then offers continue/another step/finish. A failed attempt asks at most four contextual reasons
plus a free-text alternative. Completing daily training sets `daily_training_completed=1` but keeps
`interaction_allowed=1`; a substantive text or voice message always starts an additional situation
analysis, while the completed-day menu remains available for optional skills, the memory anchor, and
short learning material.

## Docker
Build and run with Docker:
```bash
docker build -t adhd-bot .
docker run --env-file .env adhd-bot
```

## Files of interest
- [bot.py](bot.py): entrypoint, router, background tasks.
- [flows.py](flows.py): main flow helpers (analysis, crises, day transitions).
- [texts.py](texts.py): trainer texts, keyboards, and constants.
- [skills.py](skills.py): skill catalog and plan builders.
- [db.py](db.py): SQLite helpers and migrations.

## Troubleshooting
- If `BOT_TOKEN` is missing, startup raises a runtime error.
- If OpenAI import fails, the bot logs a warning and continues without AI features.
- Ensure `.env` is in the working directory you run from; `load_dotenv(override=True)` is called early in [bot.py](bot.py).
- Do not test paid flows while `TelegramConflictError: terminated by other getUpdates request` appears. It means another process is already polling the same Telegram bot token, so this instance may stop or miss messages. Stop duplicate Railway/local/container runs and keep exactly one active polling process before clicking any payment link.
- For payment QA, first verify the bot responds normally to `/start` or `/show_offer`, then use the €1 `PAYMENT_TEST_URL`, and only after payment press “✅ Я оплатил(а) — тест” or send `/confirm_payment`.
- If the “✅ Я оплатил(а) — тест” button is missing from the offer, check Railway variables: `PAYMENT_ACCEPT_ANY` must be truthy (`1`, `true`, `yes`, `on`, or `debug`) and the bot must be redeployed after changing env vars.
