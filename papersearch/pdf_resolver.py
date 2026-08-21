from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable

from . import db
from .config import load_config
from .sources.common import USER_AGENT, SourceError, configure_network, http_get_json
from .text import normalize_title


SEMANTIC_SCHOLAR_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_WORKS = "https://api.openalex.org/works"
CROSSREF_WORKS = "https://api.crossref.org/works"
UNPAYWALL_API = "https://api.unpaywall.org/v2/"
PUBLISHER_DOMAINS = {
    "dl.acm.org": "ACM DL",
    "ieeexplore.ieee.org": "IEEE Xplore",
}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_doi(value: str) -> str:
    doi = _clean(value).lower()
    doi = doi.removeprefix("https://doi.org/")
    doi = doi.removeprefix("http://doi.org/")
    doi = doi.removeprefix("doi:")
    return doi.strip()


def publisher_source_for_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    for domain, source in PUBLISHER_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return source
    return ""


def _is_publisher_url(url: str) -> bool:
    return bool(publisher_source_for_url(url))


def _tokens(title: str) -> set[str]:
    stopwords = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
    return {token for token in normalize_title(title).split() if token and token not in stopwords}


def title_similarity(left: str, right: str) -> float:
    left_norm = normalize_title(left)
    right_norm = normalize_title(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.9
    left_tokens = _tokens(left_norm)
    right_tokens = _tokens(right_norm)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _year_matches(paper_year: Any, candidate_year: Any) -> bool:
    if not paper_year or not candidate_year:
        return True
    try:
        return abs(int(paper_year) - int(candidate_year)) <= 1
    except (TypeError, ValueError):
        return True


def _is_good_match(paper: Dict[str, Any], candidate_title: str, candidate_year: Any = None) -> bool:
    return title_similarity(str(paper.get("title", "")), candidate_title) >= 0.72 and _year_matches(
        paper.get("year"),
        candidate_year,
    )


def _request_pdf_probe(url: str, timeout: int = 5) -> tuple[bool, str]:
    if not url or not url.startswith(("http://", "https://")):
        return False, ""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Range": "bytes=0-2047",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            content_type = (response.headers.get("Content-Type") or "").lower()
            chunk = response.read(2048)
            looks_like_pdf = chunk.startswith(b"%PDF")
            if "application/pdf" in content_type or looks_like_pdf or final_url.lower().endswith(".pdf"):
                return True, final_url
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return False, ""
    return False, ""


def verify_pdf_url(url: str) -> str:
    ok, final_url = _request_pdf_probe(url)
    return final_url if ok else ""


def _http_get_text_with_final_url(url: str, timeout: int = 10) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), response.geturl()
    except urllib.error.HTTPError as exc:
        try:
            charset = exc.headers.get_content_charset() if exc.headers else None
            body = exc.read().decode(charset or "utf-8", errors="replace")
        except Exception:
            body = ""
        return body, exc.geturl()


def _resolve_url_final(url: str, timeout: int = 8) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.geturl()
    except (urllib.error.URLError, TimeoutError, ValueError):
        return ""


def _publisher_metadata_from_url(url: str, source_hint: str = "") -> Dict[str, str]:
    url = _clean(url)
    if not url:
        return {}
    final_url = _resolve_url_final(url)
    candidate = final_url or url
    source = publisher_source_for_url(candidate)
    if not source:
        return {}
    return {
        "publisher_url": candidate,
        "publisher_source": source_hint or source,
        "publisher_status": "found",
    }


def _publisher_metadata_from_doi(doi: str) -> Dict[str, str]:
    doi = _normalize_doi(doi)
    if not doi:
        return {}
    return _publisher_metadata_from_url("https://doi.org/" + doi)


