"""Pipeline orchestrator — wires all steps from JD to PDF."""

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from app.llm import LLMConfig

from .adapters import (
    analyze_keyword_gaps_text,
    calculate_keyword_match_text,
    extract_keywords,
    remove_ai_phrases_text,
)
from .models import JobPosting, TailorResult
from .pdf_compiler import compile_to_pdf, get_page_count
from .resume_tailor import tailor_resume
from .state import ProcessedJobsState

logger = logging.getLogger(__name__)


def _sanitize_filename(text: str) -> str:
    """Convert text to a filesystem-safe string."""
    safe = text.replace(" ", "_")
    return "".join(c for c in safe if c.isalnum() or c in ("_", "-"))


def _build_output_prefix(job: JobPosting, config: dict[str, Any]) -> str:
    """Build the output filename prefix from config pattern."""
    pattern = config.get("output", {}).get("filename_pattern", "{company}_{job_title}_{date}")
    return pattern.format(
        company=_sanitize_filename(job.company),
        job_title=_sanitize_filename(job.title),
        date=date.today().isoformat(),
    )


def _write_changes_report(
    path: Path,
    job: JobPosting,
    result: TailorResult,
) -> None:
    """Write the _changes.md report file."""
    report = result.keyword_report
    matched = result.matched_keywords
    total_kw = len(matched) + len(report.get("missing_keywords", []))

    lines = [
        f"# {job.title} at {job.company}",
        "",
        f"**Apply:** {job.url}",
        f"**Salary:** {job.salary or 'Not specified'}",
        f"**Pre-tailor match:** {result.pre_match:.0f}% → **Post-tailor match:** {result.post_match:.0f}%",
        f"**Potential (with all injectable keywords):** {result.potential_match:.0f}%",
        "",
        "## Analysis",
        result.analysis,
        "",
        "## Changes Made",
        result.changes,
        "",
        "## Keyword Report",
        "",
        f"### Matched ({len(matched)} of {total_kw} keywords):",
        ", ".join(sorted(matched)) if matched else "(none)",
        "",
    ]

    injectable = report.get("injectable_keywords", [])
    if injectable:
        lines.append("### Missing but injectable (in your resume, not yet reflected):")
        for kw in sorted(injectable):
            lines.append(f"- {kw}")
        lines.append("")

    non_injectable = report.get("non_injectable_keywords", [])
    if non_injectable:
        lines.append("### Gaps (not in your resume — cannot add truthfully):")
        for kw in sorted(non_injectable):
            lines.append(f"- {kw}")
        lines.append("")

    if result.removed_phrases:
        lines.append("### AI phrases cleaned:")
        for phrase in sorted(result.removed_phrases):
            lines.append(f"- \"{phrase}\"")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _compute_matched_keywords(
    jd_keywords: dict[str, Any],
    text: str,
) -> list[str]:
    """Return the list of JD keywords found in the text."""
    from .adapters import _collect_keywords, keyword_in_text

    all_kw = _collect_keywords(jd_keywords)
    return [kw for kw in all_kw if keyword_in_text(kw, text)]


