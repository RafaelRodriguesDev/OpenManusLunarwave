FROM python:3.12-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PATH="/app/OpenManus/.venv/bin:$PATH"

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
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

RUN git clone https://github.com/FoundationAgents/OpenManus.git /app/OpenManus

WORKDIR /app/OpenManus

RUN sed -i 's/pillow~=11.1.0/pillow>=10.4,<11.0/g' requirements.txt

RUN uv venv --python 3.12

RUN uv pip install --upgrade pip setuptools wheel

RUN uv pip install -r requirements.txt

RUN python -m playwright install --with-deps chromium

COPY web_server.py /app/OpenManus/web_server.py
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]