def publisher_access_pdf_url(doi: str = "", publisher_url: str = "") -> Dict[str, str]:
    doi = _normalize_doi(doi)
    publisher_url = _clean(publisher_url)
    source = publisher_source_for_url(publisher_url)
    if not source and doi.startswith("10.1145/"):
        source = "ACM DL"
    if source == "ACM DL" and doi:
        return {
            "publisher_pdf_url": "https://dl.acm.org/doi/pdf/" + urllib.parse.quote(doi, safe="/."),
            "publisher_pdf_source": "ACM DL",
        }

    parsed = urllib.parse.urlparse(publisher_url)
    if source == "IEEE Xplore":
        match = re.search(r"/document/(\d+)", parsed.path)
        if match:
            return {
                "publisher_pdf_url": "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=" + match.group(1),
                "publisher_pdf_source": "IEEE Xplore",
            }
    return {}


def _doi_from_publisher_url(url: str) -> str:
    parsed = urllib.parse.urlparse(_clean(url))
    if publisher_source_for_url(url) != "ACM DL":
        return ""
    match = re.search(r"/doi/(10\.[^?#]+)", parsed.path, flags=re.I)
    return _normalize_doi(urllib.parse.unquote(match.group(1))) if match else ""


def _ensure_publisher_access_metadata(metadata: Dict[str, Any]) -> bool:
    doi = _normalize_doi(str(metadata.get("doi", "") or ""))
    publisher_url = _clean(str(metadata.get("publisher_url", "") or ""))

    if not doi and publisher_url:
        doi = _doi_from_publisher_url(publisher_url)
        if doi and not metadata.get("doi"):
            metadata["doi"] = doi

    if not publisher_url and doi.startswith("10.1145/"):
        publisher_url = "https://dl.acm.org/doi/" + urllib.parse.quote(doi, safe="/.")
        metadata["publisher_url"] = publisher_url
        metadata["publisher_source"] = "ACM DL"
        metadata["publisher_status"] = "found"
    elif not publisher_url and doi.startswith("10.1109/"):
        for key, value in _publisher_metadata_from_doi(doi).items():
            if value and not metadata.get(key):
                metadata[key] = value
        publisher_url = _clean(str(metadata.get("publisher_url", "") or ""))

    if not metadata.get("publisher_pdf_url"):
        for key, value in publisher_access_pdf_url(doi, publisher_url).items():
            if value and not metadata.get(key):
                metadata[key] = value

    if metadata.get("publisher_pdf_url"):
        metadata["publisher_status"] = "found"
        if publisher_url and not metadata.get("publisher_source"):
            metadata["publisher_source"] = publisher_source_for_url(publisher_url) or "publisher"
        return True
    return False


def _extract_attr(tag: str, attr: str) -> str:
    pattern = rf"""{attr}\s*=\s*["']([^"']+)["']"""
    match = re.search(pattern, tag, re.I)
    return match.group(1) if match else ""


def _publisher_pdf_candidates(publisher_url: str) -> list[Dict[str, str]]:
    if not publisher_url:
        return []
    try:
        html, final_url = _http_get_text_with_final_url(publisher_url, timeout=10)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    base_url = final_url or publisher_url
    source = publisher_source_for_url(base_url) or "publisher"
    candidates: list[Dict[str, str]] = []

    for tag in re.findall(r"<meta\b[^>]+>", html, flags=re.I):
        name = (_extract_attr(tag, "name") or _extract_attr(tag, "property")).lower()
        if name in {"citation_pdf_url", "dc.identifier", "pdf_url"}:
            content = _extract_attr(tag, "content")
            if content and (content.lower().endswith(".pdf") or "pdf" in content.lower() or "stamp.jsp" in content.lower()):
                candidates.append({"url": urllib.parse.urljoin(base_url, content), "source": source})

    for href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", html, flags=re.I):
        lowered = href.lower()
        if "/doi/pdf/" in lowered or "stamp.jsp" in lowered or lowered.endswith(".pdf"):
            candidates.append({"url": urllib.parse.urljoin(base_url, href), "source": source})
    return _dedupe_candidates(candidates)


