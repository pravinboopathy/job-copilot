"""Click CLI entrypoint for Job Tailor."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import click
import yaml
from dotenv import load_dotenv

from app.llm import LLMConfig

# Load .env from the job-tailor directory
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _load_config(config_path: str) -> dict[str, Any]:
    """Load YAML config file."""
    path = Path(config_path)
    if not path.exists():
        click.echo(f"Config file not found: {path}", err=True)
        sys.exit(1)
    return yaml.safe_load(path.read_text())


def _build_llm_config(config: dict[str, Any]) -> LLMConfig:
    """Build LLMConfig from CLI config + environment variables."""
    llm = config.get("llm", {})
    provider = llm.get("provider", "anthropic")

    # Resolve API key from environment
    env_key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    env_var = env_key_map.get(provider, f"{provider.upper()}_API_KEY")
    api_key = os.environ.get(env_var, "")

    if not api_key:
        click.echo(f"Warning: {env_var} not set. LLM calls will fail.", err=True)

    return LLMConfig(
        provider=provider,
        model=llm.get("model", "claude-sonnet-4-20250514"),
        api_key=api_key,
    )


@click.group()
@click.option(
    "--config", "-c",
    default="config/config.yaml",
    help="Path to config file",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable debug logging",
)
@click.pass_context
def cli(ctx: click.Context, config: str, verbose: bool) -> None:
    """Job-Tailor: Monitor job alerts and tailor your LaTeX resume."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["config"] = _load_config(config)


@cli.command()
@click.option(
    "--source",
    type=click.Choice(["email", "search"]),
    default="email",
    help="Job source: Gmail alerts or LinkedIn search",
)
@click.option("--limit", "-n", default=None, type=int, help="Max jobs to process")
@click.option("--dry-run", is_flag=True, help="Fetch JDs without tailoring")
@click.pass_context
def run(ctx: click.Context, source: str, limit: int | None, dry_run: bool) -> None:
    """Process new jobs from Gmail alerts or LinkedIn search."""
    config = ctx.obj["config"]
    asyncio.run(_run_pipeline(config, source, limit, dry_run))


async def _run_pipeline(
    config: dict[str, Any],
    source: str,
    limit: int | None,
    dry_run: bool,
) -> None:
    from .models import JobPosting

    jobs: list[JobPosting] = []

    if source == "email":
        jobs = await _get_jobs_from_email(config, limit)
    elif source == "search":
        jobs = _get_jobs_from_search(config, limit)

    if not jobs:
        click.echo("No jobs found.")
        return

    click.echo(f"Found {len(jobs)} jobs to process")

    if dry_run:
        for job in jobs:
            click.echo(f"  - {job.title} at {job.company} ({job.job_id})")
        return

    from .pipeline import run_pipeline

    llm_config = _build_llm_config(config)
    results = await run_pipeline(jobs, config, llm_config)

    click.echo(f"\nDone. Processed {len(results)} jobs.")
    for r in results:
        click.echo(f"  {r.job.title} at {r.job.company}: {r.pre_match:.0f}% → {r.post_match:.0f}%")


async def _get_jobs_from_email(
    config: dict[str, Any],
    limit: int | None,
) -> list:
    """Fetch jobs from Gmail alerts → LinkedIn."""
    from .email_parser import parse_linkedin_alert
    from .gmail_client import GmailClient
    from .linkedin_client import LinkedInClient
    from .models import JobPosting

    gmail_cfg = config.get("gmail", {})
    gmail = GmailClient(
        credentials_path=gmail_cfg.get("credentials_path", "config/credentials.json"),
        token_path=gmail_cfg.get("token_path", "config/token.json"),
    )
    gmail.authenticate()

    query = gmail_cfg.get("query", "from:jobs-noreply@linkedin.com newer_than:1d")
    max_results = gmail_cfg.get("max_results", 20)
    emails = gmail.fetch_alert_emails(query=query, max_results=max_results)

    # Parse job references from all emails
    from .models import JobReference

    all_refs: list[JobReference] = []
    for email in emails:
        refs = parse_linkedin_alert(email["body_html"])
        all_refs.extend(refs)

    click.echo(f"Found {len(all_refs)} job references from {len(emails)} emails")

    if limit:
        all_refs = all_refs[:limit]

    # Fetch full JDs
    linkedin_cfg = config.get("linkedin", {})
    linkedin = LinkedInClient(
        request_delay=linkedin_cfg.get("request_delay_seconds", 3),
    )

    jobs: list[JobPosting] = []
    for ref in all_refs:
        try:
            job = linkedin.fetch_job(ref.job_id)
            if not job.description:
                click.echo(f"  Warning: empty JD for {ref.job_id}, skipping")
                continue
            jobs.append(job)
        except Exception as e:
            click.echo(f"  Failed to fetch job {ref.job_id}: {e}")
            continue

    return jobs


