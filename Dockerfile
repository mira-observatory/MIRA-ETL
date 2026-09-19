FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=America/Guatemala
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home etl
WORKDIR /app
COPY pyproject.toml requirements-production.txt ./
COPY src ./src
RUN pip install --no-cache-dir -c requirements-production.txt . && pip check
COPY config ./config
COPY sql ./sql
USER 10001:10001
ENTRYPOINT ["mira-etl"]
CMD ["--help"]
