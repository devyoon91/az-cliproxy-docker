FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    python-telegram-bot==21.* \
    aiohttp==3.* \
    httpx

COPY bot.py .
COPY pricing/ ./pricing/

CMD ["python", "bot.py"]
