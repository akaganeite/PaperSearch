from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from . import db
from .classify import enrich_and_filter
from .config import load_config
from .llm_summary import summarize_saved_papers
from .pdf_resolver import resolve_missing_pdfs
from .sources import arxiv, cspapers, venue_preprints


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _state_key(source: str, interest_id: str, name: str) -> str:
    return f"{source}:{interest_id}:{name}"


def _arxiv_since(conn, config: Dict[str, Any], interest_id: str, bootstrap: bool) -> datetime:
    bootstrap_days = int(config.get("bootstrap", {}).get("arxiv_days", 30))
    overlap_days = int(config.get("scheduler", {}).get("overlap_days", 3))
    if bootstrap:
        return arxiv.bootstrap_since(bootstrap_days)
    last_success = db.get_state(conn, _state_key("arXiv", interest_id, "last_success_at"))
    parsed = _parse_datetime(last_success)
    if parsed is None:
        return arxiv.bootstrap_since(bootstrap_days)
    return parsed - timedelta(days=overlap_days)


def _process_papers(conn, raw_papers: list[Dict[str, Any]], interest: Dict[str, Any]) -> tuple[int, int]:
    kept = 0
    new_count = 0
    for raw_paper in raw_papers:
        keep, enriched, _reason = enrich_and_filter(raw_paper, interest)
        if not keep:
            continue
        kept += 1
        if db.upsert_paper(conn, enriched):
            new_count += 1
    return kept, new_count


def run_update(config_path: str | None = None, bootstrap: bool = False, source_filter: str = "") -> Dict[str, Any]:
    config = load_config(config_path)
    db_path = config["storage"]["database_path_resolved"]
    db.init_db(db_path)
    summary: Dict[str, Any] = {
        "bootstrap": bootstrap,
        "sources": {},
        "started_at": db.utc_now(),
        "finished_at": "",
    }

    with db.connect(db_path) as conn:
        for interest in config.get("interests", []):
            interest_id = interest["id"]

            if source_filter in ("", "arxiv", "arXiv"):
                log_id = db.start_run_log(conn, "arXiv", interest_id)
                fetched = kept = new_count = 0
                try:
                    since = _arxiv_since(conn, config, interest_id, bootstrap)
                    fetched_papers = arxiv.fetch(
                        interest,
                        since=since,
                        max_results=int(config.get("bootstrap", {}).get("arxiv_max_results", 200)),
                    )
                    fetched = len(fetched_papers)
                    kept, new_count = _process_papers(conn, fetched_papers, interest)
                    db.set_state(conn, _state_key("arXiv", interest_id, "last_success_at"), db.utc_now())
                    db.finish_run_log(conn, log_id, "success", fetched, kept, new_count)
                    summary["sources"].setdefault("arXiv", {"fetched": 0, "kept": 0, "new": 0})
                    summary["sources"]["arXiv"]["fetched"] += fetched
                    summary["sources"]["arXiv"]["kept"] += kept
                    summary["sources"]["arXiv"]["new"] += new_count
                except Exception as exc:  # noqa: BLE001 - keep other sources running.
                    db.finish_run_log(conn, log_id, "error", fetched, kept, new_count, str(exc))
                    summary["sources"].setdefault("arXiv", {"fetched": 0, "kept": 0, "new": 0, "error": ""})
                    summary["sources"]["arXiv"]["error"] = str(exc)

            if source_filter in ("", "cspapers", "csPapers"):
                log_id = db.start_run_log(conn, "csPapers", interest_id)
                fetched = kept = new_count = 0
                try:
                    bootstrap_cfg = config.get("bootstrap", {})
                    fetched_papers = cspapers.fetch(
                        interest,
                        year_from=int(bootstrap_cfg.get("cspapers_year_from", 2024)),
                        year_to=int(bootstrap_cfg.get("cspapers_year_to", 2026)),
                        pages_per_query=int(bootstrap_cfg.get("cspapers_pages_per_query", 3)),
                        max_abstract_fetch=int(bootstrap_cfg.get("cspapers_max_abstract_fetch", 80)),
                    )
                    fetched = len(fetched_papers)
                    kept, new_count = _process_papers(conn, fetched_papers, interest)
                    db.set_state(conn, _state_key("csPapers", interest_id, "last_success_at"), db.utc_now())
                    db.finish_run_log(conn, log_id, "success", fetched, kept, new_count)
                    summary["sources"].setdefault("csPapers", {"fetched": 0, "kept": 0, "new": 0})
                    summary["sources"]["csPapers"]["fetched"] += fetched
                    summary["sources"]["csPapers"]["kept"] += kept
                    summary["sources"]["csPapers"]["new"] += new_count
                except Exception as exc:  # noqa: BLE001 - keep other sources running.
                    db.finish_run_log(conn, log_id, "error", fetched, kept, new_count, str(exc))
                    summary["sources"].setdefault("csPapers", {"fetched": 0, "kept": 0, "new": 0, "error": ""})
                    summary["sources"]["csPapers"]["error"] = str(exc)

            if source_filter in ("", "venue-preprints", "venue_preprints", "venues", "VenuePreprints"):
                log_id = db.start_run_log(conn, "VenuePreprints", interest_id)
                fetched = kept = new_count = 0
                try:
                    fetched_papers = venue_preprints.fetch(config.get("venue_preprints", {}), interest)
                    fetched = len(fetched_papers)
                    kept, new_count = _process_papers(conn, fetched_papers, interest)
                    db.set_state(conn, _state_key("VenuePreprints", interest_id, "last_success_at"), db.utc_now())
                    db.finish_run_log(conn, log_id, "success", fetched, kept, new_count)
                    summary["sources"].setdefault("VenuePreprints", {"fetched": 0, "kept": 0, "new": 0})
                    summary["sources"]["VenuePreprints"]["fetched"] += fetched
                    summary["sources"]["VenuePreprints"]["kept"] += kept
                    summary["sources"]["VenuePreprints"]["new"] += new_count
                except Exception as exc:  # noqa: BLE001 - keep other sources running.
                    db.finish_run_log(conn, log_id, "error", fetched, kept, new_count, str(exc))
                    summary["sources"].setdefault("VenuePreprints", {"fetched": 0, "kept": 0, "new": 0, "error": ""})
                    summary["sources"]["VenuePreprints"]["error"] = str(exc)

        summary["finished_at"] = db.utc_now()
        summary["stats"] = db.get_stats(conn)
    resolver_cfg = config.get("pdf_resolver", {})
    if resolver_cfg.get("enabled", True):
        summary["pdfs"] = resolve_missing_pdfs(
            config_path,
            limit=int(resolver_cfg.get("run_after_update_limit", 50)),
            force=False,
        )
    llm_summary_cfg = config.get("llm_summary", {})
    if llm_summary_cfg.get("enabled", True) and llm_summary_cfg.get("run_after_update", True):
        summary["llm_summaries"] = summarize_saved_papers(
            config_path,
            limit=int(llm_summary_cfg.get("max_per_update", 10)),
        )
    return summary


