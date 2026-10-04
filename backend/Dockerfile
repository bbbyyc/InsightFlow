FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --timeout 180 --retries 8 -r /tmp/requirements.txt

RUN addgroup --system insightflow && adduser --system --ingroup insightflow insightflow \
    && mkdir -p /app/data/uploads /app/data/evaluations /models/fastembed \
    && chown -R insightflow:insightflow /app /models

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=insightflow:insightflow backend/ /app/
COPY --chown=insightflow:insightflow eval/ /eval/
COPY --chown=insightflow:insightflow scripts/ /scripts/
COPY --chown=insightflow:insightflow demo/ /demo/

USER insightflow
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