def _arxiv_id_from_text(value: str) -> str:
    text = value or ""
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", text, re.I)
    if match:
        return match.group(1).removesuffix(".pdf")
    match = re.search(r"\b([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)\b", text)
    if match:
        return match.group(1)
    return ""


def arxiv_pdf_candidate(paper: Dict[str, Any]) -> str:
    sources = {str(source).lower() for source in paper.get("sources", [])}
    source_id = str(paper.get("source_id", "") or "")
    url = str(paper.get("url", "") or "")
    raw = paper.get("raw") or {}
    raw_arxiv_id = ""
    if isinstance(raw, dict):
        raw_arxiv_id = _clean(raw.get("arxiv_id", ""))
    arxiv_id = _arxiv_id_from_text(" ".join([source_id, url, raw_arxiv_id]))
    if arxiv_id and ("arxiv" in sources or "arxiv.org" in url or raw_arxiv_id):
        return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    return ""


def _semantic_scholar_candidates(paper: Dict[str, Any]) -> tuple[list[Dict[str, str]], Dict[str, str]]:
    params = {
        "query": paper.get("title", ""),
        "limit": "5",
        "fields": "title,year,venue,url,externalIds,openAccessPdf",
    }
    url = SEMANTIC_SCHOLAR_SEARCH + "?" + urllib.parse.urlencode(params)
    payload = http_get_json(url, timeout=15)
    candidates: list[Dict[str, str]] = []
    metadata: Dict[str, str] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or not _is_good_match(paper, _clean(item.get("title", "")), item.get("year")):
            continue
        metadata["semantic_scholar_id"] = _clean(item.get("paperId", ""))
        external_ids = item.get("externalIds") or {}
        if isinstance(external_ids, dict):
            if external_ids.get("DOI"):
                metadata["doi"] = _normalize_doi(str(external_ids.get("DOI")))
            if external_ids.get("ArXiv"):
                candidates.append({"url": f"https://arxiv.org/pdf/{external_ids['ArXiv']}.pdf", "source": "Semantic Scholar arXiv"})
        open_access_pdf = item.get("openAccessPdf") or {}
        if isinstance(open_access_pdf, dict) and open_access_pdf.get("url"):
            candidates.append({"url": _clean(open_access_pdf.get("url")), "source": "Semantic Scholar"})
        if _is_publisher_url(_clean(item.get("url", ""))):
            metadata.update(_publisher_metadata_from_url(_clean(item.get("url", ""))))
        break
    return candidates, metadata


