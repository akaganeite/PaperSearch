from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .text import normalize_title


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                title_norm TEXT NOT NULL UNIQUE,
                abstract TEXT NOT NULL DEFAULT '',
                authors_json TEXT NOT NULL DEFAULT '[]',
                sources_json TEXT NOT NULL DEFAULT '[]',
                source_id TEXT NOT NULL DEFAULT '',
                doi TEXT NOT NULL DEFAULT '',
                semantic_scholar_id TEXT NOT NULL DEFAULT '',
                openalex_id TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                pdf_url TEXT NOT NULL DEFAULT '',
                pdf_source TEXT NOT NULL DEFAULT '',
                pdf_status TEXT NOT NULL DEFAULT 'unchecked',
                pdf_checked_at TEXT NOT NULL DEFAULT '',
                publisher_url TEXT NOT NULL DEFAULT '',
                publisher_source TEXT NOT NULL DEFAULT '',
                publisher_pdf_url TEXT NOT NULL DEFAULT '',
                publisher_pdf_source TEXT NOT NULL DEFAULT '',
                publisher_status TEXT NOT NULL DEFAULT 'unchecked',
                venue TEXT NOT NULL DEFAULT '',
                year INTEGER,
                published_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                source_categories_json TEXT NOT NULL DEFAULT '[]',
                task_labels_json TEXT NOT NULL DEFAULT '[]',
                target_labels_json TEXT NOT NULL DEFAULT '[]',
                relevance_score REAL NOT NULL DEFAULT 0,
                recommendation_reason TEXT NOT NULL DEFAULT '',
                is_read INTEGER NOT NULL DEFAULT 0,
                is_saved INTEGER NOT NULL DEFAULT 0,
                is_hidden INTEGER NOT NULL DEFAULT 0,
                llm_summary_json TEXT NOT NULL DEFAULT '{}',
                llm_summary_model TEXT NOT NULL DEFAULT '',
                llm_summary_generated_at TEXT NOT NULL DEFAULT '',
                llm_summary_status TEXT NOT NULL DEFAULT 'pending',
                llm_summary_error TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_papers_score ON papers(relevance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_papers_seen ON papers(first_seen_at DESC);
            CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published_at DESC);

            CREATE TABLE IF NOT EXISTS deleted_papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                title_norm TEXT NOT NULL UNIQUE,
                abstract TEXT NOT NULL DEFAULT '',
                authors_json TEXT NOT NULL DEFAULT '[]',
                sources_json TEXT NOT NULL DEFAULT '[]',
                source_id TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                pdf_url TEXT NOT NULL DEFAULT '',
                publisher_url TEXT NOT NULL DEFAULT '',
                publisher_source TEXT NOT NULL DEFAULT '',
                publisher_pdf_url TEXT NOT NULL DEFAULT '',
                publisher_pdf_source TEXT NOT NULL DEFAULT '',
                venue TEXT NOT NULL DEFAULT '',
                year INTEGER,
                published_at TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT '',
                source_categories_json TEXT NOT NULL DEFAULT '[]',
                deleted_at TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_deleted_papers_deleted_at ON deleted_papers(deleted_at DESC);

            CREATE TABLE IF NOT EXISTS source_states (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                interest_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                kept_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        _ensure_column(conn, "papers", "doi", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "semantic_scholar_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "openalex_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "pdf_source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "pdf_status", "TEXT NOT NULL DEFAULT 'unchecked'")
        _ensure_column(conn, "papers", "pdf_checked_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "publisher_url", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "publisher_source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "publisher_pdf_url", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "publisher_pdf_source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "publisher_status", "TEXT NOT NULL DEFAULT 'unchecked'")
        _ensure_column(conn, "papers", "llm_summary_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "papers", "llm_summary_model", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "llm_summary_generated_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "papers", "llm_summary_status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "papers", "llm_summary_error", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "deleted_papers", "publisher_url", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "deleted_papers", "publisher_source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "deleted_papers", "publisher_pdf_url", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "deleted_papers", "publisher_pdf_source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "deleted_papers", "first_seen_at", "TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted_papers_first_seen ON deleted_papers(first_seen_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_saved_summary ON papers(is_saved, llm_summary_status)")
        conn.execute(
            """
            UPDATE papers
            SET pdf_status = 'found',
                pdf_source = CASE WHEN pdf_source = '' THEN 'source' ELSE pdf_source END,
                pdf_checked_at = CASE WHEN pdf_checked_at = '' THEN ? ELSE pdf_checked_at END
            WHERE pdf_url != '' AND pdf_status != 'found'
            """,
            (utc_now(),),
        )
        conn.execute("UPDATE deleted_papers SET first_seen_at = deleted_at WHERE first_seen_at = ''")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _merge_unique(*items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in items:
        for item in group:
            if not item:
                continue
            key = str(item).lower()
            if key not in seen:
                seen.add(key)
                merged.append(str(item))
    return merged


def upsert_paper(conn: sqlite3.Connection, paper: Dict[str, Any]) -> bool:
    now = utc_now()
    title = str(paper.get("title", "")).strip()
    if not title:
        return False

    title_norm = normalize_title(title)
    if is_deleted_title(conn, title_norm):
        return False

    source = str(paper.get("source", "")).strip()
    existing = conn.execute("SELECT * FROM papers WHERE title_norm = ?", (title_norm,)).fetchone()

    authors = paper.get("authors") or []
    categories = paper.get("source_categories") or []
    task_labels = paper.get("task_labels") or []
    target_labels = paper.get("target_labels") or []
    raw = paper.get("raw") or {}

    if existing is None:
        conn.execute(
            """
            INSERT INTO papers (
                title, title_norm, abstract, authors_json, sources_json, source_id, doi,
                semantic_scholar_id, openalex_id, url, pdf_url, pdf_source, pdf_status,
                pdf_checked_at, publisher_url, publisher_source, publisher_pdf_url,
                publisher_pdf_source, publisher_status, venue, year, published_at, updated_at,
                source_categories_json, task_labels_json, target_labels_json, relevance_score,
                recommendation_reason, first_seen_at, last_seen_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                title_norm,
                str(paper.get("abstract", "") or ""),
                _json_dumps(authors),
                _json_dumps([source] if source else []),
                str(paper.get("source_id", "") or ""),
                str(paper.get("doi", "") or ""),
                str(paper.get("semantic_scholar_id", "") or ""),
                str(paper.get("openalex_id", "") or ""),
                str(paper.get("url", "") or ""),
                str(paper.get("pdf_url", "") or ""),
                str(paper.get("pdf_source", "") or ("source" if paper.get("pdf_url") else "")),
                str(paper.get("pdf_status", "") or ("found" if paper.get("pdf_url") else "unchecked")),
                str(paper.get("pdf_checked_at", "") or ""),
                str(paper.get("publisher_url", "") or ""),
                str(paper.get("publisher_source", "") or ""),
                str(paper.get("publisher_pdf_url", "") or ""),
                str(paper.get("publisher_pdf_source", "") or ""),
                str(paper.get("publisher_status", "") or ("found" if paper.get("publisher_url") else "unchecked")),
                str(paper.get("venue", "") or ""),
                paper.get("year"),
                str(paper.get("published_at", "") or ""),
                str(paper.get("updated_at", "") or ""),
                _json_dumps(categories),
                _json_dumps(task_labels),
                _json_dumps(target_labels),
                float(paper.get("relevance_score", 0) or 0),
                str(paper.get("recommendation_reason", "") or ""),
                now,
                now,
                _json_dumps(raw),
            ),
        )
        return True

    existing_sources = _json_loads(existing["sources_json"], [])
    existing_categories = _json_loads(existing["source_categories_json"], [])
    existing_tasks = _json_loads(existing["task_labels_json"], [])
    existing_targets = _json_loads(existing["target_labels_json"], [])
    existing_raw = _json_loads(existing["raw_json"], {})

    abstract = str(paper.get("abstract", "") or "")
    if len(existing["abstract"] or "") >= len(abstract):
        abstract = existing["abstract"] or ""

    score = max(float(existing["relevance_score"] or 0), float(paper.get("relevance_score", 0) or 0))
    reason = paper.get("recommendation_reason") or existing["recommendation_reason"]

    merged_raw = dict(existing_raw)
    if source:
        merged_raw[source] = raw

    conn.execute(
        """
        UPDATE papers
        SET abstract = ?,
            authors_json = ?,
            sources_json = ?,
            source_id = COALESCE(NULLIF(source_id, ''), ?),
            doi = COALESCE(NULLIF(doi, ''), ?),
            semantic_scholar_id = COALESCE(NULLIF(semantic_scholar_id, ''), ?),
            openalex_id = COALESCE(NULLIF(openalex_id, ''), ?),
            url = COALESCE(NULLIF(url, ''), ?),
            pdf_url = COALESCE(NULLIF(pdf_url, ''), ?),
            pdf_source = COALESCE(NULLIF(pdf_source, ''), ?),
            pdf_status = CASE
                WHEN COALESCE(NULLIF(pdf_url, ''), ?) != '' THEN 'found'
                ELSE COALESCE(NULLIF(pdf_status, ''), ?)
            END,
            pdf_checked_at = COALESCE(NULLIF(pdf_checked_at, ''), ?),
            publisher_url = COALESCE(NULLIF(publisher_url, ''), ?),
            publisher_source = COALESCE(NULLIF(publisher_source, ''), ?),
            publisher_pdf_url = COALESCE(NULLIF(publisher_pdf_url, ''), ?),
            publisher_pdf_source = COALESCE(NULLIF(publisher_pdf_source, ''), ?),
            publisher_status = CASE
                WHEN COALESCE(NULLIF(publisher_url, ''), ?) != '' THEN 'found'
                ELSE COALESCE(NULLIF(publisher_status, ''), ?)
            END,
            venue = COALESCE(NULLIF(venue, ''), ?),
            year = COALESCE(year, ?),
            published_at = COALESCE(NULLIF(published_at, ''), ?),
            updated_at = ?,
            source_categories_json = ?,
            task_labels_json = ?,
            target_labels_json = ?,
            relevance_score = ?,
            recommendation_reason = ?,
            last_seen_at = ?,
            raw_json = ?
        WHERE id = ?
        """,
        (
            abstract,
            _json_dumps(authors or _json_loads(existing["authors_json"], [])),
            _json_dumps(_merge_unique(existing_sources, [source] if source else [])),
            str(paper.get("source_id", "") or ""),
            str(paper.get("doi", "") or ""),
            str(paper.get("semantic_scholar_id", "") or ""),
            str(paper.get("openalex_id", "") or ""),
            str(paper.get("url", "") or ""),
            str(paper.get("pdf_url", "") or ""),
            str(paper.get("pdf_source", "") or ("source" if paper.get("pdf_url") else "")),
            str(paper.get("pdf_url", "") or ""),
            str(paper.get("pdf_status", "") or "unchecked"),
            str(paper.get("pdf_checked_at", "") or ""),
            str(paper.get("publisher_url", "") or ""),
            str(paper.get("publisher_source", "") or ""),
            str(paper.get("publisher_pdf_url", "") or ""),
            str(paper.get("publisher_pdf_source", "") or ""),
            str(paper.get("publisher_url", "") or ""),
            str(paper.get("publisher_status", "") or "unchecked"),
            str(paper.get("venue", "") or ""),
            paper.get("year"),
            str(paper.get("published_at", "") or ""),
            str(paper.get("updated_at", "") or existing["updated_at"] or ""),
            _json_dumps(_merge_unique(existing_categories, categories)),
            _json_dumps(_merge_unique(existing_tasks, task_labels)),
            _json_dumps(_merge_unique(existing_targets, target_labels)),
            score,
            reason,
            now,
            _json_dumps(merged_raw),
            existing["id"],
        ),
    )
    return False


def _paper_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["authors"] = _json_loads(item.pop("authors_json"), [])
    item["sources"] = _json_loads(item.pop("sources_json"), [])
    item["source_categories"] = _json_loads(item.pop("source_categories_json"), [])
    item["task_labels"] = _json_loads(item.pop("task_labels_json"), [])
    item["target_labels"] = _json_loads(item.pop("target_labels_json"), [])
    item["raw"] = _json_loads(item.pop("raw_json"), {})
    item["llm_summary"] = _json_loads(item.pop("llm_summary_json", "{}"), {})
    item["is_read"] = bool(item["is_read"])
    item["is_saved"] = bool(item["is_saved"])
    item.pop("is_hidden", None)
    return item


def _deleted_paper_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["authors"] = _json_loads(item.pop("authors_json"), [])
    item["sources"] = _json_loads(item.pop("sources_json"), [])
    item["source_categories"] = _json_loads(item.pop("source_categories_json"), [])
    item["task_labels"] = ["Deleted"]
    item["target_labels"] = []
    item["raw"] = _json_loads(item.pop("raw_json"), {})
    item["relevance_score"] = 0
    item["recommendation_reason"] = "Deleted by user; future updates will skip this title."
    item["is_read"] = True
    item["is_saved"] = False
    item["is_deleted"] = True
    return item


def is_deleted_title(conn: sqlite3.Connection, title_or_norm: str) -> bool:
    title_norm = normalize_title(title_or_norm)
    if not title_norm:
        return False
    row = conn.execute("SELECT 1 FROM deleted_papers WHERE title_norm = ?", (title_norm,)).fetchone()
    return row is not None


def list_deleted_papers(
    conn: sqlite3.Connection,
    query: str = "",
    source: str = "",
    sort: str = "added_desc",
    limit: int = 200,
) -> list[Dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if query:
        pattern = f"%{query.lower()}%"
        clauses.append("(lower(title) LIKE ? OR lower(abstract) LIKE ? OR lower(venue) LIKE ?)")
        params.extend([pattern, pattern, pattern])
    if source:
        clauses.append("sources_json LIKE ?")
        params.append(f"%{source}%")
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    order_by = _paper_order_by(sort, deleted=True)
    rows = conn.execute(
        f"""
        SELECT * FROM deleted_papers
        {where_sql}
        ORDER BY {order_by}
        LIMIT ?
        """,
        (*params, max(1, min(limit, 1000))),
    ).fetchall()
    return [_deleted_paper_from_row(row) for row in rows]


def list_papers(
    conn: sqlite3.Connection,
    query: str = "",
    source: str = "",
    task: str = "",
    target: str = "",
    status: str = "",
    sort: str = "relevance",
    limit: int = 200,
) -> list[Dict[str, Any]]:
    if status == "deleted":
        return list_deleted_papers(conn, query=query, source=source, sort=sort, limit=limit)

    clauses = []
    params: list[Any] = []
    if query:
        pattern = f"%{query.lower()}%"
        clauses.append("(lower(title) LIKE ? OR lower(abstract) LIKE ? OR lower(venue) LIKE ?)")
        params.extend([pattern, pattern, pattern])
    if source:
        clauses.append("sources_json LIKE ?")
        params.append(f"%{source}%")
    if task:
        clauses.append("task_labels_json LIKE ?")
        params.append(f"%{task}%")
    if target:
        clauses.append("target_labels_json LIKE ?")
        params.append(f"%{target}%")
    if status == "unread":
        clauses.append("is_read = 0")
    elif status == "saved":
        clauses.append("is_saved = 1")

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    order_by = _paper_order_by(sort)
    rows = conn.execute(
        f"""
        SELECT * FROM papers
        {where_sql}
        ORDER BY {order_by}
        LIMIT ?
        """,
        (*params, max(1, min(limit, 1000))),
    ).fetchall()
    return [_paper_from_row(row) for row in rows]


def _paper_order_by(sort: str, deleted: bool = False) -> str:
    if sort == "added_asc":
        return "first_seen_at ASC, title ASC"
    if sort == "added_desc":
        return "first_seen_at DESC, title ASC"
    if sort == "published_desc" and not deleted:
        return "published_at DESC, relevance_score DESC, first_seen_at DESC"
    if sort == "published_asc" and not deleted:
        return "published_at ASC, relevance_score DESC, first_seen_at DESC"
    if deleted:
        return "deleted_at DESC, first_seen_at DESC, title ASC"
    return "relevance_score DESC, published_at DESC, first_seen_at DESC"


def list_all_papers(conn: sqlite3.Connection) -> list[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM papers ORDER BY id ASC").fetchall()
    return [_paper_from_row(row) for row in rows]


def list_saved_papers_for_summary(conn: sqlite3.Connection, limit: int = 10) -> list[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM papers
        WHERE is_saved = 1
          AND COALESCE(NULLIF(llm_summary_json, ''), '{}') = '{}'
        ORDER BY first_seen_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [_paper_from_row(row) for row in rows]


def list_papers_for_pdf_resolution(
    conn: sqlite3.Connection,
    limit: int = 100,
    force: bool = False,
) -> list[Dict[str, Any]]:
    where = "1 = 1" if force else "pdf_url = '' AND COALESCE(NULLIF(pdf_status, ''), 'unchecked') IN ('unchecked', 'broken')"
    rows = conn.execute(
        f"""
        SELECT * FROM papers
        WHERE {where}
        ORDER BY
            CASE pdf_status
                WHEN 'unchecked' THEN 0
                WHEN '' THEN 1
                WHEN 'broken' THEN 2
                ELSE 3
            END,
            relevance_score DESC,
            first_seen_at DESC
        LIMIT ?
        """,
        (max(1, min(limit, 1000)),),
    ).fetchall()
    return [_paper_from_row(row) for row in rows]


def update_pdf_metadata(conn: sqlite3.Connection, paper_id: int, metadata: Dict[str, Any]) -> None:
    pdf_url = str(metadata.get("pdf_url", "") or "")
    pdf_source = str(metadata.get("pdf_source", "") or "")
    pdf_status = str(metadata.get("pdf_status", "") or "not_found")
    publisher_url = str(metadata.get("publisher_url", "") or "")
    publisher_source = str(metadata.get("publisher_source", "") or "")
    publisher_pdf_url = str(metadata.get("publisher_pdf_url", "") or "")
    publisher_pdf_source = str(metadata.get("publisher_pdf_source", "") or "")
    publisher_status = str(metadata.get("publisher_status", "") or ("found" if publisher_url else "not_found"))
    conn.execute(
        """
        UPDATE papers
        SET doi = COALESCE(NULLIF(doi, ''), ?),
            semantic_scholar_id = COALESCE(NULLIF(semantic_scholar_id, ''), ?),
            openalex_id = COALESCE(NULLIF(openalex_id, ''), ?),
            pdf_url = CASE WHEN ? != '' THEN ? ELSE pdf_url END,
            pdf_source = CASE WHEN ? != '' THEN ? ELSE pdf_source END,
            pdf_status = ?,
            pdf_checked_at = ?,
            publisher_url = CASE WHEN ? != '' THEN ? ELSE publisher_url END,
            publisher_source = CASE WHEN ? != '' THEN ? ELSE publisher_source END,
            publisher_pdf_url = CASE WHEN ? != '' THEN ? ELSE publisher_pdf_url END,
            publisher_pdf_source = CASE WHEN ? != '' THEN ? ELSE publisher_pdf_source END,
            publisher_status = ?
        WHERE id = ?
        """,
        (
            str(metadata.get("doi", "") or ""),
            str(metadata.get("semantic_scholar_id", "") or ""),
            str(metadata.get("openalex_id", "") or ""),
            pdf_url,
            pdf_url,
            pdf_source,
            pdf_source,
            pdf_status,
            str(metadata.get("pdf_checked_at", "") or utc_now()),
            publisher_url,
            publisher_url,
            publisher_source,
            publisher_source,
            publisher_pdf_url,
            publisher_pdf_url,
            publisher_pdf_source,
            publisher_pdf_source,
            publisher_status,
            paper_id,
        ),
    )


def update_llm_summary(
    conn: sqlite3.Connection,
    paper_id: int,
    summary: Dict[str, Any],
    model: str,
) -> None:
    conn.execute(
        """
        UPDATE papers
        SET llm_summary_json = ?,
            llm_summary_model = ?,
            llm_summary_generated_at = ?,
            llm_summary_status = 'done',
            llm_summary_error = ''
        WHERE id = ?
        """,
        (_json_dumps(summary), model, utc_now(), paper_id),
    )


def update_llm_summary_error(conn: sqlite3.Connection, paper_id: int, error: str) -> None:
    conn.execute(
        """
        UPDATE papers
        SET llm_summary_status = 'error',
            llm_summary_error = ?
        WHERE id = ?
        """,
        (error[:1000], paper_id),
    )


def update_classification(
    conn: sqlite3.Connection,
    paper_id: int,
    task_labels: list[str],
    target_labels: list[str],
    relevance_score: float,
    recommendation_reason: str,
    hidden: bool | None = None,
) -> None:
    if hidden is None:
        conn.execute(
            """
            UPDATE papers
            SET task_labels_json = ?,
                target_labels_json = ?,
                relevance_score = ?,
                recommendation_reason = ?
            WHERE id = ?
            """,
            (
                _json_dumps(task_labels),
                _json_dumps(target_labels),
                relevance_score,
                recommendation_reason,
                paper_id,
            ),
        )
        return

    conn.execute(
        """
        UPDATE papers
        SET task_labels_json = ?,
            target_labels_json = ?,
            relevance_score = ?,
            recommendation_reason = ?,
            is_hidden = ?
        WHERE id = ?
        """,
        (
            _json_dumps(task_labels),
            _json_dumps(target_labels),
            relevance_score,
            recommendation_reason,
            1 if hidden else 0,
            paper_id,
        ),
    )


def set_paper_flags(conn: sqlite3.Connection, paper_id: int, flags: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    allowed = {"is_read", "is_saved"}
    updates = []
    params: list[Any] = []
    for key, value in flags.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(1 if bool(value) else 0)
    if updates:
        params.append(paper_id)
        conn.execute(f"UPDATE papers SET {', '.join(updates)} WHERE id = ?", params)
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return _paper_from_row(row) if row else None


def delete_paper(conn: sqlite3.Connection, paper_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        return None

    paper = _paper_from_row(row)
    conn.execute(
        """
        INSERT INTO deleted_papers (
            title, title_norm, abstract, authors_json, sources_json, source_id, url, pdf_url,
            publisher_url, publisher_source, publisher_pdf_url, publisher_pdf_source, venue, year,
            published_at, first_seen_at, source_categories_json, deleted_at, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(title_norm) DO UPDATE SET
            title = excluded.title,
            abstract = excluded.abstract,
            authors_json = excluded.authors_json,
            sources_json = excluded.sources_json,
            source_id = excluded.source_id,
            url = excluded.url,
            pdf_url = excluded.pdf_url,
            publisher_url = excluded.publisher_url,
            publisher_source = excluded.publisher_source,
            publisher_pdf_url = excluded.publisher_pdf_url,
            publisher_pdf_source = excluded.publisher_pdf_source,
            venue = excluded.venue,
            year = excluded.year,
            published_at = excluded.published_at,
            first_seen_at = excluded.first_seen_at,
            source_categories_json = excluded.source_categories_json,
            deleted_at = excluded.deleted_at,
            raw_json = excluded.raw_json
        """,
        (
            paper["title"],
            paper["title_norm"],
            paper.get("abstract", ""),
            _json_dumps(paper.get("authors", [])),
            _json_dumps(paper.get("sources", [])),
            paper.get("source_id", ""),
            paper.get("url", ""),
            paper.get("pdf_url", ""),
            paper.get("publisher_url", ""),
            paper.get("publisher_source", ""),
            paper.get("publisher_pdf_url", ""),
            paper.get("publisher_pdf_source", ""),
            paper.get("venue", ""),
            paper.get("year"),
            paper.get("published_at", ""),
            paper.get("first_seen_at", ""),
            _json_dumps(paper.get("source_categories", [])),
            utc_now(),
            _json_dumps(paper.get("raw", {})),
        ),
    )
    conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
    deleted = conn.execute("SELECT * FROM deleted_papers WHERE title_norm = ?", (paper["title_norm"],)).fetchone()
    return _deleted_paper_from_row(deleted) if deleted else None


def get_state(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM source_states WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO source_states(key, value, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, utc_now()),
    )


def start_run_log(conn: sqlite3.Connection, source: str, interest_id: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO run_logs(source, interest_id, started_at, status)
        VALUES(?, ?, ?, 'running')
        """,
        (source, interest_id, utc_now()),
    )
    return int(cursor.lastrowid)


def finish_run_log(
    conn: sqlite3.Connection,
    log_id: int,
    status: str,
    fetched_count: int,
    kept_count: int,
    new_count: int,
    error: str = "",
) -> None:
    conn.execute(
        """
        UPDATE run_logs
        SET finished_at = ?, status = ?, fetched_count = ?, kept_count = ?, new_count = ?, error = ?
        WHERE id = ?
        """,
        (utc_now(), status, fetched_count, kept_count, new_count, error, log_id),
    )


def list_run_logs(conn: sqlite3.Connection, limit: int = 20) -> list[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM run_logs ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 200)),),
    ).fetchall()
    return [dict(row) for row in rows]


def get_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = conn.execute("SELECT * FROM papers").fetchall()
    papers = [_paper_from_row(row) for row in rows]
    stats: Dict[str, Any] = {
        "total": len(papers),
        "unread": sum(1 for paper in papers if not paper["is_read"]),
        "saved": sum(1 for paper in papers if paper["is_saved"]),
        "saved_with_summary": sum(1 for paper in papers if paper["is_saved"] and paper.get("llm_summary")),
        "saved_missing_summary": sum(1 for paper in papers if paper["is_saved"] and not paper.get("llm_summary")),
        "with_pdf": sum(1 for paper in papers if paper.get("pdf_url")),
        "missing_pdf": sum(1 for paper in papers if not paper.get("pdf_url")),
        "publisher_links": sum(1 for paper in papers if paper.get("publisher_url") and not paper.get("pdf_url")),
        "publisher_pdf_links": sum(1 for paper in papers if paper.get("publisher_pdf_url") and not paper.get("pdf_url")),
        "deleted": conn.execute("SELECT COUNT(*) AS count FROM deleted_papers").fetchone()["count"],
        "sources": {},
        "task_labels": {},
        "target_labels": {},
    }
    for paper in papers:
        for source in paper["sources"]:
            stats["sources"][source] = stats["sources"].get(source, 0) + 1
        for label in paper["task_labels"]:
            stats["task_labels"][label] = stats["task_labels"].get(label, 0) + 1
        for label in paper["target_labels"]:
            stats["target_labels"][label] = stats["target_labels"].get(label, 0) + 1
    return stats
