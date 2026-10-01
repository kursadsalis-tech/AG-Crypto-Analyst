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

# Interrupted SSH sessions can leave .git/index.lock behind after the git
# process is already gone. Heal only that stale case; never delete the lock
# while any git process is still active for this user.
if [[ -e .git/index.lock ]]; then
  if pgrep -u "$(id -u)" -af '[g]it( |$)' >/dev/null; then
    echo "Refusing deployment: .git/index.lock exists while a git process is active" >&2
    pgrep -u "$(id -u)" -af '[g]it( |$)' >&2 || true
    exit 6
  fi
  echo "Removing stale .git/index.lock from interrupted prior operation"
  rm -f .git/index.lock
fi

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

EXPECTED_SHA=$(git rev-parse HEAD)
if [[ -n "${AGCA_EXPECTED_DEPLOY_SHA:-}" && "$EXPECTED_SHA" != "$AGCA_EXPECTED_DEPLOY_SHA" ]]; then
  echo "Refusing deployment: checkout does not match AGCA_EXPECTED_DEPLOY_SHA" >&2
  exit 7
fi

if [[ ! -x .venv/bin/python ]]; then
  python3.12 -m venv .venv
fi
.venv/bin/pip install --no-cache-dir -r requirements-runtime.txt -r requirements-forecast-v3.txt

# Read-only preflight runs before any service stop/restart or schema change.
# Both routes must be ready for initial acceptance; a running manager remains
# independently available if a helper later degrades.
.venv/bin/python -m scripts.runtime_acceptance metadata \
  --expected-sha "$EXPECTED_SHA" --database-path "$DB" --health-dir "$DATA_DIR"

# Prevent scheduled writer activations while producer processes are replaced.
# An already-running oneshot is allowed to finish: never kill a DB transaction.
active_timers=(
  agca-paper-realtime-health-monitor.timer
  agca-exchange-metadata-refresh.timer
  agca-risk-refresh.timer
  agca-exact-macro-observer.timer
  agca-remote-mirror-drain.timer
  agca-cloud-attestation.timer
)
for timer in "${active_timers[@]}"; do
  sudo systemctl stop "$timer"
done
drain_deadline=$((SECONDS + 360))
for timer in "${active_timers[@]}"; do
  service="${timer%.timer}.service"
  while systemctl is-active --quiet "$service"; do
    if (( SECONDS >= drain_deadline )); then
      echo "Refusing deployment: scheduled writer did not drain: $service" >&2
      exit 8
    fi
    sleep 2
  done
done
# Keep the protective realtime authority alive until all replacements are ready.
# Stopping background workers serially avoids overlapping cold-start writers.
for service in agca-behavior-learning-worker.service agca-technical-kline-worker.service \
  agca-market-behavior-observer.service agca-trade-learning-worker.service; do
  sudo systemctl stop "$service"
done

sudo install -d -o kursadsalis -g kursadsalis -m 700 "$DATA_DIR"

# Bootstrap local-only observer tables once per deployment. The minute observer
# itself only verifies schema and must not execute CREATE/ALTER DDL while the
# live SQLite database is under continuous writes.
AGCA_SCHEMA_DB="$DB" .venv/bin/python - <<'PY'
import os
from database.market_behavior_store import bootstrap_schema
bootstrap_schema(os.environ["AGCA_SCHEMA_DB"])
PY

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
  sudo rm -f "/etc/systemd/system/$unit"
  sudo systemctl mask "$unit" 2>/dev/null || true
done

