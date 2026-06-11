"""Tests for the LinkedIn guest-API client helpers."""

from bs4 import BeautifulSoup

from src.linkedin_client import (
    LinkedInClient,
    _extract_job_id,
    _filter_cards,
    _merge_search_params,
)
from src.models import JobPosting


def _post(title: str = "x", company: str = "x") -> JobPosting:
    """Build a minimal JobPosting fixture for filter tests."""
    return JobPosting(
        job_id="1",
        title=title,
        company=company,
        description="",
        url="",
    )


def _card(html: str):
    """Parse an HTML fragment and return the first <li> as a Tag."""
    return BeautifulSoup(html, "html.parser").find("li")


def test_base_overrides_extra_on_collision() -> None:
    """User-supplied `start`/`keywords`/etc. must not break client pagination."""
    base = {"keywords": "engineer", "start": 0, "f_TPR": "r86400"}
    extra = {"start": 50, "keywords": "lawyer", "f_WT": 2}

    merged = _merge_search_params(base, extra)

    assert merged["start"] == "0"
    assert merged["keywords"] == "engineer"
    assert merged["f_TPR"] == "r86400"
    assert merged["f_WT"] == "2"


def test_list_values_comma_joined() -> None:
    """LinkedIn expects `f_E=3,4`, not repeated keys."""
    merged = _merge_search_params({}, {"f_E": [3, 4], "f_JT": ["F", "P"]})

    assert merged["f_E"] == "3,4"
    assert merged["f_JT"] == "F,P"


def test_tuple_values_comma_joined() -> None:
    """Tuples are treated the same as lists."""
    merged = _merge_search_params({}, {"f_E": (3, 4)})

    assert merged["f_E"] == "3,4"


def test_empty_and_none_values_dropped() -> None:
    """YAML `~` or `""` should not be sent to LinkedIn."""
    extra = {"f_WT": "", "f_E": None, "f_JT": "F", "f_AL": []}
    merged = _merge_search_params({}, extra)

    assert "f_WT" not in merged
    assert "f_E" not in merged
    assert "f_AL" not in merged
    assert merged["f_JT"] == "F"


def test_int_values_coerced_to_str() -> None:
    """YAML ints (`f_WT: 2`) must become strings for stable test assertions."""
    merged = _merge_search_params({"start": 25}, {"f_WT": 2, "distance": 10})

    assert merged["start"] == "25"
    assert merged["f_WT"] == "2"
    assert merged["distance"] == "10"


def test_none_extra_is_safe() -> None:
    """No extras supplied — only base params come through."""
    merged = _merge_search_params({"keywords": "k", "start": 0}, None)

    assert merged == {"keywords": "k", "start": "0"}


def test_empty_extra_dict_is_safe() -> None:
    """Empty filters dict matches the no-extras case."""
    merged = _merge_search_params({"keywords": "k"}, {})

    assert merged == {"keywords": "k"}


def test_passes_through_unknown_keys() -> None:
    """Helper is opaque — any key the user provides goes to LinkedIn as-is."""
    merged = _merge_search_params({}, {"geoId": "103644278", "sortBy": "R"})

    assert merged["geoId"] == "103644278"
    assert merged["sortBy"] == "R"


# --- _extract_job_id ---------------------------------------------------------


def test_extract_id_prefers_urn() -> None:
    """URN is authoritative; href slug is ignored when URN is present."""
    card = _card(
        '<li><div class="base-card" data-entity-urn="urn:li:jobPosting:4427116786">'
        '<a class="base-card__full-link" href="https://wrong/jobs/view/something-9999"></a>'
        "</div></li>"
    )
    assert _extract_job_id(card, card.a["href"]) == "4427116786"


def test_extract_id_falls_back_to_slug_href() -> None:
    """Search-result hrefs end with `-<id>?...` after a slug."""
    href = (
        "https://www.linkedin.com/jobs/view/"
        "cloud-infrastructure-engineer-100-remote-at-consultnet-4427116786"
        "?position=1&pageNum=0"
    )
    card = _card(f'<li><a class="base-card__full-link" href="{href}"></a></li>')
    assert _extract_job_id(card, href) == "4427116786"


def test_extract_id_falls_back_to_bare_numeric_href() -> None:
    """Email-style URLs use a bare numeric segment after /jobs/view/."""
    href = "https://www.linkedin.com/comm/jobs/view/4427116786?refId=abc"
    card = _card(f'<li><a class="base-card__full-link" href="{href}"></a></li>')
    assert _extract_job_id(card, href) == "4427116786"


def test_extract_id_returns_empty_when_unparseable() -> None:
    """No URN, no digits in href → empty string (caller skips the card)."""
    card = _card('<li><a class="base-card__full-link" href="https://no/digits/here"></a></li>')
    assert _extract_job_id(card, "https://no/digits/here") == ""


