# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git is a runtime requirement: the expertise graph parser shells out to `git log`.
RUN apt-get update \
    && apt-get install --no-install-recommends -y git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. /data holds the SQLite database, /repo the git checkout.
RUN groupadd --system --gid 1001 sentinel \
    && useradd --system --uid 1001 --gid sentinel --home-dir /app --shell /usr/sbin/nologin sentinel \
    && mkdir -p /app /data /repo \
    && chown -R sentinel:sentinel /app /data

WORKDIR /app

# Dependency metadata first, so the dependency layer is reused until pyproject.toml changes.
# README.md and LICENSE are required by the setuptools build (readme/license fields).
COPY pyproject.toml README.md LICENSE ./

# Install only the runtime dependencies from [project.dependencies] (no dev extras).
RUN python -c "import pathlib, tomllib; print('\n'.join(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Then the source; the package itself is installed without re-resolving dependencies.
COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

# No secrets are baked in: every setting is read from environment variables at runtime.
USER sentinel:1001

ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATABASE_PATH=/data/pr_sentinel.db \
    HOME=/app

EXPOSE 8000

# Liveness probe against the FastAPI health endpoint (GET /health, no auth).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4).status == 200 else 1)"

CMD ["python", "-m", "pr_sentinel.main"]
