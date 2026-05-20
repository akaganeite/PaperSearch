from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict

from ..text import matched_terms
from .common import SourceError, http_get_text


BLOCK_TAGS = {"article", "li", "p", "tr", "h2", "h3", "h4", "dt", "dd"}
SEPARATOR_TAGS = {"br", "td", "th"}
PDF_HINTS = (".pdf", "/pdf/", "/doi/pdf/", "paper.pdf", "stamp.jsp")
TITLE_BAD_PATTERNS = [
    r"\baccepted papers\b",
    r"\bcall for\b",
    r"\bimportant dates\b",
    r"\bprogram display configuration\b",
    r"\bproceedings are available\b",
    r"\bconference\b",
    r"\bsymposium\b",
    r"\bregistration\b",
    r"\bsponsor",
    r"\bcommittee\b",
    r"\bkeynote\b",
    r"\barea chair\b",
    r"\bchair\(s\):",
    r"\btool demonstrations?\b",
    r"\bdemonstrations?\s*/",
    r"\bpresenter information\b",
    r"\bnominations:",
    r"\bsession chair\b",
    r"\blocation:",
    r"\btime zone\b",
    r"\bview mode\b",
    r"\bexpand all\b",
    r"\bcollapse all\b",
    r"\bselect other time zone\b",
    r"\bdisplay full program\b",
    r"\battendee code of conduct\b",
    r"\bpresenter instructions\b",
    r"\bauthor video submission\b",
    r"\btravel grants\b",
    r"\btechnical community\b",
    r"\bcomputer society\b",
    r"\binternational association\b",
    r"\bworkshop(?:s)?\b",
    r"\bposter(?:s)?\b",
    r"\bdonor(?:s)?\b",
    r"\bmay \d{1,2}[-–]\d{1,2}, \d{4}\b",
    r"\b\d+(?:st|nd|rd|th) ieee symposium\b",
]
TITLE_PREFIX_RE = re.compile(
    r"^(?:paper\s+title\s*:|title\s*:|distinguished\s+paper\s+award\s*:|not\s+scheduled\s+talk\s+|\d{1,2}:\d{2}(?:\s*[-–]\s*\d{1,2}:\d{2})?\s+|\d+m\s+talk\s+|talk\s+|\d+[\).]\s+|[-*•]\s+)",
    flags=re.I,
)
TRACK_MARKERS = [
    "Distinguished Paper Award",
    "Research Track",
    "Research Papers",
    "Technical Papers",
    "Pre-print",
    "File Attached",
    "Artifacts Available",
    "Artifact Available",
    "DOI",
]


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def _clean_multiline(value: object) -> str:
    lines = [_clean_text(line) for line in html.unescape(str(value or "")).splitlines()]
    return "\n".join(line for line in lines if line)


def _year(value: Any) -> int | None:
    try:
        year = int(value)
        return year if 1900 <= year <= 2100 else None
    except (TypeError, ValueError):
        return None


def _published_at(year: int | None) -> str:
    return f"{year}-01-01T00:00:00Z" if year else ""


def _absolute_url(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href.strip())


def _contains_pdf_hint(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in PDF_HINTS)


def _looks_like_title(text: str) -> bool:
    text = _clean_text(text)
    if not text or len(text) < 12 or len(text) > 220:
        return False
    lowered = text.lower()
    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    if any(bad in compact for bad in ("areachair", "toolpresentations", "tooldemonstrations", "demonstrationsjournal")):
        return False
    if any(re.search(pattern, lowered) for pattern in TITLE_BAD_PATTERNS):
        return False
    if re.fullmatch(r"[\d\s:–—,\-/]+", text):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z0-9+\-'/]*", text)
    if len(words) < 3 or len(words) > 28:
        return False
    if sum(1 for char in text if char.isalpha()) < 10:
        return False
    return True


def _strip_title_noise(value: str) -> str:
    original = _clean_text(value)
    if re.match(r"^\d{1,2}:\d{2}.*keynote\b", original, flags=re.I):
        return ""
    title = _clean_text(TITLE_PREFIX_RE.sub("", value))
    title = re.sub(r"^\d{1,2}:\d{2}(?:\d+m)?talk\s*", "", title, flags=re.I)
    title = re.split(r"\s+\|\s+", title)[0].strip()
    for marker in TRACK_MARKERS:
        match = re.search(re.escape(marker), title, flags=re.I)
        if match and _looks_like_title(title[: match.start()]):
            title = title[: match.start()].strip()
    title = re.sub(r"\s+(?:DOI|Pre-print|File Attached)$", "", title, flags=re.I).strip()
    return title.strip(" -–—|")


