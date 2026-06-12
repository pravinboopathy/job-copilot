# job-copilot

A personal pipeline that filters incoming job alerts and tailors a LaTeX resume per role, with LLM truthfulness guardrails. Built for myself, open-sourced as a portfolio reference.

## How it works

The CLI has two ingress modes. `run --source email` reads incoming LinkedIn alert emails from Gmail and extracts the job IDs from the HTML body. `run --source search` queries the LinkedIn guest API directly using saved search definitions in `config.yaml`. Both modes converge on the same downstream pipeline; the Docker cron deployment runs both sources back-to-back every 6 hours.

### Ingestion

```mermaid
flowchart LR
    G[(Gmail<br/>account)] -->|OAuth| GF[Fetch alert<br/>emails]
    GF --> GP[Parse LinkedIn<br/>job IDs]
    C[(config.yaml<br/>search_queries)] -->|per query| LS[LinkedIn<br/>search API]
    LS --> CF[Card filter<br/>title + company<br/>blocklists]
    GP --> D{Dedup}
    CF --> D
    D -->|done| X1([skip])
    D -->|new| LF[Fetch full JD<br/>LinkedIn guest API]
    LF -->|empty| X2([skip])
    LF --> JP([JobPosting])

    classDef store fill:#1e40af,stroke:#93c5fd,color:#fff,stroke-width:2px
    classDef gate fill:#b45309,stroke:#fcd34d,color:#fff,stroke-width:2px
    classDef term fill:#4b5563,stroke:#d1d5db,color:#fff,stroke-width:2px
    classDef action fill:#0f766e,stroke:#5eead4,color:#fff,stroke-width:2px
    classDef handoff fill:#15803d,stroke:#86efac,color:#fff,stroke-width:2px

    class G,C store
    class D gate
    class X1,X2 term
    class GF,GP,LS,CF,LF action
    class JP handoff
```

The card-level filter is a deliberate cost-cutting step: title and company blocklists run on each search-result card *before* any full-JD fetch or LLM call. Surviving cards are then deduped against `processed_jobs.json` so reruns never re-fetch the same posting.

### Per-job tailoring

```mermaid
flowchart LR
    JP([JobPosting]) --> SI[Sanitize JD]
    SI --> EK[Extract<br/>keywords<br/>LLM]
    EK --> TH{Pre-match<br/>≥ threshold?}
    TH -->|no| REC[Record + skip]
    TH -->|yes| TR[Tailor resume<br/>LLM +<br/>truthfulness rules]
    TR --> AP[Scrub AI<br/>phrases]
    AP --> PDF[Compile PDF<br/>1-page enforced]
    PDF --> W[Write files<br/>mark_processed]
    W --> N{--notify?}
    N -->|no| Y([done])
    N -->|yes| EM[Email summary<br/>+ PDFs]

    classDef llm fill:#7e22ce,stroke:#d8b4fe,color:#fff,stroke-width:2px
    classDef gate fill:#b45309,stroke:#fcd34d,color:#fff,stroke-width:2px
    classDef term fill:#4b5563,stroke:#d1d5db,color:#fff,stroke-width:2px
    classDef action fill:#0f766e,stroke:#5eead4,color:#fff,stroke-width:2px
    classDef handoff fill:#15803d,stroke:#86efac,color:#fff,stroke-width:2px

    class EK,TR llm
    class TH,N gate
    class REC,Y term
    class SI,AP,PDF,W,EM action
    class JP handoff
```

Output for each tailored job lands in `data/output/` as three files: the tailored `.tex`, the compiled `.pdf`, and a `_changes.md` report showing pre/post match %, what changed, matched keywords, missing-but-injectable keywords, gaps that cannot be added truthfully, and any AI phrases that were scrubbed.

## Engineering highlights

