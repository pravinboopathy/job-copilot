"""LinkedIn guest API client for fetching job descriptions."""

import logging
import random
import re
import time
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from .models import JobPosting

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api"
_JOB_DETAIL_URL = f"{_BASE_URL}/jobPosting/{{job_id}}"
_JOB_SEARCH_URL = f"{_BASE_URL}/seeMoreJobPostings/search"

# Search-result hrefs look like .../jobs/view/<slug>-<numeric-id>?... — the ID
# is the trailing digits before any query/fragment. Email-alert URLs use a
# bare numeric segment after /jobs/view/ — both shapes match this regex.
_JOB_ID_FROM_HREF = re.compile(r"(\d+)(?:[/?#]|$)")

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _filter_cards(
    cards: list[JobPosting],
    title_exclude: list[str] | None = None,
    company_exclude: list[str] | None = None,
) -> list[JobPosting]:
    """Drop search-result cards whose title or company matches a blocklist.

    Patterns are matched as whole words (case-insensitive) so `java` drops
    "Java Developer" but NOT "JavaScript Developer". Empty patterns are
    silently ignored.
    """
    def _compile(patterns: list[str] | None) -> re.Pattern | None:
        if not patterns:
            return None
        cleaned = [re.escape(p.strip()) for p in patterns if p and p.strip()]
        if not cleaned:
            return None
        # Alphanumeric lookarounds (instead of \b) so patterns starting or
        # ending with non-word chars still anchor — `.NET` would not match
        # under `\b\.NET\b` because there's no boundary between space and `.`.
        return re.compile(
            r"(?<![A-Za-z0-9])(?:" + "|".join(cleaned) + r")(?![A-Za-z0-9])",
            re.IGNORECASE,
        )

    title_re = _compile(title_exclude)
    company_re = _compile(company_exclude)
    if title_re is None and company_re is None:
        return cards

    kept: list[JobPosting] = []
    for c in cards:
        if title_re and title_re.search(c.title):
            continue
        if company_re and company_re.search(c.company):
            continue
        kept.append(c)
    return kept


def _extract_job_id(card: Tag, href: str) -> str:
    """Pull the LinkedIn job_id from a search-result card.

    Prefers the parent card's `data-entity-urn` (`urn:li:jobPosting:NNN`),
    which is authoritative and survives slug-format changes. Falls back to
    the trailing numeric ID in the href, so email-style and slug-style URLs
    both work.
    """
    urn_el = card.find(attrs={"data-entity-urn": True})
    if urn_el is not None:
        urn = urn_el.get("data-entity-urn", "")
        if urn.startswith("urn:li:jobPosting:"):
            tail = urn.rsplit(":", 1)[-1]
            if tail.isdigit():
                return tail

    match = _JOB_ID_FROM_HREF.search(href)
    if match:
        return match.group(1)
    return ""


def _merge_search_params(
    base: dict[str, Any],
    extra: dict[str, Any] | None,
) -> dict[str, str]:
    """Merge user-supplied filter params with client-controlled base params.

    Base wins on collision so users can't break pagination by setting `start`
    (or shadow `keywords`/`location`/`f_TPR`). List values are comma-joined for
    LinkedIn (it expects `f_E=3,4`, not repeated keys). Empty/None values in
    `extra` are dropped. All values are coerced to str.
    """
    merged: dict[str, str] = {}
    if extra:
        for k, v in extra.items():
            if v is None or v == "":
                continue
            if isinstance(v, (list, tuple)):
                if not v:
                    continue
                merged[k] = ",".join(str(item) for item in v)
            else:
                merged[k] = str(v)
    for k, v in base.items():
        merged[k] = str(v)
    return merged


