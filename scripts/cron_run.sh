#!/bin/bash
# Cron wrapper for Job Tailor pipeline
# Runs every 6 hours: 0 */6 * * * /path/to/scripts/cron_run.sh

set -e

cd /Users/pravinboopathy/projects/resume_tailor/tools/job-tailor

# Activate environment
source .venv/bin/activate 2>/dev/null || conda activate job-tailor 2>/dev/null

# Connect VPN (optional — comment out if not using Surfshark)
# surfshark-cli connect 2>/dev/null

# Run pipeline with email notification
python -m src.cli run --source email --notify 2>&1 | tee -a data/cron.log

# Disconnect VPN
# surfshark-cli disconnect 2>/dev/null
