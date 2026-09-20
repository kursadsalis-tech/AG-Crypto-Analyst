#!/usr/bin/env bash
set -euo pipefail

sudo systemctl disable --now agca-live-cycle.timer agca-hourly-forecast.timer 2>/dev/null || true
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage=/home/kursadsalis/AG-Crypto-Analyst-cloud-new
backup="/home/kursadsalis/AG-Crypto-Analyst-cloud-backup-${stamp}"

if [[ -e "$stage" ]]; then
  echo "Refusing to overwrite existing staging path: $stage" >&2
  exit 2
fi
mkdir -p "$stage"
tar -xzf /home/kursadsalis/agca-code.tar.gz -C "$stage"
gzip -dc /home/kursadsalis/crypto_data.db.gz > "$stage/crypto_data.db"

if [[ -d /home/kursadsalis/AG-Crypto-Analyst-cloud ]]; then
  mv /home/kursadsalis/AG-Crypto-Analyst-cloud "$backup"
fi
mv "$stage" /home/kursadsalis/AG-Crypto-Analyst-cloud
cd /home/kursadsalis/AG-Crypto-Analyst-cloud

python3.12 -m venv .venv
.venv/bin/pip install --no-cache-dir -r requirements-runtime.txt -r requirements-forecast-v3.txt
sudo install -d -o kursadsalis -g kursadsalis -m 700 /var/lib/agca

if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

sudo install -m 644 deploy/agca-cloud-worker.service /etc/systemd/system/agca-cloud-worker.service
sudo install -m 644 deploy/agca-paper-realtime-worker.service /etc/systemd/system/agca-paper-realtime-worker.service
sudo install -m 644 deploy/agca-paper-realtime-health-monitor.service /etc/systemd/system/agca-paper-realtime-health-monitor.service
sudo install -m 644 deploy/agca-paper-realtime-health-monitor.timer /etc/systemd/system/agca-paper-realtime-health-monitor.timer
sudo install -m 644 deploy/agca-exchange-metadata-refresh.service /etc/systemd/system/agca-exchange-metadata-refresh.service
sudo install -m 644 deploy/agca-exchange-metadata-refresh.timer /etc/systemd/system/agca-exchange-metadata-refresh.timer
sudo install -m 644 deploy/agca-paper-selection.service /etc/systemd/system/agca-paper-selection.service
sudo install -m 644 deploy/agca-paper-selection.timer /etc/systemd/system/agca-paper-selection.timer
sudo install -m 644 deploy/agca-cloud-attestation.service /etc/systemd/system/agca-cloud-attestation.service
sudo install -m 644 deploy/agca-cloud-attestation.timer /etc/systemd/system/agca-cloud-attestation.timer
sudo install -m 644 deploy/agca-remote-mirror-drain.service /etc/systemd/system/agca-remote-mirror-drain.service
sudo install -m 644 deploy/agca-remote-mirror-drain.timer /etc/systemd/system/agca-remote-mirror-drain.timer
sudo install -m 644 deploy/agca-sqlite-integrity-check.service /etc/systemd/system/agca-sqlite-integrity-check.service
sudo install -m 644 deploy/agca-sqlite-integrity-check.timer /etc/systemd/system/agca-sqlite-integrity-check.timer
sudo systemctl daemon-reload
sudo systemctl enable --now agca-cloud-worker.service
sudo systemctl enable --now agca-paper-realtime-worker.service
sudo systemctl enable --now agca-paper-realtime-health-monitor.timer
sudo systemctl enable --now agca-exchange-metadata-refresh.timer
sudo systemctl enable --now agca-paper-selection.timer
sudo systemctl enable --now agca-cloud-attestation.timer
sudo systemctl enable --now agca-remote-mirror-drain.timer
sudo systemctl enable --now agca-sqlite-integrity-check.timer
echo "BACKUP=$backup"