- **Multi-provider LLM routing.** Wraps LiteLLM's `Router` with explicit retry policies (auth=0, rate-limit=3, timeout=2, internal-server=2) and provider-specific `api_base` normalization. Supports Anthropic, OpenAI, Gemini, OpenRouter, DeepSeek, Ollama. (`src/_vendor/llm.py`)
- **JSON-mode prompting with bounded extraction.** Capability-detects `response_format` support per model via LiteLLM's registry. Custom JSON extractor handles markdown-fenced output, thinking-model tags (`<think>...</think>` from deepseek-r1/qwq), and unbalanced-brace truncation — with recursion depth and content-size limits. Retries on `JSONDecodeError` with progressively higher temperature.
- **Truthfulness guardrails baked into the prompt.** `CRITICAL_TRUTHFULNESS_RULES` (see `src/_vendor/prompts/templates.py`) forbid inventing skills, employers, dates, or accomplishments. Keyword injection requires the keyword to already exist in the base resume — the LLM reorders emphasis, it does not fabricate.
- **AI-phrase scrubber.** Post-processes LLM output against a curated blacklist of overused AI phrasings (`leverage`, `cutting-edge`, `seamlessly`, etc.) with deterministic replacements. (`src/_vendor/prompts/refinement.py`)
- **Deterministic dedup state.** Flat JSON file keyed by LinkedIn job ID, with pre/post match %, output path, and timestamp per job. Filtering happens before LLM calls so reruns cost nothing on already-processed work.
- **Card-level filtering before any fetch.** Title and company blocklists run on LinkedIn search-result cards (word-boundary matches) — strictly a cost-cutting step that prevents wasted LinkedIn JD fetches and LLM calls.
- **Single-page PDF budgeting.** Content-budget enforcement and font-size fallback during LaTeX compilation to keep output to one page regardless of how much the LLM produces.
- **Docker + cron deployment.** Compose file with bind-mounted state, host-side cron wrapper that runs both email and search sources back-to-back.

## Responsible use

This tool tailors candidate resumes; the human applies. It does not auto-submit applications.

- The LLM is constrained by `CRITICAL_TRUTHFULNESS_RULES`. It cannot invent skills, employers, dates, or accomplishments that aren't already in the source resume.
- Keyword injection requires the keyword to exist somewhere in the source — the prompt reorders emphasis to surface relevant content, it does not fabricate new content.
- Every generated PDF and its `_changes.md` report is reviewed before the human submits anything. The pipeline produces candidates, not submissions.
- LinkedIn data is fetched only from the unauthenticated `jobs-guest` endpoint — the same JSON a logged-out browser sees on `linkedin.com/jobs/view/{id}`. The `linkedin.request_delay_seconds` setting (default 3s) paces requests.
- `tailoring.enable_ai_phrase_removal` scrubs common AI-generated phrasings so the output reads as the user's own writing, not as obvious LLM output.

## Setup

See [`DEPLOY.md`](DEPLOY.md) for the full local and Docker deployment guide.

Short version:

```bash
uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env                                # add API key
cp config/config.yaml.example config/config.yaml    # set queries, model, email
cp config/credentials.json.example config/credentials.json   # replace with real Gmail OAuth creds
# Put your LaTeX resume at resume/base_resume.tex
python -m src.cli test-gmail
python -m src.cli run --source search --dry-run
```

## Layout

```
src/
├── cli.py              Click CLI entrypoint
├── pipeline.py         Orchestrator: JD → tailor → compile → report
├── linkedin_client.py  Guest-API client + card-level filter
├── gmail_client.py     OAuth + alert fetch + send-results
├── email_parser.py     Parse LinkedIn alert HTML into job references
├── resume_tailor.py    LLM prompt building, truthfulness rules application
├── adapters.py         Keyword extraction, AI-phrase scrub, match scoring
├── pdf_compiler.py     pdflatex wrapper
├── state.py            Dedup tracker (processed_jobs.json)
├── models.py           Dataclasses
└── _vendor/            Vendored LLM + prompt modules from the original Resume
                        Matcher backend (trimmed; see _vendor/llm.py for notes)
```

## Status

Personal tool, actively used. Open-sourced as a portfolio reference — not packaged for general distribution. Issues and PRs are welcome but I may not respond quickly.

## Background

This started as `tools/job-tailor/` inside a fork of [Resume Matcher](https://github.com/srbhr/Resume-Matcher). Only the LLM wrapper, the keyword-extraction prompt, the truthfulness rules, and the AI-phrase blacklist were ever used from the upstream backend — those are vendored under `src/_vendor/`. The rest of the fork (FastAPI app, Next.js frontend, dashboard) is unrelated to this tool.
