#!/usr/bin/env bash
set -a; [ -f .env ] && . ./.env; set +a
cd "$(dirname "$0")"
exec python3 -m uvicorn mask:app --host 0.0.0.0 --port "${PORT:-54468}" --workers "${WORKERS:-2}"
