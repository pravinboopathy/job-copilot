# Job Tailor — Deployment Guide

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [Surfshark CLI](https://surfshark.com/download/linux) (VPN for LinkedIn scraping)
- pdflatex (`brew install basictex` on macOS, `apt install texlive` on Linux)
- Anthropic API key

## Environment Setup

```bash
cd tools/job-tailor
uv venv --python 3.13
source .venv/bin/activate
uv pip install -e ../../apps/backend
uv pip install -r requirements.txt
```

## Configuration

### `.env`

```bash
cp .env.example .env
# Edit .env with your API key
```

```env
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### `config.yaml`

Edit `config/config.yaml` to adjust:
- `linkedin.search_queries` — keywords, location, time filter
- `llm.model` — which Claude model to use
- `tailoring.min_match_threshold` — minimum keyword match % to tailor (default 15)
- `tailoring.strategy` — "nudge", "keywords", or "full"

### Gmail OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → enable **Gmail API**
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
4. Application type: **Desktop app**
5. Download JSON → save as `config/credentials.json`
6. Go to **OAuth consent screen → Test users** → add your Gmail address

First run opens a browser for consent:

```bash
python -m src.cli test-gmail
```

This saves `config/token.json` for future non-interactive use.

### Resume

Place your base LaTeX resume at:

```
resume/base_resume.tex
```

## Surfshark VPN

Connect before scraping LinkedIn to avoid rate limiting:

```bash
surfshark-cli connect
# Verify: curl ifconfig.me
```

All requests from the CLI are automatically routed through the VPN tunnel.

## Running

```bash
# Test Gmail connection
python -m src.cli test-gmail

# Test LinkedIn fetch
python -m src.cli test-linkedin <job_id>

# Dry run — list jobs without tailoring
python -m src.cli run --source email --dry-run

# Process jobs from Gmail alerts
python -m src.cli run --source email --limit 5

# Process a single job by ID
python -m src.cli job <job_id>

# Search LinkedIn directly
python -m src.cli run --source search --limit 5

# Check processing stats
python -m src.cli status
```

Output goes to `data/output/`:
- `{Company}_{Title}_{Date}.tex` — tailored LaTeX
- `{Company}_{Title}_{Date}.pdf` — compiled PDF
- `{Company}_{Title}_{Date}_changes.md` — analysis, changes, keyword report

## Best Practices

- **VPN first** — always `surfshark-cli connect` before running with LinkedIn sources
- **Rate limiting** — the CLI enforces a 3s delay between LinkedIn requests (configurable in `config.yaml`)
- **Check output** — review `_changes.md` reports to verify tailoring quality
- **Dedup** — the CLI tracks processed jobs in `data/processed_jobs.json`; rerunning skips already-processed jobs

---

## Cron Job + Email Notifications

Automate the pipeline to run every 6 hours and email results with PDFs and apply links.

### 1. Enable Gmail Send Scope

The `gmail.send` scope is already configured in the code. Delete your existing token to re-auth with the new scope:

```bash
rm config/token.json
python -m src.cli test-gmail
```

This opens a browser — grant both read and send permissions.

### 2. Configure Notification Email

Already set in `config/config.yaml`:

```yaml
notification:
  email: "boopathypravin@gmail.com"
```

### 3. Test Email Notification

```bash
python -m src.cli run --source email --limit 1 --notify
```

You should receive an email with:
- HTML table: Job Title, Company, Location, Match %, Apply link
- PDF attachments for each tailored resume

### 4. Install Cron Job

```bash
crontab -e
```

Add this line (runs every 6 hours):

```
0 */6 * * * /Users/pravinboopathy/projects/resume_tailor/tools/job-tailor/scripts/cron_run.sh
```

The cron wrapper script (`scripts/cron_run.sh`) activates the environment, optionally connects VPN, runs the pipeline with `--notify`, and logs output to `data/cron.log`.

### 5. Verify

```bash
# Check cron is installed
crontab -l

# Check logs after a run
tail -50 data/cron.log
```

---

## Future: Docker Deployment

> Not yet implemented. Documented here for future reference.

### Dockerfile

```dockerfile
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-latex-base texlive-fonts-recommended \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY apps/backend /app/apps/backend
COPY tools/job-tailor /app/tools/job-tailor

RUN pip install -e /app/apps/backend \
    && pip install -r /app/tools/job-tailor/requirements.txt

WORKDIR /app/tools/job-tailor
ENTRYPOINT ["python", "-m", "src.cli"]
```

### Build & Run

```bash
# Build from repo root
docker build -t job-tailor -f tools/job-tailor/Dockerfile .

# Run with mounted config and resume
docker run --env-file tools/job-tailor/.env \
  -v $(pwd)/tools/job-tailor/config:/app/tools/job-tailor/config \
  -v $(pwd)/tools/job-tailor/resume:/app/tools/job-tailor/resume \
  -v $(pwd)/tools/job-tailor/data:/app/tools/job-tailor/data \
  job-tailor run --source email --limit 5
```

Gmail `token.json` must be generated locally first (requires browser for OAuth), then mounted into the container via the config volume.

For VPN in Docker, use `--network host` with Surfshark CLI running on the host, or configure a SOCKS5 proxy.

---

## Future: Automated Pipeline (Optional)

> Further automation beyond the current cron + email setup.

### Integration with Resume Matcher Web App

The tailored `.tex` files and keyword reports could be imported into the Resume Matcher web app:
- Auto-create resume entries from tailored output
- Display keyword match scores on the dashboard
- Track application status per job

### Full Automation Vision

```
LinkedIn alert email
  → Gmail API fetch
  → Parse job IDs
  → Fetch JDs (via VPN)
  → Extract keywords (LLM)
  → Tailor resume (LLM)
  → Clean AI phrases
  → Compile PDF
  → [Optional] Auto-submit application
  → [Optional] Track in Resume Matcher dashboard
```
