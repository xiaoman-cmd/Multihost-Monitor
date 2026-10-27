#!/usr/bin/env bash
# Build then replace this shell with ONE ASGI process; no reload supervisor.
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/mhm-uv-cache}"
export npm_config_cache="${npm_config_cache:-/private/tmp/mhm-npm-cache}"
umask 077
uv sync --frozen
npm --prefix web ci --no-audit --no-fund
npm --prefix web run build
uv run --frozen alembic upgrade head
exec .venv/bin/uvicorn app.main:create_app --factory --app-dir server --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
