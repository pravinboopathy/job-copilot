"""Dedup tracker using a flat JSON file."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProcessedJobsState:
    """Track which jobs have been processed to avoid duplicates."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load state file %s: %s", self.path, e)
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, default=str))

    def is_processed(self, job_id: str) -> bool:
        return job_id in self._data

    def mark_processed(self, job_id: str, metadata: dict[str, Any] | None = None) -> None:
        self._data[job_id] = {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
        self._save()

    def get_stats(self) -> dict[str, Any]:
        total = len(self._data)
        skipped = sum(1 for v in self._data.values() if v.get("skipped"))
        tailored = total - skipped
        return {
            "total_processed": total,
            "tailored": tailored,
            "skipped": skipped,
        }
