"""Parse LinkedIn job alert emails to extract job references."""

import logging
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .models import JobReference

logger = logging.getLogger(__name__)

# LinkedIn job alert links follow this pattern
_JOB_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?linkedin\.com/(?:comm/)?jobs/view/(\d+)"
)


def parse_linkedin_alert(html: str) -> list[JobReference]:
    """Extract job references from a LinkedIn alert email HTML body.

    Parses links matching LinkedIn job view URLs and extracts the job ID,
    title (from link text), and company (best-effort from surrounding context).
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    seen_ids: set[str] = set()
    refs: list[JobReference] = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = _JOB_URL_PATTERN.search(href)
        if not match:
            continue

        job_id = match.group(1)
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        title = link.get_text(strip=True) or ""
        company = _extract_company(link)

        refs.append(JobReference(job_id=job_id, title=title, company=company))

    logger.info("Parsed %d job references from email", len(refs))
    return refs


def _extract_company(link_element: BeautifulSoup) -> str:
    """Best-effort company extraction from the link's surrounding HTML.

    LinkedIn alert emails typically have the company name in a sibling
    or parent element near the job title link.
    """
    # Check next sibling text
    for sibling in link_element.next_siblings:
        text = getattr(sibling, "get_text", lambda **_: "")
        result = text(strip=True) if callable(text) else str(sibling).strip()
        if result and len(result) < 100:
            return result
        break

    # Check parent's text content minus the link text
    parent = link_element.parent
    if parent:
        parent_text = parent.get_text(strip=True)
        link_text = link_element.get_text(strip=True)
        remainder = parent_text.replace(link_text, "").strip(" -–—·|")
        if remainder and len(remainder) < 100:
            return remainder

    return ""
