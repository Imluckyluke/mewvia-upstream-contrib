# syntax=docker/dockerfile:1

# ---- builder: build wheels once, keep the runtime image slim ----
FROM denoland/deno:bin-2.5.6@sha256:2f5f9d651af33c337767c423a340f76df35e35ff8ff4734210237b9c6fed94c7 AS deno
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /wheels
COPY requirements.txt requirements-dev.txt ./
RUN pip wheel --wheel-dir=/wheels -r requirements-dev.txt


# ---- runtime ----
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime

COPY --from=deno /deno /usr/local/bin/deno

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

# ffmpeg/ffprobe: the media engine. postgresql-client: pg_dump for backups.
# Retries/timeout make the build survive flaky networks. Zombie reaping is
# handled by Docker's own init (`init: true` in docker-compose.yml), so no tini.
RUN printf '%s\n' \
        'Acquire::Retries "10";' \
        'Acquire::http::Timeout "30";' \
        'Acquire::https::Timeout "30";' \
        > /etc/apt/apt.conf.d/99-mewvia-retries \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        age iptables util-linux \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels -r /tmp/requirements.txt

# Non-root. Temp workdir for media jobs is owned by the app user.
# Tool caches live under $HOME, not /app — /app is bind-mounted read-heavy in dev
# and writing caches there would fail (and pollute the host tree).
ENV RUFF_CACHE_DIR=/home/mewvia/.cache/ruff \
    MYPY_CACHE_DIR=/home/mewvia/.cache/mypy \
    PYTHONPYCACHEPREFIX=/home/mewvia/.cache/pycache

RUN useradd --create-home --uid 10001 mewvia \
    && mkdir -p /data/tmp /data/backups /app \
        /home/mewvia/.cache/ruff /home/mewvia/.cache/mypy \
    && chown -R mewvia:mewvia /data /app /home/mewvia

WORKDIR /app
COPY --chown=mewvia:mewvia app /app/app
COPY --chown=mewvia:mewvia migrations /app/migrations
COPY --chown=mewvia:mewvia assets /app/assets
COPY --chown=mewvia:mewvia alembic.ini /app/

USER mewvia

CMD ["python", "-m", "app"]

FROM runtime AS test
USER root
COPY requirements-dev.txt /tmp/
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels -r /tmp/requirements-dev.txt
COPY --chown=mewvia:mewvia tests /app/tests
COPY pyproject.toml /app/
USER mewvia

FROM runtime AS production
