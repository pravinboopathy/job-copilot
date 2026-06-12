"""Prompts used by the tailoring pipeline.

Two exports:

- ``EXTRACT_KEYWORDS_PROMPT`` — a ``.format(job_description=...)`` template that
  returns a structured keyword inventory for a JD.
- ``CRITICAL_TRUTHFULNESS_RULES`` — a dict keyed by tailoring strategy
  (``nudge`` / ``keywords`` / ``full``). Each value is appended verbatim into
  the resume-tailoring system prompt.

These prompts are derived from Resume Matcher (Apache-2.0,
https://github.com/srbhr/Resume-Matcher) at the snapshot point of the
original fork. See ``NOTICES`` at the repo root for attribution.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

EXTRACT_KEYWORDS_PROMPT = """Extract job requirements as JSON. Output ONLY the JSON object, no other text.

Example format:
{{
  "required_skills": ["Python", "AWS"],
  "preferred_skills": ["Kubernetes"],
  "experience_requirements": ["5+ years"],
  "education_requirements": ["Bachelor's in CS"],
  "key_responsibilities": ["Lead team"],
  "keywords": ["microservices", "agile"],
  "experience_years": 5,
  "seniority_level": "senior"
}}

Extract numeric years (e.g., "5+ years" → 5) and infer seniority level.

Job description:
{job_description}"""


# ---------------------------------------------------------------------------
# Truthfulness rules (shared template + per-strategy rule 7)
# ---------------------------------------------------------------------------

CRITICAL_TRUTHFULNESS_RULES_TEMPLATE = """CRITICAL TRUTHFULNESS RULES - NEVER VIOLATE:
1. DO NOT add any skill, tool, technology, or certification that is not explicitly mentioned in the original resume
2. DO NOT invent numeric achievements (e.g., "increased by 30%") unless they exist in original
3. DO NOT add company names, product names, or technical terms not in the original
4. DO NOT upgrade experience level (e.g., "Junior" -> "Senior")
5. DO NOT add languages, frameworks, or platforms the candidate hasn't used
6. DO NOT extend employment dates or change timelines. Copy date ranges exactly as they appear, including months.
7. {rule_7}
8. Preserve factual accuracy - only use information provided by the candidate
9. NEVER remove existing skills, certifications, languages, or awards. You may reorder by relevance, but every original item must remain.

Violation of these rules could cause serious problems for the candidate in job interviews.
"""


def _build_truthfulness_rules(rule_7: str) -> str:
    return CRITICAL_TRUTHFULNESS_RULES_TEMPLATE.format(rule_7=rule_7)


CRITICAL_TRUTHFULNESS_RULES: dict[str, str] = {
    "nudge": _build_truthfulness_rules(
        "DO NOT add new bullet points or content - only rephrase existing content"
    ),
    "keywords": _build_truthfulness_rules(
        "You may rephrase existing bullet points to include keywords, but do NOT add new bullet points"
    ),
    "full": _build_truthfulness_rules(
        "You may expand existing bullet points or add new ones that elaborate on existing work, but DO NOT invent entirely new responsibilities"
    ),
}
