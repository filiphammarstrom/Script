FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Användardata (projekt, bas-AI, nycklar) sparas under /app/data.
# Montera en PERSISTENT disk här i molnet, annars försvinner data vid varje deploy.
EXPOSE 8000

# $PORT sätts av många hostingplattformar (Render/Fly/Railway); annars 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
