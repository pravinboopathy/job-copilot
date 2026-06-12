# Deployment Guide

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) or `pip` (uv is faster)
- `pdflatex` (`brew install basictex` on macOS, `apt install texlive-latex-base texlive-latex-extra texlive-fonts-recommended` on Debian/Ubuntu)
- An API key for one supported LLM provider (Anthropic, OpenAI, Gemini, OpenRouter, DeepSeek, or a local Ollama server)

## Local Setup

```bash
git clone <repo-url> job-copilot
cd job-copilot

uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements.txt

cp .env.example .env
cp config/config.yaml.example config/config.yaml
cp config/credentials.json.example config/credentials.json
```

Then:

1. Edit `.env` and set the API key matching `llm.provider` in `config.yaml`.
2. Edit `config/config.yaml` — set your search queries, LLM model, notification email.
3. Replace `config/credentials.json` with real Gmail OAuth credentials (see "Gmail OAuth" below).
4. Place your LaTeX resume at `resume/base_resume.tex`.

## Gmail OAuth

The Gmail integration reads incoming LinkedIn alert emails and (optionally) sends result notifications. You need OAuth credentials for a Google Cloud project you own:

1. [Google Cloud Console](https://console.cloud.google.com/) → create a project
2. Enable the **Gmail API**
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
4. Application type: **Desktop app**
5. Download the JSON and save it as `config/credentials.json`
6. **OAuth consent screen → Test users** → add your Gmail address

First run opens a browser for consent:

```bash
python -m src.cli test-gmail
```

This saves `config/token.json` for future non-interactive runs.

## Running

```bash
# Test connections
python -m src.cli test-gmail
python -m src.cli test-linkedin <job_id>

# Dry run — list jobs without tailoring
python -m src.cli run --source email --dry-run

# Process jobs from incoming Gmail alerts
python -m src.cli run --source email --limit 5

# Process a single job by LinkedIn ID
python -m src.cli job <job_id>

# Search LinkedIn directly (uses config.yaml search_queries)
python -m src.cli run --source search --limit 5

# Show processing stats
python -m src.cli status
```

Output for each tailored job goes to `data/output/`:

- `{Company}_{Title}_{Date}.tex` — tailored LaTeX
- `{Company}_{Title}_{Date}.pdf` — compiled PDF
- `{Company}_{Title}_{Date}_changes.md` — analysis: keyword match %, what changed, gap report

The CLI tracks processed jobs in `data/processed_jobs.json` and skips already-processed IDs on rerun. Review the `_changes.md` report and the PDF before deciding whether to apply.

## Automated runs (cron)

For local-venv deployment, install the included wrapper:

```bash
crontab -e
# Append (runs every 6 hours):
0 */6 * * * /absolute/path/to/job-copilot/scripts/cron_run.sh
```

`scripts/cron_run.sh` activates the venv, runs the pipeline with `--notify`, and logs to `data/cron.log`. The notification email is sent from your Gmail account (the OAuth scope includes `gmail.send`) to the address in `config.notification.email`.

## Docker Deployment

The included `Dockerfile` and `docker-compose.yml` build a container with `texlive` and the CLI. Bind-mounts pass `config/`, `resume/`, and `data/` into the container so state and credentials live on the host.

```bash
docker compose build
docker compose run --rm job-copilot status
docker compose run --rm job-copilot run --source email --notify
```

For scheduled docker runs, `scripts/docker_cron.sh` is a host-side wrapper:

```bash
crontab -e
0 */6 * * * /absolute/path/to/job-copilot/scripts/docker_cron.sh
```

## Configuration reference

See `config/config.yaml.example` for the full set of options with inline comments. Key knobs:

- `linkedin.search_queries` — list of LinkedIn search definitions (keywords, location, time filter, experience/workplace/job-type filters, title and company blocklists)
- `linkedin.request_delay_seconds` — delay between LinkedIn requests (default 3s) to respect rate limits
- `llm.provider` / `llm.model` — which LLM backend to call
- `tailoring.strategy` — `full` (rewrite emphasis), `nudge` (lighter touch), or `keywords` (keyword injection only)
- `tailoring.min_match_threshold` — minimum keyword-match % required before running the tailoring step. Jobs below this are recorded in dedup state but not tailored
- `tailoring.enable_ai_phrase_removal` — scrub common AI-generated phrasing (see `src/_internal/prompts/refinement.py` for the blacklist)
