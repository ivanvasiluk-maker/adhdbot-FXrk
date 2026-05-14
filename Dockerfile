FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Fail the image build early if a merge conflict leaves Python syntax broken.
RUN python -m py_compile bot.py db.py texts.py flows.py skills.py nlp_fallback.py sheets_sync.py core/engine.py

CMD ["python", "bot.py"]