def reclassify_existing(config_path: str | None = None) -> Dict[str, Any]:
    config = load_config(config_path)
    db_path = config["storage"]["database_path_resolved"]
    db.init_db(db_path)
    interest = config.get("interests", [])[0]
    summary = {"checked": 0, "kept": 0, "unmatched": 0}

    with db.connect(db_path) as conn:
        for paper in db.list_all_papers(conn):
            summary["checked"] += 1
            raw_paper = {
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", ""),
                "authors": paper.get("authors", []),
                "source": (paper.get("sources") or [""])[0],
                "source_id": paper.get("source_id", ""),
                "url": paper.get("url", ""),
                "pdf_url": paper.get("pdf_url", ""),
                "venue": paper.get("venue", ""),
                "year": paper.get("year"),
                "published_at": paper.get("published_at", ""),
                "updated_at": paper.get("updated_at", ""),
                "source_categories": paper.get("source_categories", []),
                "raw": paper.get("raw", {}),
            }
            keep, enriched, reason = enrich_and_filter(raw_paper, interest)
            if keep:
                summary["kept"] += 1
                db.update_classification(
                    conn,
                    int(paper["id"]),
                    enriched["task_labels"],
                    enriched["target_labels"],
                    float(enriched["relevance_score"]),
                    enriched["recommendation_reason"],
                )
            else:
                summary["unmatched"] += 1
                db.update_classification(
                    conn,
                    int(paper["id"]),
                    ["其他"],
                    paper.get("target_labels", ["Unknown/Unclear"]),
                    0.0,
                    f"重新分类未命中当前兴趣规则：{reason}",
                )
        summary["stats"] = db.get_stats(conn)
    return summary
