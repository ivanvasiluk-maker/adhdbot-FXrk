#!/bin/sh
set -eu

# Re-run the same safety checks at container startup. This makes a stale or
# incorrectly resolved deploy fail with a clear preflight message before the bot
# starts looping on a SyntaxError.
python scripts/check_build_sanity.py
python -m py_compile bot.py db.py texts.py flows.py skills.py nlp_fallback.py sheets_sync.py core/engine.py scripts/check_build_sanity.py
exec python bot.py
