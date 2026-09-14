FROM node:22.23.2-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend ./
RUN npm run typecheck && npm test && npm run build && npm audit --audit-level=high

FROM ubuntu:24.04 AS build-base

ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=
ARG ALL_PROXY=

ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    ALL_PROXY=${ALL_PROXY} \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get -o Acquire::http::Proxy="false" -o Acquire::https::Proxy="false" update \
 && apt-get -o Acquire::http::Proxy="false" -o Acquire::https::Proxy="false" install -y --no-install-recommends \
      build-essential ca-certificates python3 python3-dev python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir uv

FROM build-base AS web-dependencies
COPY docker/requirements-web.txt /tmp/requirements.txt
RUN uv venv /opt/venv \
 && uv pip sync --python /opt/venv/bin/python /tmp/requirements.txt

FROM build-base AS document-dependencies
COPY docker/requirements-document.txt /tmp/requirements.txt
RUN uv venv /opt/venv \
 && uv pip sync --python /opt/venv/bin/python /tmp/requirements.txt

FROM document-dependencies AS test
COPY docker/requirements-test.txt /tmp/requirements.txt
RUN uv pip sync --python /opt/venv/bin/python /tmp/requirements.txt
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY Dockerfile gunicorn.conf.py ./
COPY docker-compose.yaml ./
COPY docker/release-components.json ./docker/release-components.json
COPY app.py ./
COPY ipaper ./ipaper
COPY static ./static
COPY --from=frontend-build /build/static/workbench ./static/workbench
COPY templates ./templates
COPY scripts ./scripts
COPY security ./security
COPY .github ./.github
COPY tests ./tests
RUN pytest -m "not integration" -q

FROM build-base AS worker-dependencies
COPY docker/requirements-worker.txt /tmp/requirements.txt
RUN uv venv /opt/venv \
 && uv pip sync --python /opt/venv/bin/python /tmp/requirements.txt

FROM ubuntu:24.04 AS runtime-base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get -o Acquire::http::Proxy="false" -o Acquire::https::Proxy="false" update \
 && apt-get -o Acquire::http::Proxy="false" -o Acquire::https::Proxy="false" install -y --no-install-recommends \
      ca-certificates libglib2.0-0 libgl1 libgomp1 libx11-6 libxcb1 libxext6 libxrender1 python3 \
 && rm -rf /var/lib/apt/lists/*

FROM runtime-base AS translation-worker

ARG TRANSLATION_WORKER_VERSION=1.2.0
ARG VCS_REF=unknown

ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app
COPY --from=worker-dependencies /opt/venv /opt/venv
COPY ipaper /app/ipaper

RUN groupadd --gid 1001 ipaper \
 && useradd --uid 10002 --gid 1001 --no-create-home --home-dir /app --shell /usr/sbin/nologin ipaper-worker \
 && mkdir -p /work/jobs \
 && chown 10002:1001 /work/jobs

LABEL org.opencontainers.image.title="iPaper Translation Worker" \
      org.opencontainers.image.version="${TRANSLATION_WORKER_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/ifzzh/iPaper"

USER 10002:1001
EXPOSE 7192
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7192/healthz', timeout=4).read()"
CMD ["python", "-m", "ipaper.translation_worker"]

FROM runtime-base AS document-worker

ARG DOCUMENT_WORKER_VERSION=1.2.0
ARG VCS_REF=unknown

ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

WORKDIR /app
COPY --from=document-dependencies /opt/venv /opt/venv
COPY ipaper /app/ipaper

RUN groupadd --gid 1001 ipaper \
 && useradd --uid 10003 --gid 1001 --no-create-home --home-dir /app --shell /usr/sbin/nologin ipaper-document \
 && mkdir -p /work/document-jobs \
 && chown 10003:1001 /work/document-jobs

LABEL org.opencontainers.image.title="iPaper Document Worker" \
      org.opencontainers.image.version="${DOCUMENT_WORKER_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/ifzzh/iPaper"

USER 10003:1001
EXPOSE 7193
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7193/healthz', timeout=4).read()"
CMD ["python", "-m", "ipaper.document_worker"]

FROM runtime-base AS runtime

ARG APP_VERSION=1.4.0
ARG VCS_REF=unknown
ARG ARXIV_PROXY=
ARG ARXIV_API_PROXY=
ARG ARXIV_HTTP_PROXY=
ARG ARXIV_HTTPS_PROXY=

ENV ARXIV_PROXY=${ARXIV_PROXY} \
    ARXIV_API_PROXY=${ARXIV_API_PROXY} \
    ARXIV_HTTP_PROXY=${ARXIV_HTTP_PROXY} \
    ARXIV_HTTPS_PROXY=${ARXIV_HTTPS_PROXY}

WORKDIR /app
COPY --from=web-dependencies /opt/venv /opt/venv
COPY app.py /app/app.py
COPY wsgi.py /app/wsgi.py
COPY gunicorn.conf.py /app/gunicorn.conf.py
COPY ipaper /app/ipaper
COPY static /app/static
COPY --from=frontend-build /build/static/workbench /app/static/workbench
COPY templates /app/templates

RUN groupadd --gid 1001 ipaper \
 && useradd --uid 10001 --gid 1001 --no-create-home --home-dir /app --shell /usr/sbin/nologin ipaper \
 && mkdir -p /app/db /data/papers /work/jobs /work/document-jobs \
 && chown -R 10001:1001 /app /data/papers /work/jobs /work/document-jobs

LABEL org.opencontainers.image.title="iPaper" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/ifzzh/iPaper"

USER 10001:1001
EXPOSE 7191
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7191/healthz', timeout=4).read()"
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:application"]
