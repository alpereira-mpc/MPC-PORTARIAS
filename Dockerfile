FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libreoffice-writer-nogui \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser . ./

USER appuser
EXPOSE 8080

CMD ["sh", "-c", "exec streamlit run portal.py --server.address=0.0.0.0 --server.port=${PORT:-8080}"]
