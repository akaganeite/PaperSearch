from __future__ import annotations

import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from .common import SourceError, http_get_text


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_API = "http://export.arxiv.org/api/query"


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _term_query(prefix: str, terms: list[str]) -> str:
    parts = []
    for term in terms:
        escaped = term.replace('"', "")
        if " " in escaped or "-" in escaped:
            parts.append(f'{prefix}:"{escaped}"')
        else:
            parts.append(f"{prefix}:{escaped}")
    return "(" + " OR ".join(parts) + ")"


def build_search_queries(_interest: Dict[str, Any]) -> list[str]:
    return [
        'all:"large language model" AND all:vulnerability',
        'all:LLM AND all:"static analysis"',
        'all:LLM AND all:"program repair"',
        'all:LLM AND all:"patch generation"',
        'all:agent AND all:"software security"',
        'all:LLM AND all:"root cause"',
        'all:LLM AND all:"fault localization"',
    ]


def _allowed_category(categories: list[str], interest: Dict[str, Any]) -> bool:
    allowed = set(interest.get("arxiv_categories", []))
    return not allowed or bool(allowed.intersection(categories))


def fetch(interest: Dict[str, Any], since: datetime, max_results: int = 200) -> list[Dict[str, Any]]:
    per_query_limit = max(10, min(50, max_results // 2))
    papers: list[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    errors: list[str] = []
    successful_queries = 0

    for query in build_search_queries(interest):
        params = {
            "search_query": query,
            "start": "0",
            "max_results": str(per_query_limit),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = ARXIV_API + "?" + urllib.parse.urlencode(params)
        try:
            xml_text = http_get_text(url, timeout=12, retries=1)
        except SourceError as exc:
            errors.append(f"{query}: {exc}")
            continue
        successful_queries += 1
        root = ET.fromstring(xml_text)

        for entry in root.findall("atom:entry", ATOM_NS):
            title = _clean_text(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
            abstract = _clean_text(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
            published = _clean_text(entry.findtext("atom:published", default="", namespaces=ATOM_NS))
            updated = _clean_text(entry.findtext("atom:updated", default="", namespaces=ATOM_NS))
            published_dt = _parse_datetime(published)
            if published_dt and published_dt < since:
                continue

            arxiv_url = _clean_text(entry.findtext("atom:id", default="", namespaces=ATOM_NS))
            arxiv_id = arxiv_url.rstrip("/").split("/")[-1]
            if arxiv_id in seen_ids:
                continue
            seen_ids.add(arxiv_id)

            authors = [
                _clean_text(author.findtext("atom:name", default="", namespaces=ATOM_NS))
                for author in entry.findall("atom:author", ATOM_NS)
            ]
            authors = [author for author in authors if author]
            categories = [
                category.attrib.get("term", "")
                for category in entry.findall("atom:category", ATOM_NS)
                if category.attrib.get("term")
            ]
            if not _allowed_category(categories, interest):
                continue

            pdf_url = ""
            for link in entry.findall("atom:link", ATOM_NS):
                if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                    pdf_url = link.attrib.get("href", "")
                    break

            papers.append(
                {
                    "source": "arXiv",
                    "source_id": arxiv_id,
                    "title": title,
                    "abstract": abstract,
                    "authors": authors,
                    "url": arxiv_url,
                    "pdf_url": pdf_url,
                    "venue": "",
                    "year": published_dt.year if published_dt else None,
                    "published_at": published,
                    "updated_at": updated,
                    "source_categories": categories,
                    "raw": {
                        "query": query,
                        "arxiv_id": arxiv_id,
                        "categories": categories,
                    },
                }
            )
        time.sleep(3)

    if successful_queries == 0 and errors:
        raise SourceError("; ".join(errors[:2]))
    return papers


def bootstrap_since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)
