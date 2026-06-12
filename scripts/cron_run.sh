#!/bin/bash
# Cron wrapper for Job Tailor pipeline
# Runs every 6 hours: 0 */6 * * * /path/to/scripts/cron_run.sh

set -e

cd "$(dirname "$0")/.."

# Activate environment
source .venv/bin/activate 2>/dev/null || conda activate job-copilot 2>/dev/null

# Connect VPN (optional — comment out if not using Surfshark)
# surfshark-cli connect 2>/dev/null

# Run pipeline with email notification
python -m src.cli run --source email --notify 2>&1 | tee -a data/cron.log

# Discover fresh postings from LinkedIn search (uses linkedin.search_queries
# in config/config.yaml; tune filters/max_results there to control volume).
python -m src.cli run --source search --notify 2>&1 | tee -a data/cron.log

# Disconnect VPN
# surfshark-cli disconnect 2>/dev/null