def _openalex_candidates(paper: Dict[str, Any]) -> tuple[list[Dict[str, str]], Dict[str, str]]:
    params = {
        "search": paper.get("title", ""),
        "per-page": "5",
    }
    url = OPENALEX_WORKS + "?" + urllib.parse.urlencode(params)
    payload = http_get_json(url, timeout=15)
    candidates: list[Dict[str, str]] = []
    metadata: Dict[str, str] = {}
    for item in payload.get("results", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or not _is_good_match(paper, _clean(item.get("title", "")), item.get("publication_year")):
            continue
        metadata["openalex_id"] = _clean(item.get("id", ""))
        if item.get("doi"):
            metadata["doi"] = _normalize_doi(str(item.get("doi")))
        locations = []
        if isinstance(item.get("best_oa_location"), dict):
            locations.append(item["best_oa_location"])
        if isinstance(item.get("primary_location"), dict):
            locations.append(item["primary_location"])
        if isinstance(item.get("locations"), list):
            locations.extend(location for location in item["locations"] if isinstance(location, dict))
        for location in locations:
            pdf_url = _clean(location.get("pdf_url", ""))
            if pdf_url:
                candidates.append({"url": pdf_url, "source": "OpenAlex"})
            landing_url = _clean(location.get("landing_page_url", ""))
            if landing_url and not metadata.get("publisher_url"):
                metadata.update(_publisher_metadata_from_url(landing_url))
        break
    return candidates, metadata


def _crossref_candidates(paper: Dict[str, Any]) -> tuple[list[Dict[str, str]], Dict[str, str]]:
    params = {
        "query.title": paper.get("title", ""),
        "rows": "5",
    }
    url = CROSSREF_WORKS + "?" + urllib.parse.urlencode(params)
    payload = http_get_json(url, timeout=15)
    items = ((payload.get("message") or {}).get("items") or []) if isinstance(payload, dict) else []
    candidates: list[Dict[str, str]] = []
    metadata: Dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        title_values = item.get("title") or []
        candidate_title = _clean(title_values[0] if title_values else "")
        year_parts = (((item.get("published-print") or item.get("published-online") or {}).get("date-parts")) or [[None]])[0]
        candidate_year = year_parts[0] if year_parts else None
        if not _is_good_match(paper, candidate_title, candidate_year):
            continue
        if item.get("DOI"):
            metadata["doi"] = _normalize_doi(str(item.get("DOI")))
        if item.get("URL"):
            metadata.update(_publisher_metadata_from_url(str(item.get("URL"))))
        for link in item.get("link", []) or []:
            if not isinstance(link, dict):
                continue
            if "pdf" in _clean(link.get("content-type", "")).lower() and link.get("URL"):
                candidates.append({"url": _clean(link.get("URL")), "source": "Crossref"})
        break
    return candidates, metadata


def _unpaywall_candidates(doi: str, email: str) -> list[Dict[str, str]]:
    doi = _normalize_doi(doi)
    email = _clean(email)
    if not doi or not email:
        return []
    url = UNPAYWALL_API + urllib.parse.quote(doi, safe="") + "?" + urllib.parse.urlencode({"email": email})
    payload = http_get_json(url, timeout=15)
    candidates: list[Dict[str, str]] = []
    locations = []
    if isinstance(payload, dict) and isinstance(payload.get("best_oa_location"), dict):
        locations.append(payload["best_oa_location"])
    if isinstance(payload, dict) and isinstance(payload.get("oa_locations"), list):
        locations.extend(location for location in payload["oa_locations"] if isinstance(location, dict))
    for location in locations:
        pdf_url = _clean(location.get("url_for_pdf", ""))
        if pdf_url:
            candidates.append({"url": pdf_url, "source": "Unpaywall"})
    return candidates


def _dedupe_candidates(candidates: Iterable[Dict[str, str]]) -> list[Dict[str, str]]:
    seen: set[str] = set()
    result: list[Dict[str, str]] = []
    for candidate in candidates:
        url = _clean(candidate.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        result.append({"url": url, "source": _clean(candidate.get("source", "resolver"))})
    return result


def resolve_pdf_for_paper(paper: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    checked_at = db.utc_now()
    metadata: Dict[str, Any] = {
        "doi": paper.get("doi", ""),
        "semantic_scholar_id": paper.get("semantic_scholar_id", ""),
        "openalex_id": paper.get("openalex_id", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "pdf_source": paper.get("pdf_source", ""),
        "pdf_status": "found" if paper.get("pdf_url") else "not_found",
        "pdf_checked_at": checked_at,
        "publisher_url": paper.get("publisher_url", ""),
        "publisher_source": paper.get("publisher_source", ""),
        "publisher_pdf_url": paper.get("publisher_pdf_url", ""),
        "publisher_pdf_source": paper.get("publisher_pdf_source", ""),
        "publisher_status": "found" if paper.get("publisher_url") else "not_found",
    }

    candidates: list[Dict[str, str]] = []
    if paper.get("pdf_url"):
        candidates.append({"url": str(paper["pdf_url"]), "source": str(paper.get("pdf_source") or "existing")})
    arxiv_candidate = arxiv_pdf_candidate(paper)
    if arxiv_candidate:
        candidates.append({"url": arxiv_candidate, "source": "arXiv"})

    resolver_config = config.get("pdf_resolver", {})
    for candidate in _dedupe_candidates(candidates):
        verified = verify_pdf_url(candidate["url"])
        if verified:
            metadata["pdf_url"] = verified
            metadata["pdf_source"] = candidate["source"]
            metadata["pdf_status"] = "found"
            return metadata
    if paper.get("pdf_url"):
        metadata["pdf_status"] = "broken"

    try:
        if _ensure_publisher_access_metadata(metadata):
            return metadata
    except (SourceError, ValueError, KeyError, TypeError):
        pass

    providers = [
        _semantic_scholar_candidates,
        _openalex_candidates,
        _crossref_candidates,
    ]
    for provider in providers:
        try:
            provider_candidates, provider_metadata = provider(paper)
        except (SourceError, ValueError, KeyError, TypeError):
            continue
        for key, value in provider_metadata.items():
            if value and not metadata.get(key):
                metadata[key] = value
        for candidate in _dedupe_candidates(provider_candidates):
            verified = verify_pdf_url(candidate["url"])
            if verified:
                metadata["pdf_url"] = verified
                metadata["pdf_source"] = candidate["source"]
                metadata["pdf_status"] = "found"
                return metadata
        delay = float(resolver_config.get("request_delay_seconds", 1.0) or 0)
        if delay:
            time.sleep(delay)

    try:
        unpaywall_candidates = _unpaywall_candidates(
            str(metadata.get("doi", "") or ""),
            str(resolver_config.get("unpaywall_email", "") or ""),
        )
    except (SourceError, ValueError, KeyError, TypeError):
        unpaywall_candidates = []
    for candidate in _dedupe_candidates(unpaywall_candidates):
        verified = verify_pdf_url(candidate["url"])
        if verified:
            metadata["pdf_url"] = verified
            metadata["pdf_source"] = candidate["source"]
            metadata["pdf_status"] = "found"
            return metadata

    try:
        _ensure_publisher_access_metadata(metadata)
    except (SourceError, ValueError, KeyError, TypeError):
        pass
    if metadata.get("publisher_url"):
        for candidate in _publisher_pdf_candidates(str(metadata["publisher_url"])):
            verified = verify_pdf_url(candidate["url"])
            if verified:
                metadata["pdf_url"] = verified
                metadata["pdf_source"] = candidate["source"] + " public PDF"
                metadata["pdf_status"] = "found"
                return metadata
        metadata["publisher_status"] = "found"

    metadata["pdf_url"] = str(paper.get("pdf_url", "") or "")
    metadata["pdf_source"] = str(paper.get("pdf_source", "") or "")
    metadata["pdf_status"] = "broken" if paper.get("pdf_url") else "not_found"
    if metadata.get("publisher_url") and not metadata.get("publisher_source"):
        metadata["publisher_source"] = publisher_source_for_url(str(metadata["publisher_url"])) or "publisher"
    return metadata


def resolve_unpaywall_only_for_paper(paper: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    checked_at = db.utc_now()
    metadata: Dict[str, Any] = {
        "doi": paper.get("doi", ""),
        "semantic_scholar_id": paper.get("semantic_scholar_id", ""),
        "openalex_id": paper.get("openalex_id", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "pdf_source": paper.get("pdf_source", ""),
        "pdf_status": "found" if paper.get("pdf_url") else "not_found",
        "pdf_checked_at": checked_at,
        "publisher_url": paper.get("publisher_url", ""),
        "publisher_source": paper.get("publisher_source", ""),
        "publisher_pdf_url": paper.get("publisher_pdf_url", ""),
        "publisher_pdf_source": paper.get("publisher_pdf_source", ""),
        "publisher_status": "found" if paper.get("publisher_url") else "not_found",
    }
    resolver_config = config.get("pdf_resolver", {})
    try:
        candidates = _unpaywall_candidates(
            str(metadata.get("doi", "") or ""),
            str(resolver_config.get("unpaywall_email", "") or ""),
        )
    except (SourceError, ValueError, KeyError, TypeError):
        candidates = []
    for candidate in _dedupe_candidates(candidates):
        verified = verify_pdf_url(candidate["url"])
        if verified:
            metadata["pdf_url"] = verified
            metadata["pdf_source"] = candidate["source"]
            metadata["pdf_status"] = "found"
            return metadata
    metadata["pdf_url"] = str(paper.get("pdf_url", "") or "")
    metadata["pdf_source"] = str(paper.get("pdf_source", "") or "")
    metadata["pdf_status"] = "broken" if paper.get("pdf_url") else "not_found"
    return metadata


def resolve_publisher_only_for_paper(paper: Dict[str, Any], _config: Dict[str, Any]) -> Dict[str, Any]:
    checked_at = db.utc_now()
    metadata: Dict[str, Any] = {
        "doi": paper.get("doi", ""),
        "semantic_scholar_id": paper.get("semantic_scholar_id", ""),
        "openalex_id": paper.get("openalex_id", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "pdf_source": paper.get("pdf_source", ""),
        "pdf_status": "found" if paper.get("pdf_url") else "not_found",
        "pdf_checked_at": checked_at,
        "publisher_url": paper.get("publisher_url", ""),
        "publisher_source": paper.get("publisher_source", ""),
        "publisher_pdf_url": paper.get("publisher_pdf_url", ""),
        "publisher_pdf_source": paper.get("publisher_pdf_source", ""),
        "publisher_status": "found" if paper.get("publisher_url") else "not_found",
    }
    if not metadata.get("publisher_url"):
        metadata.update(_publisher_metadata_from_doi(str(metadata.get("doi", "") or "")))
    if metadata.get("publisher_url"):
        if not metadata.get("publisher_pdf_url"):
            metadata.update(
                publisher_access_pdf_url(
                    str(metadata.get("doi", "") or ""),
                    str(metadata.get("publisher_url", "") or ""),
                )
            )
        for candidate in _publisher_pdf_candidates(str(metadata["publisher_url"])):
            verified = verify_pdf_url(candidate["url"])
            if verified:
                metadata["pdf_url"] = verified
                metadata["pdf_source"] = candidate["source"] + " public PDF"
                metadata["pdf_status"] = "found"
                return metadata
        metadata["publisher_status"] = "found"
        if not metadata.get("publisher_source"):
            metadata["publisher_source"] = publisher_source_for_url(str(metadata["publisher_url"])) or "publisher"
    return metadata


def resolve_missing_pdfs(
    config_path: str | None = None,
    limit: int | None = None,
    force: bool = False,
    unpaywall_only: bool = False,
    publisher_only: bool = False,
) -> Dict[str, Any]:
    config = load_config(config_path)
    configure_network(config.get("network", {}))
    if not config.get("pdf_resolver", {}).get("enabled", True):
        return {"checked": 0, "found": 0, "not_found": 0, "disabled": True}
    db_path = config["storage"]["database_path_resolved"]
    db.init_db(db_path)
    resolver_config = config.get("pdf_resolver", {})
    resolved_limit = int(limit or resolver_config.get("backfill_limit", 200))
    summary = {"checked": 0, "found": 0, "access_pages": 0, "not_found": 0, "broken": 0}

    with db.connect(db_path) as conn:
        papers = db.list_papers_for_pdf_resolution(conn, limit=resolved_limit, force=force)
        for paper in papers:
            if publisher_only:
                metadata = resolve_publisher_only_for_paper(paper, config)
            elif unpaywall_only:
                metadata = resolve_unpaywall_only_for_paper(paper, config)
            else:
                metadata = resolve_pdf_for_paper(paper, config)
            db.update_pdf_metadata(conn, int(paper["id"]), metadata)
            conn.commit()
            summary["checked"] += 1
            status = metadata.get("pdf_status", "not_found")
            if status == "found":
                summary["found"] += 1
            elif metadata.get("publisher_url"):
                summary["access_pages"] += 1
            elif status == "broken":
                summary["broken"] += 1
            else:
                summary["not_found"] += 1
    return summary