async def process_single_job(
    job: JobPosting,
    base_tex: str,
    config: dict[str, Any],
    llm_config: LLMConfig,
    state: ProcessedJobsState,
) -> TailorResult | None:
    """Process a single job through the full pipeline.

    Returns None if skipped (already processed or below threshold).
    """
    # 1. Dedup
    if state.is_processed(job.job_id):
        logger.info("Skipping %s at %s: already processed", job.title, job.company)
        return None

    print(f"\n{'='*60}")
    print(f"Processing: {job.title} at {job.company}")
    print(f"{'='*60}")

    # 2. Extract keywords (LLM call)
    print("  Extracting keywords...")
    jd_keywords = await extract_keywords(job.description, llm_config)
    logger.info("Extracted keywords: %s", list(jd_keywords.keys()))

    # 3. Pre-tailor match (deterministic)
    pre_match = calculate_keyword_match_text(base_tex, jd_keywords)
    print(f"  Pre-tailor match: {pre_match:.0f}%")

    # 4. Threshold gate
    min_threshold = config.get("tailoring", {}).get("min_match_threshold", 30)
    if pre_match < min_threshold:
        print(f"  Skipping: {pre_match:.0f}% match (below {min_threshold}%)")
        state.mark_processed(job.job_id, {
            "skipped": True,
            "skip_reason": "low_match",
            "pre_match": pre_match,
            "title": job.title,
            "company": job.company,
        })
        return None

    # 5. Tailor (LLM call)
    print("  Tailoring resume...")
    tailor_output = await tailor_resume(base_tex, job, jd_keywords, config, llm_config)

    # 6. AI phrase removal (deterministic)
    tailored_tex = tailor_output.tailored_tex
    removed_phrases: list[str] = []
    if config.get("tailoring", {}).get("enable_ai_phrase_removal", True):
        tailored_tex, removed_phrases = remove_ai_phrases_text(
            tailored_tex, job.description
        )
        if removed_phrases:
            print(f"  Cleaned {len(removed_phrases)} AI phrases")

    # 7. Post-tailor analysis (deterministic)
    post_analysis = analyze_keyword_gaps_text(jd_keywords, tailored_tex, base_tex)
    post_match = post_analysis["current_match_percentage"]
    potential_match = post_analysis["potential_match_percentage"]
    print(f"  Post-tailor match: {post_match:.0f}% (potential: {potential_match:.0f}%)")

    # Build output paths
    output_dir = Path(config.get("output", {}).get("directory", "data/output"))
    prefix = _build_output_prefix(job, config)
    tex_path = output_dir / f"{prefix}.tex"
    pdf_path = output_dir / f"{prefix}.pdf"
    report_path = output_dir / f"{prefix}_changes.md"

    # 8. Write .tex
    output_dir.mkdir(parents=True, exist_ok=True)
    tex_path.write_text(tailored_tex, encoding="utf-8")
    print(f"  Saved: {tex_path}")

    # 9. Compile PDF + 1-page enforcement (font size fallback)
    compiled_pdf = compile_to_pdf(tailored_tex, pdf_path)
    if compiled_pdf:
        page_count = get_page_count(compiled_pdf)
        if page_count > 1:
            print(f"  Resume is {page_count} pages — reducing font size to 10pt...")
            tailored_tex = re.sub(
                r"\\documentclass\[11pt\]", r"\\documentclass[10pt]", tailored_tex
            )
            tex_path.write_text(tailored_tex, encoding="utf-8")
            compiled_pdf = compile_to_pdf(tailored_tex, pdf_path)
            if compiled_pdf:
                retry_pages = get_page_count(compiled_pdf)
                if retry_pages > 1:
                    print(f"  Warning: still {retry_pages} pages after font reduction")
                else:
                    print(f"  Font reduction successful — 1 page")
        print(f"  Saved: {compiled_pdf or pdf_path}")
    else:
        print("  PDF compilation skipped (pdflatex not available)")

    # Compute matched keywords for the report
    matched_keywords = _compute_matched_keywords(jd_keywords, tailored_tex)

    result = TailorResult(
        job=job,
        tailored_tex=tailored_tex,
        analysis=tailor_output.analysis,
        changes=tailor_output.changes,
        pre_match=pre_match,
        post_match=post_match,
        potential_match=potential_match,
        keyword_report=post_analysis,
        removed_phrases=removed_phrases,
        pdf_path=str(compiled_pdf) if compiled_pdf else None,
        tex_path=str(tex_path),
        report_path=str(report_path),
        matched_keywords=matched_keywords,
    )

    # 10. Write report
    _write_changes_report(report_path, job, result)
    print(f"  Saved: {report_path}")

    # 11. Mark processed
    state.mark_processed(job.job_id, {
        "title": job.title,
        "company": job.company,
        "pre_match": pre_match,
        "post_match": post_match,
        "potential_match": potential_match,
        "tex_path": str(tex_path),
    })

    return result


async def run_pipeline(
    jobs: list[JobPosting],
    config: dict[str, Any],
    llm_config: LLMConfig,
) -> list[TailorResult]:
    """Process a batch of jobs sequentially.

    Sequential (not concurrent) because of LLM and LinkedIn rate limits.
    """
    state_path = config.get("state", {}).get("path", "data/processed_jobs.json")
    state = ProcessedJobsState(state_path)
    base_tex_path = config.get("resume", {}).get("base_tex_path", "resume/base_resume.tex")

    base_tex = Path(base_tex_path).read_text(encoding="utf-8")

    results: list[TailorResult] = []
    for job in jobs:
        try:
            result = await process_single_job(job, base_tex, config, llm_config, state)
            if result:
                results.append(result)
        except Exception as e:
            logger.error("Failed to process %s at %s: %s", job.title, job.company, e)
            print(f"  ERROR: {e}")
            continue

    return results
