FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    python-telegram-bot==21.* \
    aiohttp==3.* \
    httpx \
    openai

COPY bot.py .
COPY pricing/ ./pricing/
COPY budget/ ./budget/
COPY task_agg/ ./task_agg/
COPY dashboard/ ./dashboard/
COPY render/ ./render/
COPY az_client/ ./az_client/
COPY telegram_handlers/ ./telegram_handlers/
COPY notify/ ./notify/
COPY webhooks/ ./webhooks/
COPY monitor/ ./monitor/
COPY streaming/ ./streaming/

CMD ["python", "bot.py"]
