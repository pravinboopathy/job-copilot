"""Shared data classes used across modules."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobReference:
    """A job reference extracted from a LinkedIn alert email."""

    job_id: str
    title: str = ""
    company: str = ""


@dataclass
class JobPosting:
    """A fully-fetched job posting with description."""

    job_id: str
    title: str
    company: str
    description: str
    url: str
    salary: str | None = None
    location: str | None = None


@dataclass
class TailorOutput:
    """Parsed output from the LLM tailoring call."""

    analysis: str
    tailored_tex: str
    changes: str


@dataclass
class TailorResult:
    """Complete result from processing a single job."""

    job: JobPosting
    tailored_tex: str
    analysis: str
    changes: str
    pre_match: float
    post_match: float
    potential_match: float
    keyword_report: dict[str, Any]
    removed_phrases: list[str]
    pdf_path: str | None
    tex_path: str
    report_path: str
    matched_keywords: list[str] = field(default_factory=list)
