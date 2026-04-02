"""Job Tailor CLI — path setup for Resume Matcher backend imports."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
