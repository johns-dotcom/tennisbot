#!/usr/bin/env bash
# Railway entrypoint — one repo, three services, selected by SERVICE_ROLE.
set -euo pipefail

case "${SERVICE_ROLE:-web}" in
  web)
    alembic upgrade head
    exec python -m bot web
    ;;
  worker)
    exec python -m bot watch
    ;;
  ingest)
    exec python -m bot ingest
    ;;
  *)
    echo "unknown SERVICE_ROLE '${SERVICE_ROLE}'" >&2
    exit 1
    ;;
esac
