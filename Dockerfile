FROM python:3.11-slim

# Install Chromium + dependencies for headless Selenium
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        chromium chromium-driver \
        libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 \
        libatk-bridge2.0-0 libcups2 libxcomposite1 libxdamage1 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        libxshmfence1 fonts-liberation && \
    rm -rf /var/lib/apt/lists/* && \
    chromium --version && chromedriver --version

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data directory (mount Render disk here)
RUN mkdir -p /data/digests /data/.cache
ENV DATA_DIR=/data

EXPOSE ${PORT:-10000}

# Single worker required: APScheduler and scraper state are in-memory globals
CMD gunicorn --bind 0.0.0.0:${PORT:-10000} --timeout 300 --workers 1 --access-logfile - --preload app:app
