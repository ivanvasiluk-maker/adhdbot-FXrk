FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Fail the image build early if Python syntax breaks or merge conflict markers remain.
RUN python scripts/check_build_sanity.py

CMD ["python", "bot.py"]
