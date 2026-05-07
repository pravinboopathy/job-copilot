"""Test filename sanitization."""

from src.pipeline import _sanitize_filename


def test_basic_sanitize() -> None:
    assert _sanitize_filename("Netflix Job") == "Netflix_Job"


def test_strips_commas_and_punctuation() -> None:
    assert _sanitize_filename("Netflix, Inc.") == "Netflix_Inc"


def test_collapses_multiple_underscores() -> None:
    assert _sanitize_filename("Senior   Engineer") == "Senior_Engineer"


def test_strips_leading_trailing_separators() -> None:
    assert _sanitize_filename(" ,Job Title, ") == "Job_Title"


def test_preserves_hyphens() -> None:
    assert _sanitize_filename("Full-Stack Engineer") == "Full-Stack_Engineer"


def test_empty_after_sanitize() -> None:
    assert _sanitize_filename("!!!") == ""
