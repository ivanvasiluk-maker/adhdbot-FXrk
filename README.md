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
SHEETS_WEBHOOK_URL=
TEST_MODE=0
```
Notes:
- Leave `OPENAI_API_KEY` empty to run without AI features.
- Set `TEST_MODE=1` to skip paywalls and unlock full flow during testing.
- `DB_PATH` points to the SQLite file; it is auto-created/migrated on start.

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
