FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Shared stdlib-only job history/trace package (not on PyPI).
COPY packages/production-trace /app/packages/production-trace
WORKDIR /app/video-postproduction-api
COPY services/video-postproduction-api/pyproject.toml ./
COPY services/video-postproduction-api/video_postproduction ./video_postproduction
COPY services/video-postproduction-api/migrations ./migrations
COPY services/video-postproduction-api/alembic.ini ./

RUN pip install /app/packages/production-trace && pip install .[transcription]

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
