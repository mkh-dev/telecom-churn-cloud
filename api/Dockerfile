# syntax=docker/dockerfile:1

# --- Étape 1 : dépendances installées dans un venv -------------------------------------------
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt \
 && find /opt/venv -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +

# --- Étape 2 : image finale minimale, utilisateur non root -----------------------------------
FROM python:3.12-slim

# Modèle chargé au démarrage ; une autre révision (A/B) peut surcharger MODEL_PATH / MODEL_VERSION
ARG MODEL_FILE=model-1.0.0.joblib
# Commit de l'image, renvoyé par GET /version (passé par la CI : --build-arg GIT_SHA=<sha court>)
ARG GIT_SHA=dev

ENV PATH="/opt/venv/bin:$PATH"     GIT_SHA=${GIT_SHA} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_PATH=/app/models/${MODEL_FILE}

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin api

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY app ./app
COPY models ./models

USER 10001
EXPOSE 8000

# Python standard (urllib) plutôt que curl, pour ne rien ajouter à l'image ; 503 => échec
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
