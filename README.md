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
ENABLE_PAYMENTS=0
SHEETS_WEBHOOK_URL=
SHEETS_SYNC_ENABLED=true
SHEETS_SYNC_INTERVAL_SECONDS=60
SHEETS_SYNC_BATCH_SIZE=50
TEST_MODE=0
TEST_CHEAT_CODE=SKILLER_TEST_1498
BOT_STARTUP_CHECK=0
ADMIN_IDS=
```
Notes:
- Leave `OPENAI_API_KEY` empty to run without AI features.
- Set `TEST_MODE=1` to skip paywalls and unlock full flow during testing.
- For cheap payment-link QA, set `PAYMENT_TEST_URL` to the €1 link and `PAYMENT_ACCEPT_ANY=1`. In this mode the offer uses the test link when available, and `/confirm_payment` or the “✅ Я оплатил(а) — тест” button manually marks the user as paid for 30 days. There is no automatic provider-side payment verification without a payment webhook. Turn `PAYMENT_ACCEPT_ANY` off before production.
- Set `TEST_CHEAT_CODE` to a private code; entering `/test_access <code>` or the code as a plain message enables per-user QA helpers, including `/force_next_day` and `/set_day 3` (both immediately open that day’s training) plus `/show_offer`. Destructive/admin operations such as payment marking, stats, and Sheets sync stay ADMIN-only.
- Set `BOT_STARTUP_CHECK=1` only for deploy/build sanity checks; in this mode the bot initializes and exits without starting Telegram polling.
- `DB_PATH` points to the SQLite file; it is auto-created/migrated on start. Treat this file as persistent production data: deploy scripts must mount/keep it and must not delete or recreate it, otherwise users lose their current scenario step.
- User state is stored in the `users` table and migrated additively. The durable resume columns are `telegram_id`, `day_number`, `current_step`, `access_status`, `trainer`, `mode`, `created_at`, `updated_at`, and `schema_version`; legacy bot fields are kept in sync for compatibility.

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
