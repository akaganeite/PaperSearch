from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any, Dict, Iterable

from .common import SourceError, http_get_json, http_get_text


CSPAPERS_API = "https://api.cspapers.org/"
CSPAPERS_RAW = "https://raw.githubusercontent.com/swkim101/cspapers.org/main/"


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _first(item: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", []):
            return value
    return ""


def _flatten_payload(payload: Any) -> list[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "papers", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _authors(value: Any) -> list[str]:
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                name = _clean_text(_first(item, ["name", "author"]))
            else:
                name = _clean_text(item)
            if name:
                result.append(name)
        return result
    if isinstance(value, str):
        return [part.strip() for part in re.split(r",| and ", value) if part.strip()]
    return []


def _year(value: Any) -> int | None:
    try:
        year = int(value)
        return year if 1900 <= year <= 2100 else None
    except (TypeError, ValueError):
        return None


def _published_at(year: int | None) -> str:
    return f"{year}-01-01T00:00:00Z" if year else ""


def _source_id(title: str, index: str) -> str:
    if index:
        return index
    return hashlib.sha1(title.lower().encode("utf-8")).hexdigest()


def _fetch_abstract(index: str) -> str:
    if not index:
        return ""
    index = urllib.parse.quote(index.lstrip("/"), safe="/:")
    try:
        return _clean_text(http_get_text(CSPAPERS_RAW + index, timeout=15))
    except (SourceError, ValueError):
        return ""


def _to_paper(item: Dict[str, Any], fetch_abstract: bool) -> Dict[str, Any] | None:
    title = _clean_text(_first(item, ["title", "name"]))
    if not title:
        return None

    year = _year(_first(item, ["year", "publishedYear"]))
    venue = _clean_text(_first(item, ["venue", "conference", "conf", "booktitle"]))
    index = _clean_text(_first(item, ["index", "abstractPath", "path"]))
    abstract = _clean_text(_first(item, ["abstract", "summary"]))
    if fetch_abstract and not abstract and index:
        abstract = _fetch_abstract(index)

    url = _clean_text(_first(item, ["url", "paperUrl", "link", "doi", "ee"]))
    authors = _authors(_first(item, ["authors", "author"]))

    return {
        "source": "csPapers",
        "source_id": _source_id(title, index),
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "url": url,
        "pdf_url": _clean_text(_first(item, ["pdf", "pdfUrl"])),
        "venue": venue,
        "year": year,
        "published_at": _published_at(year),
        "updated_at": "",
        "source_categories": [venue] if venue else [],
        "raw": item,
    }


def _query_variants() -> list[str]:
    return [
        "large language model agent vulnerability static analysis program repair patch generation root cause",
        "vulnerability detection static analysis patch generation root cause program repair",
        "automated program repair patch correctness patch validation vulnerability",
        "bug localization fault localization root cause analysis vulnerability",
        "software security static analysis vulnerability patch repair",
    ]


def fetch(
    interest: Dict[str, Any],
    year_from: int,
    year_to: int,
    pages_per_query: int = 3,
    max_abstract_fetch: int = 80,
) -> list[Dict[str, Any]]:
    venues = interest.get("cspapers_venues", [])
    results: list[Dict[str, Any]] = []
    seen_titles: set[str] = set()
    abstract_fetch_count = 0

    for query in _query_variants():
        for venue in venues:
            for page in range(max(1, pages_per_query)):
                params = {
                    "query": query,
                    "yearFrom": str(year_from),
                    "yearTo": str(year_to),
                    "venue": venue,
                    "orderBy": "date",
                    "ascending": "false",
                    "skip": str(page * 20),
                }
                url = CSPAPERS_API + "?" + urllib.parse.urlencode(params)
                try:
                    payload = http_get_json(url, timeout=30)
                except SourceError:
                    break
                items = _flatten_payload(payload)
                if not items:
                    break
                for item in items:
                    title = _clean_text(_first(item, ["title", "name"]))
                    title_key = title.lower()
                    if not title or title_key in seen_titles:
                        continue
                    seen_titles.add(title_key)
                    should_fetch_abstract = abstract_fetch_count < max_abstract_fetch
                    paper = _to_paper(item, fetch_abstract=should_fetch_abstract)
                    if paper is None:
                        continue
                    if should_fetch_abstract and paper.get("abstract"):
                        abstract_fetch_count += 1
                    results.append(paper)
    return results
