FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Print the import area so Railway logs prove which bot.py snapshot is being built.
# Then fail the image build early if a merge conflict leaves Python syntax broken.
RUN nl -ba bot.py | sed -n '30,60p' && \
    python -m py_compile bot.py db.py texts.py flows.py skills.py nlp_fallback.py sheets_sync.py core/engine.py

CMD ["python", "bot.py"]