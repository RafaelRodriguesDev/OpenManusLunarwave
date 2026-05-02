FROM python:3.12-bookworm

WORKDIR /app/OpenManus

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PATH="/app/OpenManus/.venv/bin:$PATH"
ENV DISPLAY=:99

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    bash \
    build-essential \
    ca-certificates \
    gcc \
    g++ \
    make \
    python3-dev \
    pkg-config \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    libjpeg-dev \
    libpng-dev \
    libsqlite3-dev \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fluxbox \
    chromium \
    chromium-driver \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY . .

RUN uv venv --python 3.12

RUN uv pip install --upgrade pip setuptools wheel

RUN uv pip install -r requirements.txt

RUN python -m playwright install chromium

COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]