#!/bin/bash
# Host-side cron entry for the Docker deployment on docker-jobs.
# Runs both job sources back-to-back through `docker compose run --rm`.
# Each invocation brings up WireGuard inside the container, runs the CLI,
# and tears VPN down. State (data/processed_jobs.json) persists across
# runs via the bind mount declared in docker-compose.yml.
#
# Install on docker-jobs:
#   crontab -e
#   0 8 * * * /home/deploy/job-copilot/scripts/docker_cron.sh
#
# (The companion scripts/cron_run.sh is for the local-venv deployment.)
set -e

cd "$(dirname "$0")/.."

LOG=data/cron.log
mkdir -p data

{
  echo
  echo "=== $(date -Is) cron run start ==="
} >> "$LOG"

docker compose run --rm job-copilot run --source email  --notify >> "$LOG" 2>&1
docker compose run --rm job-copilot run --source search --notify >> "$LOG" 2>&1

echo "=== $(date -Is) cron run end ===" >> "$LOG"