class LinkedInClient:
    """Fetch job descriptions from LinkedIn's guest API (no auth needed)."""

    def __init__(self, request_delay: float = 3.0) -> None:
        self.session = requests.Session()
        self.session.headers.update(_DEFAULT_HEADERS)
        self.request_delay = request_delay
        self._last_request_time: float = 0.0

    def _rate_limit(self) -> None:
        """Enforce minimum delay between requests with random jitter."""
        elapsed = time.time() - self._last_request_time
        jittered_delay = self.request_delay + random.uniform(0, self.request_delay)
        if elapsed < jittered_delay:
            time.sleep(jittered_delay - elapsed)
        self._last_request_time = time.time()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        """Make a rate-limited GET request with retry on 429."""
        self._rate_limit()

        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)

            if resp.status_code == 429:
                wait = self.request_delay * (2 ** attempt) + random.uniform(0, 2)
                logger.warning("Rate limited (429), waiting %.0fs", wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        raise requests.HTTPError(f"Rate limited after 3 attempts: {url}")

    def fetch_job(self, job_id: str) -> JobPosting:
        """Fetch a single job posting by ID."""
        url = _JOB_DETAIL_URL.format(job_id=job_id)
        resp = self._get(url)
        return self._parse_job_page(job_id, resp.text)

    def search_jobs(
        self,
        keywords: str,
        location: str = "United States",
        time_filter: str = "r86400",
        max_results: int = 25,
        extra_params: dict[str, Any] | None = None,
    ) -> list[JobPosting]:
        """Search for jobs using the guest API.

        Args:
            keywords: Search query (e.g., "infrastructure engineer")
            location: Location filter
            time_filter: Time range — r86400 (24h), r604800 (7d), r2592000 (30d)
            max_results: Maximum number of results to return
            extra_params: Optional opaque passthrough of LinkedIn guest-API
                filter params. Well-known codes (see the dev.to scraping
                reference for the full list):
                  f_WT  workplace type: 1=on-site, 2=remote, 3=hybrid
                  f_E   experience:     1=intern, 2=entry, 3=associate,
                                        4=mid-senior, 5=director, 6=exec
                  f_JT  job type:       F=full-time, P=part-time, C=contract,
                                        T=temp, V=volunteer, I=intern
                  f_SB2 salary bucket
                  f_C   company IDs
                  f_I   industry
                  f_AL  easy apply (true)
                  geoId alternative to location string
                  distance miles around geoId
                  sortBy R=recent, DD=default
                List values are comma-joined. The client-controlled keys
                (keywords, location, f_TPR, start) override any same-named
                entries here, so user filters can't break pagination.
                Gotchas: passing `geoId` silently shadows `location`;
                `sortBy=R` can shift the result set between page fetches and
                cause duplicates/skips on long sweeps.
        """
        jobs: list[JobPosting] = []
        start = 0
        first_page = True

        while len(jobs) < max_results:
            params = _merge_search_params(
                {
                    "keywords": keywords,
                    "location": location,
                    "f_TPR": time_filter,
                    "start": start,
                },
                extra_params,
            )

            try:
                resp = self._get(_JOB_SEARCH_URL, params=params)
            except requests.HTTPError as e:
                logger.error("Search request failed: %s", e)
                break

            if first_page:
                logger.info("Search URL: %s", resp.url)
                first_page = False

            page_jobs = self._parse_search_results(resp.text)
            if not page_jobs:
                break

            jobs.extend(page_jobs)
            start += 25

        return jobs[:max_results]

    def _parse_job_page(self, job_id: str, html: str) -> JobPosting:
        """Parse a job detail page into a JobPosting."""
        soup = BeautifulSoup(html, "html.parser")

        title = ""
        title_el = soup.find("h2", class_="top-card-layout__title")
        if title_el:
            title = title_el.get_text(strip=True)

        company = ""
        company_el = soup.find("a", class_="topcard__org-name-link")
        if not company_el:
            company_el = soup.find("span", class_="topcard__flavor")
        if company_el:
            company = company_el.get_text(strip=True)

        location = ""
        location_el = soup.find("span", class_="topcard__flavor--bullet")
        if location_el:
            location = location_el.get_text(strip=True)

        salary = None
        salary_el = soup.find("div", class_="salary")
        if not salary_el:
            salary_el = soup.find("span", class_="topcard__flavor--salary")
        if salary_el:
            salary = salary_el.get_text(strip=True)

        description = ""
        desc_el = soup.find("div", class_="description__text")
        if not desc_el:
            desc_el = soup.find("div", class_="show-more-less-html__markup")
        if desc_el:
            description = desc_el.get_text(separator="\n", strip=True)

        return JobPosting(
            job_id=job_id,
            title=title,
            company=company,
            description=description,
            url=f"https://www.linkedin.com/jobs/view/{job_id}",
            salary=salary,
            location=location,
        )

    def _parse_search_results(self, html: str) -> list[JobPosting]:
        """Parse job search result cards into JobPostings."""
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("li")
        jobs: list[JobPosting] = []

        for card in cards:
            link = card.find("a", class_="base-card__full-link")
            if not link:
                continue

            job_id = _extract_job_id(card, link.get("href", ""))
            if not job_id:
                continue

            title = ""
            title_el = card.find("h3", class_="base-search-card__title")
            if title_el:
                title = title_el.get_text(strip=True)

            company = ""
            company_el = card.find("h4", class_="base-search-card__subtitle")
            if company_el:
                company = company_el.get_text(strip=True)

            location = ""
            location_el = card.find("span", class_="job-search-card__location")
            if location_el:
                location = location_el.get_text(strip=True)

            jobs.append(JobPosting(
                job_id=job_id,
                title=title,
                company=company,
                description="",  # Populated by fetch_job()
                url=f"https://www.linkedin.com/jobs/view/{job_id}",
                location=location,
            ))

        return jobs
