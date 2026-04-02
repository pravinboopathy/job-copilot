"""Thin adapters bridging Resume Matcher's JSON-resume functions to plain text/LaTeX.

Resume Matcher's refiner operates on JSON resume dicts. This module reimplements
the simple loops for plain-text inputs so the CLI can work directly with .tex files.
"""

import re
from typing import Any

from app.llm import LLMConfig, complete_json
from app.prompts.refinement import AI_PHRASE_BLACKLIST, AI_PHRASE_REPLACEMENTS
from app.prompts.templates import EXTRACT_KEYWORDS_PROMPT

# Prompt injection patterns — copied from improver.py:28-37 to avoid
# importing the full improver module for 8 lines of regex.
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?above",
    r"forget\s+(everything|all)",
    r"new\s+instructions?:",
    r"system\s*:",
    r"<\s*/?\s*system\s*>",
    r"\[\s*INST\s*\]",
    r"\[\s*/\s*INST\s*\]",
]


def sanitize_input(text: str) -> str:
    """Strip prompt injection patterns from user input."""
    sanitized = text
    for pattern in _INJECTION_PATTERNS:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)
    return sanitized


def keyword_in_text(keyword: str, text: str) -> bool:
    """Check if keyword exists as a whole word in text.

    Uses word boundaries to avoid false positives
    (e.g., 'python' should not match 'pythonic').
    """
    escaped = re.escape(keyword.lower())
    pattern = rf"\b{escaped}\b"
    return bool(re.search(pattern, text.lower()))


def _collect_keywords(jd_keywords: dict[str, Any]) -> set[str]:
    """Collect all keywords from a JD keywords dict."""
    all_kw: set[str] = set()
    all_kw.update(jd_keywords.get("required_skills", []))
    all_kw.update(jd_keywords.get("preferred_skills", []))
    all_kw.update(jd_keywords.get("keywords", []))
    return all_kw


def calculate_keyword_match_text(text: str, jd_keywords: dict[str, Any]) -> float:
    """Calculate keyword match percentage for plain text.

    Reimplements refiner.calculate_keyword_match but takes a plain text
    string instead of a JSON resume dict.
    """
    all_keywords = _collect_keywords(jd_keywords)
    if not all_keywords:
        return 0.0
    matched = sum(1 for kw in all_keywords if keyword_in_text(kw, text))
    return (matched / len(all_keywords)) * 100


def analyze_keyword_gaps_text(
    jd_keywords: dict[str, Any],
    tailored_text: str,
    master_text: str,
) -> dict[str, Any]:
    """Analyze keyword gaps for plain text inputs.

    Reimplements refiner.analyze_keyword_gaps but takes two plain text
    strings instead of two JSON resume dicts.
    """
    all_kw = _collect_keywords(jd_keywords)

    missing: list[str] = []
    injectable: list[str] = []
    non_injectable: list[str] = []

    for kw in all_kw:
        if not keyword_in_text(kw, tailored_text):
            missing.append(kw)
            if keyword_in_text(kw, master_text):
                injectable.append(kw)
            else:
                non_injectable.append(kw)

    total = len(all_kw) if all_kw else 1
    return {
        "missing_keywords": missing,
        "injectable_keywords": injectable,
        "non_injectable_keywords": non_injectable,
        "current_match_percentage": (total - len(missing)) / total * 100,
        "potential_match_percentage": (total - len(non_injectable)) / total * 100,
    }


def remove_ai_phrases_text(
    tex: str,
    job_description: str = "",
) -> tuple[str, list[str]]:
    """Remove AI-generated phrases from a LaTeX string.

    Reimplements the inner clean_text() logic from refiner.remove_ai_phrases
    without the recursive dict walker, since we operate on a single string.
    """
    jd_lower = job_description.lower()
    jd_protected: set[str] = {
        p.lower() for p in AI_PHRASE_BLACKLIST if p.lower() in jd_lower
    }

    removed: set[str] = set()
    cleaned = tex

    for phrase in AI_PHRASE_BLACKLIST:
        if phrase.lower() in jd_protected:
            continue
        if phrase.lower() in cleaned.lower():
            removed.add(phrase)
            replacement = AI_PHRASE_REPLACEMENTS.get(phrase.lower(), "")
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            cleaned = pattern.sub(replacement, cleaned)

    return cleaned, list(removed)


async def extract_keywords(
    job_description: str,
    llm_config: LLMConfig,
) -> dict[str, Any]:
    """Extract structured keywords from a job description via LLM.

    Wrapper around improver.extract_job_keywords that accepts an explicit
    LLMConfig so the CLI controls which model/provider is used.
    """
    sanitized_jd = sanitize_input(job_description)
    prompt = EXTRACT_KEYWORDS_PROMPT.format(job_description=sanitized_jd)

    return await complete_json(
        prompt=prompt,
        system_prompt="You are an expert job description analyzer.",
        config=llm_config,
    )
