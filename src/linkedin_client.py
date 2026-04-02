"""LinkedIn guest API client for fetching job descriptions."""

import logging
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

from .models import JobPosting

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api"
_JOB_DETAIL_URL = f"{_BASE_URL}/jobPosting/{{job_id}}"
_JOB_SEARCH_URL = f"{_BASE_URL}/seeMoreJobPostings/search"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class LinkedInClient:
    """Fetch job descriptions from LinkedIn's guest API (no auth needed)."""

    def __init__(self, request_delay: float = 3.0) -> None:
        self.session = requests.Session()
        self.session.headers.update(_DEFAULT_HEADERS)
        self.request_delay = request_delay
        self._last_request_time: float = 0.0

    def _rate_limit(self) -> None:
        """Enforce minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        """Make a rate-limited GET request with retry on 429."""
        self._rate_limit()

        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)

            if resp.status_code == 429:
                wait = self.request_delay * (2 ** attempt)
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
    ) -> list[JobPosting]:
        """Search for jobs using the guest API.

        Args:
            keywords: Search query (e.g., "infrastructure engineer")
            location: Location filter
            time_filter: Time range — r86400 (24h), r604800 (7d), r2592000 (30d)
            max_results: Maximum number of results to return
        """
        jobs: list[JobPosting] = []
        start = 0

        while len(jobs) < max_results:
            params = {
                "keywords": keywords,
                "location": location,
                "f_TPR": time_filter,
                "start": start,
            }

            try:
                resp = self._get(_JOB_SEARCH_URL, params=params)
            except requests.HTTPError as e:
                logger.error("Search request failed: %s", e)
                break

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

            href = link.get("href", "")
            # Extract job ID from URL
            job_id = ""
            for segment in href.rstrip("/").split("/"):
                if segment.isdigit():
                    job_id = segment
                    break

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
