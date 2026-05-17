FROM python:3.11-slim

# Force unbuffered logs (Coolify shows them live), no .pyc clutter, predictable timezone.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libglib2.0-0 \
        libsndfile1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer caching: install deps before copying code.
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

# Source code last (changes most often).
COPY . .

RUN chmod +x start.sh

EXPOSE 8000

# Coolify / orchestrators can use this for liveness checks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["sh", "start.sh"]
