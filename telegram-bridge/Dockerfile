FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    python-telegram-bot==21.* \
    aiohttp==3.* \
    httpx

COPY bot.py .
COPY pricing/ ./pricing/
COPY budget/ ./budget/
COPY task_agg/ ./task_agg/
COPY dashboard/ ./dashboard/
COPY render/ ./render/
COPY az_client/ ./az_client/

CMD ["python", "bot.py"]
