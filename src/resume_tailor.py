"""LLM-based resume tailoring using Resume Matcher's LiteLLM wrapper."""

import logging
import re
from typing import Any

from app.llm import LLMConfig, complete
from app.prompts.templates import CRITICAL_TRUTHFULNESS_RULES

from .adapters import sanitize_input
from .models import JobPosting, TailorOutput

logger = logging.getLogger(__name__)


def _count_content_budget(base_tex: str) -> str:
    """Parse base LaTeX to compute a content budget string for the LLM prompt.

    Counts \\item entries, skills lines, and position/project entries so the LLM
    knows the exact content capacity of a single page.
    """
    item_count = len(re.findall(r"\\item\b", base_tex))
    skills_lines = len(re.findall(r"\\textbf\{[^}]*:\}", base_tex))

    # Count position/project entries (lines with \textbf{...} followed by \hfill)
    entry_count = len(re.findall(r"\\textbf\{.*?\}.*\\hfill", base_tex))

    lines = [
        "CONTENT BUDGET — the base resume fits exactly 1 page with these counts. Do NOT exceed them:",
        f"- Total \\item bullet points: {item_count}",
        f"- Position/project entries: {entry_count}",
        f"- Skills lines: {skills_lines}",
        "- You may rephrase, reorder, or replace bullets but NOT add extras.",
        "- Do NOT add new position or project entries.",
        "- Keep each bullet point to 2 lines of text maximum.",
    ]
    return "\n".join(lines)


def build_system_prompt(strategy: str = "full") -> str:
    """Build the system prompt with truthfulness rules and output format.

    Args:
        strategy: Tailoring strategy — "nudge", "keywords", or "full".
    """
    truthfulness_rules = CRITICAL_TRUTHFULNESS_RULES[strategy]

    return f"""You are an expert resume editor specializing in LaTeX resumes.
You will receive a base LaTeX resume and a job description with extracted keywords.
Your task is to tailor the resume for the job while preserving LaTeX formatting.

{truthfulness_rules}

OUTPUT FORMAT:
You must output exactly three sections, separated by these delimiters:

---ANALYSIS---
[Your analysis of the job and how the candidate's experience aligns]

---LATEX---
[The complete tailored LaTeX resume - must compile with pdflatex]

---CHANGES---
[Bullet list of specific changes you made and why]

IMPORTANT:
- The resume MUST fit on exactly ONE page. Do not add content that would push it to a second page.
- Do NOT add more bullet points than exist in the original resume. You may replace bullets but not increase the total count.
- If you need to add keywords, rephrase existing bullets rather than adding new ones.
- Prefer concise phrasing. Remove filler to make room for relevant keywords.
- Preserve ALL LaTeX commands, environments, and formatting
- Do not add \\usepackage commands or modify the preamble
- Every \\item, \\section, \\textbf etc. must remain syntactically valid
- Do NOT add any skill, technology, or achievement not present in the original resume"""


def _format_keywords(jd_keywords: dict[str, Any]) -> str:
    """Format extracted keywords into a readable section for the prompt."""
    sections: list[str] = []

    for key, label in [
        ("required_skills", "Required Skills"),
        ("preferred_skills", "Preferred Skills"),
        ("keywords", "Additional Keywords"),
        ("experience_requirements", "Experience Requirements"),
        ("education_requirements", "Education Requirements"),
        ("key_responsibilities", "Key Responsibilities"),
    ]:
        items = jd_keywords.get(key, [])
        if items:
            formatted = ", ".join(str(i) for i in items)
            sections.append(f"**{label}:** {formatted}")

    seniority = jd_keywords.get("seniority_level", "")
    years = jd_keywords.get("experience_years", "")
    if seniority:
        sections.append(f"**Seniority Level:** {seniority}")
    if years:
        sections.append(f"**Experience Years:** {years}")

    return "\n".join(sections)


def build_user_prompt(
    base_tex: str,
    job: JobPosting,
    jd_keywords: dict[str, Any],
) -> str:
    """Build the user message with sanitized JD and structured keywords."""
    sanitized_jd = sanitize_input(job.description)
    keywords_section = _format_keywords(jd_keywords)
    content_budget = _count_content_budget(base_tex)

    return f"""## Job Details
**Title:** {job.title}
**Company:** {job.company}
**URL:** {job.url}

## Job Description
{sanitized_jd}

## Extracted Keywords
{keywords_section}

## {content_budget}

## Base Resume (LaTeX)
```latex
{base_tex}
```

Tailor this resume for the job above. Follow the output format specified in your instructions."""


def parse_tailor_output(raw: str) -> TailorOutput:
    """Parse the delimiter-separated LLM output.

    Expected format:
        ---ANALYSIS---
        ...analysis text...
        ---LATEX---
        ...complete LaTeX...
        ---CHANGES---
        ...bullet list of changes...
    """
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    for line in raw.split("\n"):
        stripped = line.strip()
        if stripped in ("---ANALYSIS---", "---LATEX---", "---CHANGES---"):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = stripped.strip("-")
            current_lines = []
        else:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    # Extract LaTeX from code block if model wrapped it
    latex = sections.get("LATEX", raw)
    if latex.startswith("```latex"):
        latex = latex[len("```latex") :].strip()
    if latex.startswith("```"):
        latex = latex[3:].strip()
    if latex.endswith("```"):
        latex = latex[:-3].strip()

    return TailorOutput(
        analysis=sections.get("ANALYSIS", ""),
        tailored_tex=latex,
        changes=sections.get("CHANGES", ""),
    )


async def tailor_resume(
    base_tex: str,
    job: JobPosting,
    jd_keywords: dict[str, Any],
    config: dict[str, Any],
    llm_config: LLMConfig | None = None,
) -> TailorOutput:
    """Tailor a LaTeX resume for a specific job via LLM.

    Uses llm.py's complete() (not complete_json) because:
    - Output is LaTeX, not JSON
    - LaTeX contains characters that would need escaping in JSON
    - Delimiter-based parsing is simpler and more robust
    """
    strategy = config.get("tailoring", {}).get("strategy", "full")
    system_prompt = build_system_prompt(strategy)
    user_prompt = build_user_prompt(base_tex, job, jd_keywords)

    max_tokens = config.get("llm", {}).get("max_tokens", 8192)

    raw_output = await complete(
        prompt=user_prompt,
        system_prompt=system_prompt,
        config=llm_config,
        max_tokens=max_tokens,
        temperature=0.3,
    )

    return parse_tailor_output(raw_output)
