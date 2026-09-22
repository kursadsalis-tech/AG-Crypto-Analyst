#!/usr/bin/env bash
set -euo pipefail

DEPLOY=/home/kursadsalis/AG-Crypto-Analyst-cloud
DATA_DIR=/var/lib/agca
DB="$DATA_DIR/crypto_data.db"

if [[ ! -d "$DEPLOY/.git" ]]; then
  echo "Canonical git checkout missing: $DEPLOY" >&2
  exit 2
fi
if [[ ! -f "$DB" ]]; then
  echo "Refusing deployment without canonical database: $DB" >&2
  exit 3
fi
if [[ -e "$DEPLOY/crypto_data.db" ]]; then
  echo "Refusing deployment with a deployment-local crypto_data.db" >&2
  exit 4
fi

cd "$DEPLOY"
git diff --quiet && git diff --cached --quiet || {
  echo "Refusing deployment with local git changes" >&2
  exit 5
}

# The installer updates this checkout. If this file changes during git pull,
# continuing in the already-running shell can execute a stale mixture of the
# pre-update and post-update script. Re-exec exactly once from the refreshed
# checkout before any deployment action.
if [[ "${AGCA_DEPLOY_REEXEC:-0}" != "1" ]]; then
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
  exec env AGCA_DEPLOY_REEXEC=1 bash "$DEPLOY/deploy/install_gce_free_worker.sh"
fi

if [[ ! -x .venv/bin/python ]]; then
  python3.12 -m venv .venv
fi
.venv/bin/pip install --no-cache-dir -r requirements-runtime.txt -r requirements-forecast-v3.txt

sudo install -d -o kursadsalis -g kursadsalis -m 700 "$DATA_DIR"

if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

retired=(
  agca-control-center.service
  agca-control-broker.service
  agca-agent-supervisor.service agca-agent-supervisor.timer
  agca-behavior-learning.service agca-behavior-learning.timer
  agca-derivatives-snapshot.service agca-derivatives-snapshot.timer
  agca-risk-snapshot.service agca-risk-snapshot.timer
  agca-binance-risk-refresh.service agca-binance-risk-refresh.timer
  agca-okx-copy-trader-collector.service agca-okx-copy-trader-collector.timer
  agca-hourly-forecast.service agca-hourly-forecast.timer
  agca-live-cycle.service agca-live-cycle.timer
)
for unit in "${retired[@]}"; do
  sudo systemctl disable --now "$unit" 2>/dev/null || true
  sudo systemctl mask "$unit" 2>/dev/null || true
done

canonical_units=(
  agca-paper-realtime-worker.service
  agca-paper-realtime-health-monitor.service
  agca-paper-realtime-health-monitor.timer
  agca-exchange-metadata-refresh.service
  agca-exchange-metadata-refresh.timer
  agca-risk-refresh.service
  agca-risk-refresh.timer
  agca-exact-macro-observer.service
  agca-exact-macro-observer.timer
  agca-market-behavior-observer.service
  agca-market-behavior-observer.timer
  agca-remote-mirror-drain.service
  agca-remote-mirror-drain.timer
  agca-cloud-attestation.service
  agca-cloud-attestation.timer
  agca-observer-health-monitor.service
  agca-observer-health-monitor.timer
  agca-sqlite-integrity-check.service
  agca-sqlite-integrity-check.timer
)
for unit in "${canonical_units[@]}"; do
  sudo install -m 644 "deploy/$unit" "/etc/systemd/system/$unit"
done

sudo systemctl daemon-reload

# These are intentionally not scheduled. Observer health is local-only and
# SQLite integrity remains a manual maintenance check on the busy runtime DB.
sudo systemctl disable --now agca-observer-health-monitor.timer 2>/dev/null || true
sudo systemctl disable --now agca-sqlite-integrity-check.timer 2>/dev/null || true

active_timers=(
  agca-paper-realtime-health-monitor.timer
  agca-exchange-metadata-refresh.timer
  agca-risk-refresh.timer
  agca-exact-macro-observer.timer
  agca-market-behavior-observer.timer
  agca-remote-mirror-drain.timer
  agca-cloud-attestation.timer
)
for timer in "${active_timers[@]}"; do
  sudo systemctl enable --now "$timer"
done
sudo systemctl enable --now agca-paper-realtime-worker.service

systemctl is-active --quiet agca-paper-realtime-worker.service
test ! -e "$DEPLOY/crypto_data.db"
echo "COMMIT=$(git rev-parse HEAD)"
echo "CANONICAL_DB=$DB"
echo "PAPER_RUNTIME=ACTIVE"