canonical_units=(
  agca-maintenance.slice
  agca-paper-realtime-worker.service
  agca-technical-kline-worker.service
  agca-behavior-learning-worker.service
  agca-trade-learning-worker.service
  agca-paper-realtime-health-monitor.service
  agca-paper-realtime-health-monitor.timer
  agca-exchange-metadata-refresh.service
  agca-exchange-metadata-refresh.timer
  agca-risk-refresh.service
  agca-risk-refresh.timer
  agca-exact-macro-observer.service
  agca-exact-macro-observer.timer
  agca-market-behavior-observer.service
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

# The next normal apt activation inherits the shared resource budget. Never
# restart a running package transaction or change its existing timer schedule.
for unit in apt-daily.service apt-daily-upgrade.service; do
  sudo install -d -m 755 "/etc/systemd/system/$unit.d"
  sudo install -m 644 deploy/agca-apt-maintenance.conf "/etc/systemd/system/$unit.d/50-agca-resources.conf"
done

sudo systemctl disable --now agca-market-behavior-observer.timer 2>/dev/null || true
sudo rm -f /etc/systemd/system/agca-market-behavior-observer.timer
sudo systemctl daemon-reload

# These are intentionally not scheduled. Observer health is local-only and
# SQLite integrity remains a manual maintenance check on the busy runtime DB.
sudo systemctl disable --now agca-observer-health-monitor.timer 2>/dev/null || true
sudo systemctl disable --now agca-sqlite-integrity-check.timer 2>/dev/null || true

# Begin staged producer restart.
# Each synchronous acceptance must return before
# the next writer starts; failure deliberately leaves later workers untouched.
# Historical behavior learning is outside the realtime decision hot path.
# Publish a compatible cache from the reviewed process before replacing its
# consumer; failures stop installation before the realtime worker is restarted.
sudo systemctl enable agca-behavior-learning-worker.service
sudo systemctl restart agca-behavior-learning-worker.service
.venv/bin/python -m scripts.runtime_acceptance cache \
  --expected-sha "$EXPECTED_SHA" --database-path "$DB" --health-dir "$DATA_DIR" \
  --window-seconds 15 --ready-timeout 120 --max-ready-seconds 1800

# Exact closed 1m technical bars have one independent websocket owner.
sudo systemctl enable agca-technical-kline-worker.service
sudo systemctl restart agca-technical-kline-worker.service
.venv/bin/python -m scripts.runtime_acceptance producer \
  --service agca-technical-kline-worker.service \
  --expected-sha "$EXPECTED_SHA" --database-path "$DB" --health-dir "$DATA_DIR" \
  --window-seconds 15 --ready-timeout 120 --max-ready-seconds 1800

# Keep the observer process alive across minute publications.
sudo systemctl enable agca-market-behavior-observer.service
sudo systemctl restart agca-market-behavior-observer.service
.venv/bin/python -m scripts.runtime_acceptance producer \
  --service agca-market-behavior-observer.service \
  --expected-sha "$EXPECTED_SHA" --database-path "$DB" --health-dir "$DATA_DIR" \
  --window-seconds 30 --ready-timeout 120 --max-ready-seconds 1800

# Post-trade learning remains a background specialist without order authority.
sudo systemctl enable agca-trade-learning-worker.service
sudo systemctl restart agca-trade-learning-worker.service
.venv/bin/python -m scripts.runtime_acceptance producer \
  --service agca-trade-learning-worker.service \
  --expected-sha "$EXPECTED_SHA" --database-path "$DB" --health-dir "$DATA_DIR" \
  --window-seconds 15 --ready-timeout 120 --max-ready-seconds 1800

# Resume scheduled support work only after every producer passed readiness.
# Start each oneshot synchronously before enabling its timer, avoiding the
# overdue Persistent=true timers all launching their first write together.
for timer in "${active_timers[@]}"; do
  sudo systemctl start "${timer%.timer}.service"
  sudo systemctl enable --now "$timer"
done

# Earlier producer readiness may have aged during another cold start. Revalidate
# the complete current-process set and both metadata routes before the consumer.
.venv/bin/python -m scripts.runtime_acceptance producer \
  --service agca-behavior-learning-worker.service --service agca-technical-kline-worker.service \
  --service agca-market-behavior-observer.service --service agca-trade-learning-worker.service \
  --expected-sha "$EXPECTED_SHA" --database-path "$DB" --health-dir "$DATA_DIR" \
  --window-seconds 30 --ready-timeout 120 --max-ready-seconds 1800
.venv/bin/python -m scripts.runtime_acceptance metadata \
  --expected-sha "$EXPECTED_SHA" --database-path "$DB" --health-dir "$DATA_DIR"

# enable --now does not replace an existing process. Restart the sole PAPER
# lifecycle only after metadata and the producer cache passed preflight.
sudo systemctl enable agca-paper-realtime-worker.service
sudo systemctl restart agca-paper-realtime-worker.service
# End staged producer restart.

systemctl is-active --quiet agca-paper-realtime-worker.service
systemctl is-active --quiet agca-technical-kline-worker.service
systemctl is-active --quiet agca-behavior-learning-worker.service
systemctl is-active --quiet agca-trade-learning-worker.service
systemctl is-active --quiet agca-market-behavior-observer.service
.venv/bin/python -m scripts.runtime_acceptance runtime \
  --expected-sha "$EXPECTED_SHA" --database-path "$DB" --health-dir "$DATA_DIR" \
  --window-seconds 60 --ready-timeout 120 --max-ready-seconds 1800 --poll-seconds 5
test ! -e "$DEPLOY/crypto_data.db"
echo "COMMIT=$(git rev-parse HEAD)"
echo "CANONICAL_DB=$DB"
echo "PAPER_RUNTIME=ACCEPTED_STABLE_EXACT_SOURCE"