def _get_jobs_from_search(
    config: dict[str, Any],
    limit: int | None,
) -> list:
    """Search for jobs via LinkedIn guest API."""
    from .linkedin_client import LinkedInClient
    from .models import JobPosting

    linkedin_cfg = config.get("linkedin", {})
    linkedin = LinkedInClient(
        request_delay=linkedin_cfg.get("request_delay_seconds", 3),
    )

    queries = linkedin_cfg.get("search_queries", [])
    if not queries:
        click.echo("No search queries configured in config.yaml", err=True)
        return []

    all_jobs: list[JobPosting] = []
    for q in queries:
        search_results = linkedin.search_jobs(
            keywords=q.get("keywords", ""),
            location=q.get("location", "United States"),
            time_filter=q.get("time_filter", "r86400"),
        )
        # Fetch full JDs for search results (they only have metadata)
        for result in search_results:
            try:
                job = linkedin.fetch_job(result.job_id)
                if job.description:
                    all_jobs.append(job)
            except Exception as e:
                click.echo(f"  Failed to fetch job {result.job_id}: {e}")
                continue

    if limit:
        all_jobs = all_jobs[:limit]

    return all_jobs


@cli.command()
@click.argument("job_id")
@click.pass_context
def job(ctx: click.Context, job_id: str) -> None:
    """Process a single LinkedIn job by ID."""
    config = ctx.obj["config"]
    asyncio.run(_process_single_job(config, job_id))


async def _process_single_job(config: dict[str, Any], job_id: str) -> None:
    from .linkedin_client import LinkedInClient
    from .pipeline import process_single_job
    from .state import ProcessedJobsState

    click.echo(f"Fetching job {job_id}...")
    linkedin_cfg = config.get("linkedin", {})
    linkedin = LinkedInClient(
        request_delay=linkedin_cfg.get("request_delay_seconds", 3),
    )
    job_posting = linkedin.fetch_job(job_id)

    if not job_posting.description:
        click.echo("Error: empty job description", err=True)
        return

    click.echo(f"Job: {job_posting.title} at {job_posting.company}")

    llm_config = _build_llm_config(config)
    state_path = config.get("state", {}).get("path", "data/processed_jobs.json")
    state = ProcessedJobsState(state_path)
    base_tex_path = config.get("resume", {}).get("base_tex_path", "resume/base_resume.tex")
    base_tex = Path(base_tex_path).read_text(encoding="utf-8")

    result = await process_single_job(
        job_posting, base_tex, config, llm_config, state
    )

    if result:
        click.echo(f"\nMatch: {result.pre_match:.0f}% → {result.post_match:.0f}%")
        click.echo(f"Output: {result.tex_path}")
    else:
        click.echo("Job was skipped (already processed or below threshold)")


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show processing statistics."""
    from .state import ProcessedJobsState

    config = ctx.obj["config"]
    state_path = config.get("state", {}).get("path", "data/processed_jobs.json")
    state = ProcessedJobsState(state_path)
    stats = state.get_stats()

    click.echo("Processing Statistics")
    click.echo(f"  Total processed: {stats['total_processed']}")
    click.echo(f"  Tailored:        {stats['tailored']}")
    click.echo(f"  Skipped:         {stats['skipped']}")


@cli.command("test-gmail")
@click.pass_context
def test_gmail(ctx: click.Context) -> None:
    """Test Gmail connection and list recent LinkedIn alert emails."""
    from .gmail_client import GmailClient

    config = ctx.obj["config"]
    gmail_cfg = config.get("gmail", {})

    gmail = GmailClient(
        credentials_path=gmail_cfg.get("credentials_path", "config/credentials.json"),
        token_path=gmail_cfg.get("token_path", "config/token.json"),
    )

    click.echo("Authenticating with Gmail...")
    gmail.authenticate()
    click.echo("Authentication successful!")

    query = gmail_cfg.get("query", "from:jobs-noreply@linkedin.com newer_than:1d")
    click.echo(f"Searching: {query}")
    emails = gmail.fetch_alert_emails(query=query, max_results=5)

    click.echo(f"\nFound {len(emails)} emails:")
    for email in emails:
        click.echo(f"  - {email['subject']}")


@cli.command("test-linkedin")
@click.argument("job_id")
@click.pass_context
def test_linkedin(ctx: click.Context, job_id: str) -> None:
    """Fetch and display a single LinkedIn job description."""
    from .linkedin_client import LinkedInClient

    config = ctx.obj["config"]
    linkedin_cfg = config.get("linkedin", {})

    linkedin = LinkedInClient(
        request_delay=linkedin_cfg.get("request_delay_seconds", 3),
    )

    click.echo(f"Fetching job {job_id}...")
    job_posting = linkedin.fetch_job(job_id)

    click.echo(f"\nTitle:    {job_posting.title}")
    click.echo(f"Company:  {job_posting.company}")
    click.echo(f"Location: {job_posting.location or 'N/A'}")
    click.echo(f"Salary:   {job_posting.salary or 'N/A'}")
    click.echo(f"URL:      {job_posting.url}")
    click.echo(f"\nDescription ({len(job_posting.description)} chars):")
    click.echo(job_posting.description[:500])
    if len(job_posting.description) > 500:
        click.echo(f"... ({len(job_posting.description) - 500} more chars)")


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
