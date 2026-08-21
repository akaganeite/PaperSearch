from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from . import db
from .classify import enrich_and_filter
from .config import load_config
from .llm_prefilter import enqueue_candidates, merge_summaries, process_review_queue, public_summary
from .llm_summary import summarize_saved_papers
from .pdf_resolver import resolve_missing_pdfs
from .sources import arxiv, cspapers, venue_preprints
from .sources.common import configure_network


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


def _process_prefiltered_papers(
    db_path: str,
    raw_papers: list[Dict[str, Any]],
    interest: Dict[str, Any],
    config: Dict[str, Any],
    remaining_reviews: int,
) -> tuple[int, int, int, Dict[str, Any]]:
    enqueued = enqueue_candidates(db_path, raw_papers, interest, config)
    reviewed = process_review_queue(db_path, interest, config, remaining_reviews)
    combined = merge_summaries(enqueued, reviewed)
    current_keys = enqueued.get("_candidate_keys", set())
    kept = len(current_keys.intersection(combined.get("_accepted_keys", set())))
    new_count = len(current_keys.intersection(combined.get("_new_keys", set())))
    return kept, new_count, int(reviewed.get("processed", 0) or 0), combined


def run_update(config_path: str | None = None, bootstrap: bool = False, source_filter: str = "") -> Dict[str, Any]:
    config = load_config(config_path)
    configure_network(config.get("network", {}))
    db_path = config["storage"]["database_path_resolved"]
    db.init_db(db_path)
    summary: Dict[str, Any] = {
        "bootstrap": bootstrap,
        "sources": {},
        "started_at": db.utc_now(),
        "finished_at": "",
    }

    prefilter_cfg = config.get("llm_prefilter", {})
    prefilter_enabled = bool(prefilter_cfg.get("enabled", False))
    remaining_reviews = max(0, int(prefilter_cfg.get("max_per_update", 100) or 100))
    prefilter_summary: Dict[str, Any] | None = None

    with db.connect(db_path) as conn:
        for interest in config.get("interests", []):
            interest_id = interest["id"]

            if prefilter_enabled and remaining_reviews > 0:
                conn.commit()
                retried = process_review_queue(db_path, interest, config, remaining_reviews)
                remaining_reviews -= int(retried.get("processed", 0) or 0)
                prefilter_summary = merge_summaries(prefilter_summary or {}, retried)

            if source_filter in ("", "arxiv", "arXiv"):
                log_id = db.start_run_log(conn, "arXiv", interest_id)
                conn.commit()
                fetched = kept = new_count = 0
                try:
                    since = _arxiv_since(conn, config, interest_id, bootstrap)
                    arxiv_cfg = config.get("sources", {}).get("arxiv", {})
                    network_cfg = config.get("network", {})
                    fetched_papers = arxiv.fetch(
                        interest,
                        since=since,
                        max_results=int(config.get("bootstrap", {}).get("arxiv_max_results", 200)),
                        timeout_seconds=int(arxiv_cfg.get("timeout_seconds", network_cfg.get("arxiv_timeout_seconds", 12))),
                        max_runtime_seconds=int(arxiv_cfg.get("max_runtime_seconds", network_cfg.get("arxiv_max_runtime_seconds", 180))),
                    )
                    fetched = len(fetched_papers)
                    if prefilter_enabled:
                        kept, new_count, used, source_prefilter = _process_prefiltered_papers(
                            db_path,
                            fetched_papers,
                            interest,
                            config,
                            remaining_reviews,
                        )
                        remaining_reviews -= used
                        prefilter_summary = merge_summaries(prefilter_summary or {}, source_prefilter)
                    else:
                        kept, new_count = _process_papers(conn, fetched_papers, interest)
                    db.set_state(conn, _state_key("arXiv", interest_id, "last_success_at"), db.utc_now())
                    db.finish_run_log(conn, log_id, "success", fetched, kept, new_count)
                    conn.commit()
                    summary["sources"].setdefault("arXiv", {"fetched": 0, "kept": 0, "new": 0})
                    summary["sources"]["arXiv"]["fetched"] += fetched
                    summary["sources"]["arXiv"]["kept"] += kept
                    summary["sources"]["arXiv"]["new"] += new_count
                except Exception as exc:  # noqa: BLE001 - keep other sources running.
                    db.finish_run_log(conn, log_id, "error", fetched, kept, new_count, str(exc))
                    conn.commit()
                    summary["sources"].setdefault("arXiv", {"fetched": 0, "kept": 0, "new": 0, "error": ""})
                    summary["sources"]["arXiv"]["error"] = str(exc)

            if source_filter in ("", "cspapers", "csPapers"):
                log_id = db.start_run_log(conn, "csPapers", interest_id)
                conn.commit()
                fetched = kept = new_count = 0
                try:
                    bootstrap_cfg = config.get("bootstrap", {})
                    cspapers_cfg = config.get("sources", {}).get("cspapers", {})
                    network_cfg = config.get("network", {})
                    fetched_papers = cspapers.fetch(
                        interest,
                        year_from=int(bootstrap_cfg.get("cspapers_year_from", 2024)),
                        year_to=int(bootstrap_cfg.get("cspapers_year_to", 2026)),
                        pages_per_query=int(bootstrap_cfg.get("cspapers_pages_per_query", 3)),
                        max_abstract_fetch=int(bootstrap_cfg.get("cspapers_max_abstract_fetch", 80)),
                        timeout_seconds=int(cspapers_cfg.get("timeout_seconds", network_cfg.get("cspapers_timeout_seconds", 10))),
                        max_runtime_seconds=int(cspapers_cfg.get("max_runtime_seconds", network_cfg.get("cspapers_max_runtime_seconds", 300))),
                        max_consecutive_errors=int(cspapers_cfg.get("max_consecutive_errors", 20)),
                    )
                    fetched = len(fetched_papers)
                    if prefilter_enabled:
                        kept, new_count, used, source_prefilter = _process_prefiltered_papers(
                            db_path,
                            fetched_papers,
                            interest,
                            config,
                            remaining_reviews,
                        )
                        remaining_reviews -= used
                        prefilter_summary = merge_summaries(prefilter_summary or {}, source_prefilter)
                    else:
                        kept, new_count = _process_papers(conn, fetched_papers, interest)
                    db.set_state(conn, _state_key("csPapers", interest_id, "last_success_at"), db.utc_now())
                    db.finish_run_log(conn, log_id, "success", fetched, kept, new_count)
                    conn.commit()
                    summary["sources"].setdefault("csPapers", {"fetched": 0, "kept": 0, "new": 0})
                    summary["sources"]["csPapers"]["fetched"] += fetched
                    summary["sources"]["csPapers"]["kept"] += kept
                    summary["sources"]["csPapers"]["new"] += new_count
                except Exception as exc:  # noqa: BLE001 - keep other sources running.
                    db.finish_run_log(conn, log_id, "error", fetched, kept, new_count, str(exc))
                    conn.commit()
                    summary["sources"].setdefault("csPapers", {"fetched": 0, "kept": 0, "new": 0, "error": ""})
                    summary["sources"]["csPapers"]["error"] = str(exc)

            if source_filter in ("", "venue-preprints", "venue_preprints", "venues", "VenuePreprints"):
                log_id = db.start_run_log(conn, "VenuePreprints", interest_id)
                conn.commit()
                fetched = kept = new_count = 0
                try:
                    fetched_papers = venue_preprints.fetch(config.get("venue_preprints", {}), interest)
                    fetched = len(fetched_papers)
                    if prefilter_enabled:
                        kept, new_count, used, source_prefilter = _process_prefiltered_papers(
                            db_path,
                            fetched_papers,
                            interest,
                            config,
                            remaining_reviews,
                        )
                        remaining_reviews -= used
                        prefilter_summary = merge_summaries(prefilter_summary or {}, source_prefilter)
                    else:
                        kept, new_count = _process_papers(conn, fetched_papers, interest)
                    db.set_state(conn, _state_key("VenuePreprints", interest_id, "last_success_at"), db.utc_now())
                    db.finish_run_log(conn, log_id, "success", fetched, kept, new_count)
                    conn.commit()
                    summary["sources"].setdefault("VenuePreprints", {"fetched": 0, "kept": 0, "new": 0})
                    summary["sources"]["VenuePreprints"]["fetched"] += fetched
                    summary["sources"]["VenuePreprints"]["kept"] += kept
                    summary["sources"]["VenuePreprints"]["new"] += new_count
                except Exception as exc:  # noqa: BLE001 - keep other sources running.
                    db.finish_run_log(conn, log_id, "error", fetched, kept, new_count, str(exc))
                    conn.commit()
                    summary["sources"].setdefault("VenuePreprints", {"fetched": 0, "kept": 0, "new": 0, "error": ""})
                    summary["sources"]["VenuePreprints"]["error"] = str(exc)

        if prefilter_enabled:
            prefilter_public = public_summary(prefilter_summary or {})
            queue_status = db.get_llm_prefilter_status(conn)
            prefilter_public["pending"] = queue_status["pending"]
            prefilter_public["queue"] = queue_status
            prefilter_public["remaining_budget"] = remaining_reviews
            summary["llm_prefilter"] = prefilter_public
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