def _extract_doi(text: str) -> str:
    match = re.search(r"\b10\.\d{4,9}/[^\s\"'<>]+", text, flags=re.I)
    if not match:
        return ""
    return match.group(0).rstrip(").,;]")


def _source_id(venue: str, year: int | None, title: str, page_url: str) -> str:
    key = "|".join([venue.lower(), str(year or ""), title.lower(), page_url])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


class VenuePageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Dict[str, str]] = []
        self.blocks: list[Dict[str, Any]] = []
        self._current_link: Dict[str, str] | None = None
        self._block_depth = 0
        self._block_tag = ""
        self._block_text: list[str] = []
        self._block_links: list[Dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag in BLOCK_TAGS:
            if self._block_depth == 0:
                self._block_tag = tag
                self._block_text = []
                self._block_links = []
            self._block_depth += 1
        if tag in SEPARATOR_TAGS and self._block_depth > 0:
            self._block_text.append("\n")
        if tag == "a":
            self._current_link = {"href": attr_map.get("href", ""), "text": ""}

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_link is not None:
            link = {
                "href": self._current_link.get("href", ""),
                "text": _clean_text(self._current_link.get("text", "")),
            }
            if link["href"] or link["text"]:
                self.links.append(link)
                if self._block_depth > 0:
                    self._block_links.append(link)
            self._current_link = None
        if tag in BLOCK_TAGS and self._block_depth > 0:
            self._block_depth -= 1
            if self._block_depth == 0:
                text = _clean_multiline("".join(self._block_text))
                if text:
                    self.blocks.append({"tag": self._block_tag, "text": text, "links": list(self._block_links)})
                self._block_tag = ""
                self._block_text = []
                self._block_links = []

    def handle_data(self, data: str) -> None:
        if self._current_link is not None:
            self._current_link["text"] = self._current_link.get("text", "") + data
        if self._block_depth > 0:
            self._block_text.append(data)


def _best_link(page_url: str, links: list[Dict[str, str]], title: str = "") -> tuple[str, str]:
    best_url = ""
    pdf_url = ""
    title_norm = _clean_text(title).lower()
    for link in links:
        href = link.get("href", "")
        if not href or href.startswith("#"):
            continue
        url = _absolute_url(page_url, href)
        text = _clean_text(link.get("text", "")).lower()
        if _contains_pdf_hint(url):
            pdf_url = pdf_url or url
        if not best_url and (
            "pre-print" in text
            or "preprint" in text
            or "paper" in text
            or "doi" in text
            or "artifact" in text
            or "arxiv" in url.lower()
            or "doi.org" in url.lower()
            or (title_norm and _clean_text(link.get("text", "")).lower() == title_norm)
        ):
            best_url = url
    return best_url or page_url, pdf_url


def _paper_from_candidate(
    *,
    title: str,
    block_text: str,
    links: list[Dict[str, str]],
    page_url: str,
    source_label: str,
    venue: str,
    year: int | None,
) -> Dict[str, Any] | None:
    title = _strip_title_noise(title)
    if not _looks_like_title(title):
        return None
    url, pdf_url = _best_link(page_url, links, title)
    doi = _extract_doi(" ".join([block_text, url]))
    source_id = _source_id(venue, year, title, page_url)
    return {
        "source": source_label,
        "source_id": source_id,
        "title": title,
        "abstract": "",
        "authors": [],
        "url": url,
        "pdf_url": pdf_url,
        "doi": doi,
        "venue": venue,
        "year": year,
        "published_at": _published_at(year),
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_categories": [venue, "accepted/preprint"],
        "raw": {
            "source_page": page_url,
            "venue": venue,
            "year": year,
            "context": block_text[:1200],
        },
    }


def parse_page(
    html_text: str,
    *,
    page_url: str,
    source_label: str,
    venue: str,
    year: int | None,
) -> list[Dict[str, Any]]:
    parser = VenuePageParser()
    parser.feed(html_text)
    candidates: list[Dict[str, Any]] = []
    seen_titles: set[str] = set()

    for block in parser.blocks:
        text = _clean_text(block.get("text", ""))
        if not text:
            continue
        parts = [_strip_title_noise(part) for part in re.split(r"\n+| {3,}", text)]
        parts = [part for part in parts if part]
        title = ""
        for part in parts:
            if _looks_like_title(part):
                title = part
                break
        if not title and block.get("tag") in {"h2", "h3", "h4"} and _looks_like_title(text):
            title = text
        if not title:
            continue
        title_key = title.lower()
        if title_key in seen_titles:
            continue
        paper = _paper_from_candidate(
            title=title,
            block_text=text,
            links=block.get("links", []),
            page_url=page_url,
            source_label=source_label,
            venue=venue,
            year=year,
        )
        if paper is None:
            continue
        seen_titles.add(title_key)
        candidates.append(paper)

    for link in parser.links:
        title = _strip_title_noise(link.get("text", ""))
        title_key = title.lower()
        if title_key in seen_titles or not _looks_like_title(title):
            continue
        paper = _paper_from_candidate(
            title=title,
            block_text=title,
            links=[link],
            page_url=page_url,
            source_label=source_label,
            venue=venue,
            year=year,
        )
        if paper is None:
            continue
        seen_titles.add(title_key)
        candidates.append(paper)

    return candidates


def _expand_url(template: str, year: int) -> str:
    return template.format(year=year, yy=str(year)[-2:])


def _fetch_page(url: str, timeout_seconds: int) -> tuple[str, str]:
    return url, http_get_text(url, timeout=timeout_seconds, retries=0)


def _configured_years(config: Dict[str, Any]) -> list[int]:
    years = [_year(year) for year in config.get("years", [])]
    years = [year for year in years if year]
    if years:
        return sorted(set(years))
    now = datetime.now(timezone.utc).year
    return [now, now + 1]


def _has_interest_hint(paper: Dict[str, Any], interest: Dict[str, Any] | None) -> bool:
    if not interest:
        return True
    raw = paper.get("raw") if isinstance(paper.get("raw"), dict) else {}
    text = " ".join(
        [
            str(paper.get("title", "") or ""),
            str(paper.get("abstract", "") or ""),
            str(paper.get("venue", "") or ""),
            str(raw.get("context", "") or ""),
        ]
    )
    exclude_matches = matched_terms(text, interest.get("exclude_keywords", []))
    if exclude_matches:
        return False
    hint_terms = (
        list(interest.get("llm_agent_terms", []))
        + list(interest.get("security_software_terms", []))
        + list(interest.get("priority_topics", []))
    )
    return bool(matched_terms(text, hint_terms))


def fetch(config: Dict[str, Any], interest: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
    if not config.get("enabled", True):
        return []

    years = _configured_years(config)
    source_label = str(config.get("source_label", "VenuePreprints"))
    delay = float(config.get("request_delay_seconds", 0.3) or 0)
    max_url_attempts_per_venue_year = int(config.get("max_url_attempts_per_venue_year", 4) or 4)
    max_workers = int(config.get("max_workers", 4) or 4)
    timeout_seconds = int(config.get("timeout_seconds", 8) or 8)
    venues = config.get("venues", [])
    results: list[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    successful_pages = 0
    page_jobs: list[tuple[str, int, str, str]] = []

    for venue_cfg in venues:
        venue = str(venue_cfg.get("venue", "") or "").strip()
        if not venue:
            continue
        templates = [str(item) for item in venue_cfg.get("url_templates", []) if item]
        for year in years:
            for template in templates[:max_url_attempts_per_venue_year]:
                page_jobs.append((venue, year, _expand_url(template, year), source_label))

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 8))) as executor:
        future_map = {
            executor.submit(_fetch_page, url, timeout_seconds): (venue, year, url, label)
            for venue, year, url, label in page_jobs
        }
        for future in as_completed(future_map):
            venue, year, url, label = future_map[future]
            try:
                _url, html_text = future.result()
            except SourceError:
                continue
            successful_pages += 1
            for paper in parse_page(
                html_text,
                page_url=url,
                source_label=label,
                venue=venue,
                year=year,
            ):
                key = paper["title"].lower()
                if key in seen_keys:
                    continue
                if not _has_interest_hint(paper, interest):
                    continue
                seen_keys.add(key)
                results.append(paper)
            if delay:
                time.sleep(delay)

    if successful_pages == 0 and venues:
        raise SourceError("No configured venue accepted/preprint pages were reachable.")
    return results
