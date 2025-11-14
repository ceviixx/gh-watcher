# Slim Python image
FROM python:3.12-slim

# Non-root user
RUN useradd -m appuser

# System prerequisites
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (build cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code
COPY bot.py .

# State directory + permissions
RUN mkdir -p /state && chown -R appuser:appuser /state && chmod -R 755 /state
VOLUME ["/state"]

USER appuser

ENV PYTHONUNBUFFERED=1
ENV STATE_DIR=/state

ENTRYPOINT ["python", "/app/bot.py"]