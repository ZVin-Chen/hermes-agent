FROM tianon/gosu:1.19-trixie@sha256:3b176695959c71e123eb390d427efc665eeb561b1540e82679c15e992006b8b9 AS gosu_source
FROM debian:13.4

# Disable Python stdout buffering to ensure logs are printed immediately
ENV PYTHONUNBUFFERED=1

# Store Playwright browsers outside the volume mount so the build-time
# install survives the /opt/data volume overlay at runtime.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright

# Install system dependencies in one layer, clear APT cache.
# python3-pip is added so we can install uv via pip — this avoids the
# ghcr.io/astral-sh/uv image which is unreachable behind some networks
# (CN proxies, corporate egress, etc.) returning HTTP 403.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential nodejs npm python3 python3-pip python3-venv \
        ripgrep ffmpeg gcc python3-dev libffi-dev procps git && \
    rm -rf /var/lib/apt/lists/*

# Install uv from PyPI (Debian-managed-environment safe via --break-system-packages,
# which only affects this container).  Pinned to match the previous GHCR tag.
RUN pip install --no-cache-dir --break-system-packages uv==0.11.6

# Non-root user for runtime; UID can be overridden via HERMES_UID at runtime
RUN useradd -u 10000 -m -d /opt/data hermes

COPY --chmod=0755 --from=gosu_source /gosu /usr/local/bin/

WORKDIR /opt/hermes

# ---------- Layer-cached dependency install ----------
# Copy only package manifests first so npm install + Playwright are cached
# unless the lockfiles themselves change.
COPY package.json package-lock.json ./
COPY web/package.json web/package-lock.json web/

RUN npm install --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    (cd web && npm install --prefer-offline --no-audit) && \
    npm cache clean --force

# ---------- Source code ----------
# .dockerignore excludes node_modules, so the installs above survive.
COPY --chown=hermes:hermes . .

# Build web dashboard (Vite outputs to hermes_cli/web_dist/)
RUN cd web && npm run build

# ---------- Python virtualenv ----------
# HERMES_EXTRAS selects optional dependency groups from pyproject.toml.
# Default is "all-no-observability" — every extra except Phoenix/OTel.
# observability is opt-in so the default image stays leaner and avoids
# pulling pandas / duckdb / strawberry-graphql for users who don't trace.
#
#   docker build                                              -t hermes        .  # default = all extras except observability
#   docker build --build-arg HERMES_EXTRAS=all                -t hermes:obs    .  # everything, including Phoenix/OTel
#   docker build --build-arg HERMES_EXTRAS=observability      -t hermes:slim   .  # core + Phoenix only
#   docker build --build-arg HERMES_EXTRAS=""                 -t hermes:core   .  # core only (PyPI-style)
ARG HERMES_EXTRAS=all-no-observability
RUN chown hermes:hermes /opt/hermes
USER hermes
RUN uv venv && \
    if [ -n "$HERMES_EXTRAS" ]; then \
        uv pip install --no-cache-dir -e ".[${HERMES_EXTRAS}]"; \
    else \
        uv pip install --no-cache-dir -e .; \
    fi

# ---------- Runtime ----------
ENV HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist
ENV HERMES_HOME=/opt/data
VOLUME [ "/opt/data" ]
ENTRYPOINT [ "/opt/hermes/docker/entrypoint.sh" ]