def test_extract_id_ignores_non_jobposting_urn() -> None:
    """A non-jobPosting URN should not be mistaken for a job ID."""
    card = _card(
        '<li><div data-entity-urn="urn:li:company:12345">'
        '<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/role-7777"></a>'
        "</div></li>"
    )
    # URN doesn't match jobPosting; should fall back to href → 7777
    assert _extract_job_id(card, card.a["href"]) == "7777"


# --- _parse_search_results ---------------------------------------------------


_REAL_CARD_FRAGMENT = """
<li>
  <div class="base-card base-search-card base-search-card--link job-search-card"
       data-entity-urn="urn:li:jobPosting:4427116786">
    <a class="base-card__full-link"
       href="https://www.linkedin.com/jobs/view/cloud-infrastructure-engineer-100%25-remote-at-consultnet-4427116786?position=1"></a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">Cloud Infrastructure Engineer - 100% Remote</h3>
      <h4 class="base-search-card__subtitle">
        <a class="hidden-nested-link" href="#">ConsultNet Technology Services and Solutions</a>
      </h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">Springville, UT</span>
      </div>
    </div>
  </div>
</li>
"""


def test_parse_search_results_extracts_full_card() -> None:
    """Round-trip a realistic card and check every populated field."""
    jobs = LinkedInClient(request_delay=0)._parse_search_results(_REAL_CARD_FRAGMENT)

    assert len(jobs) == 1
    j = jobs[0]
    assert j.job_id == "4427116786"
    assert j.title == "Cloud Infrastructure Engineer - 100% Remote"
    assert j.company == "ConsultNet Technology Services and Solutions"
    assert j.location == "Springville, UT"
    assert j.url == "https://www.linkedin.com/jobs/view/4427116786"
    assert j.description == ""  # Search cards don't carry JD text


def test_parse_search_results_empty_html() -> None:
    """No <li> elements at all → empty list, no crash."""
    jobs = LinkedInClient(request_delay=0)._parse_search_results("<html></html>")
    assert jobs == []


# --- _filter_cards -----------------------------------------------------------


def test_filter_drops_title_match() -> None:
    """Title containing the blocked word is dropped."""
    cards = [_post(title="Java Developer"), _post(title="Backend Engineer")]
    kept = _filter_cards(cards, title_exclude=["java"])
    assert [c.title for c in kept] == ["Backend Engineer"]


def test_filter_word_boundary_keeps_javascript() -> None:
    """`java` does NOT match `JavaScript` — the core polyglot-safety guarantee."""
    cards = [
        _post(title="Java Developer"),
        _post(title="JavaScript Engineer"),
        _post(title="Senior Java Architect"),
    ]
    kept = _filter_cards(cards, title_exclude=["java"])
    assert [c.title for c in kept] == ["JavaScript Engineer"]


def test_filter_case_insensitive() -> None:
    """Casing in card data or in the pattern doesn't matter."""
    cards = [_post(title="JAVA developer"), _post(title="backend ENGINEER")]
    kept = _filter_cards(cards, title_exclude=["Java"])
    assert [c.title for c in kept] == ["backend ENGINEER"]


def test_filter_drops_company_match() -> None:
    """Company substring (word-boundary) drops the card."""
    cards = [
        _post(company="BeaconFire Inc."),
        _post(company="Stripe"),
        _post(company="Jobs via Dice"),
    ]
    kept = _filter_cards(cards, company_exclude=["BeaconFire", "Jobs via Dice"])
    assert [c.company for c in kept] == ["Stripe"]


def test_filter_combines_both_blocklists() -> None:
    """A card is dropped if title OR company matches."""
    cards = [
        _post(title="Backend Engineer", company="BeaconFire"),     # company match
        _post(title="Java Developer", company="Stripe"),            # title match
        _post(title="Backend Engineer", company="Stripe"),          # keep
    ]
    kept = _filter_cards(cards, title_exclude=["java"], company_exclude=["BeaconFire"])
    assert len(kept) == 1
    assert kept[0].company == "Stripe" and "Backend" in kept[0].title


def test_filter_empty_lists_are_noop() -> None:
    """No patterns → identity pass-through (and skip the regex compile)."""
    cards = [_post(title="Java Developer")]
    assert _filter_cards(cards) == cards
    assert _filter_cards(cards, title_exclude=[], company_exclude=[]) == cards
    assert _filter_cards(cards, title_exclude=None, company_exclude=None) == cards


def test_filter_ignores_empty_or_whitespace_patterns() -> None:
    """Blank entries in config don't match every string."""
    cards = [_post(title="Backend Engineer")]
    # Without filtering for blanks, "" would match everything.
    kept = _filter_cards(cards, title_exclude=["", "  "])
    assert kept == cards


def test_filter_handles_regex_special_chars_in_pattern() -> None:
    """`.NET` is a literal, not a regex — the dot must not match `D` in DOT."""
    cards = [_post(title=".NET Developer"), _post(title="DNET Architect")]
    kept = _filter_cards(cards, title_exclude=[".NET"])
    assert [c.title for c in kept] == ["DNET Architect"]
