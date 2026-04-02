FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    cd-paranoia cdda2wav cd-discid flac eject curl \
    ffmpeg lame opus-tools udev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Frontend (pre-built)
COPY frontend/dist /app/static

# Backend
COPY backend /app/backend

ENV PYTHONPATH=/app
EXPOSE 3900
CMD ["sh", "-c", "alembic -c backend/alembic/alembic.ini upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 3900"]
