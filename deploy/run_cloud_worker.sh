#!/usr/bin/env bash
set -euo pipefail

GCLOUD_BIN="$(command -v gcloud)"
AGCA_REMOTE_DATABASE_URL="$($GCLOUD_BIN secrets versions access latest \
  --secret=agca-db-url --project=project-6a717580-e37d-48f6-832)"
export AGCA_REMOTE_DATABASE_URL
exec /home/kursadsalis/AG-Crypto-Analyst-cloud/.venv/bin/python -m runtime.cloud_worker
