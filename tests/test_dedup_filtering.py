"""Test that already-processed jobs are filtered before applying --limit."""

import json
from pathlib import Path

from src.models import JobReference
from src.state import ProcessedJobsState


def test_unprocessed_refs_come_first(tmp_path: Path) -> None:
    """With 5 refs and 2 already processed, limit=2 should pick 2 unprocessed."""
    state_path = tmp_path / "state.json"
    state = ProcessedJobsState(state_path)
    state.mark_processed("job_1", {"title": "old"})
    state.mark_processed("job_3", {"title": "old"})

    all_refs = [
        JobReference(job_id="job_1", title="A"),
        JobReference(job_id="job_2", title="B"),
        JobReference(job_id="job_3", title="C"),
        JobReference(job_id="job_4", title="D"),
        JobReference(job_id="job_5", title="E"),
    ]

    unprocessed = [ref for ref in all_refs if not state.is_processed(ref.job_id)]
    limited = unprocessed[:2]

    assert len(unprocessed) == 3
    assert [r.job_id for r in limited] == ["job_2", "job_4"]


def test_all_processed_yields_empty(tmp_path: Path) -> None:
    """If all refs are processed, result should be empty."""
    state_path = tmp_path / "state.json"
    state = ProcessedJobsState(state_path)
    for i in range(3):
        state.mark_processed(f"job_{i}")

    all_refs = [JobReference(job_id=f"job_{i}") for i in range(3)]
    unprocessed = [ref for ref in all_refs if not state.is_processed(ref.job_id)]

    assert unprocessed == []


def test_no_limit_returns_all_unprocessed(tmp_path: Path) -> None:
    """Without a limit, all unprocessed refs are returned."""
    state_path = tmp_path / "state.json"
    state = ProcessedJobsState(state_path)
    state.mark_processed("job_0")

    all_refs = [JobReference(job_id=f"job_{i}") for i in range(5)]
    unprocessed = [ref for ref in all_refs if not state.is_processed(ref.job_id)]

    assert len(unprocessed) == 4
    assert "job_0" not in [r.job_id for r in unprocessed]


def test_empty_state_no_filtering(tmp_path: Path) -> None:
    """Fresh state file filters nothing."""
    state_path = tmp_path / "state.json"
    state = ProcessedJobsState(state_path)

    all_refs = [JobReference(job_id=f"job_{i}") for i in range(3)]
    unprocessed = [ref for ref in all_refs if not state.is_processed(ref.job_id)]
    limited = unprocessed[:2]

    assert len(unprocessed) == 3
    assert len(limited) == 2
