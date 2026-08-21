from __future__ import annotations

import json
import re
from typing import Any, Dict

from . import db
from .config import load_config
from .llm_client import chat_json


DEFAULT_SYSTEM_PROMPT = """You are a paper-summary assistant.
You only summarize papers the user has already saved; do not select or rank papers.
Be concrete, concise, and faithful to the provided metadata. If only sparse metadata is available, say so and do not invent method details.
Return JSON only, with no Markdown."""


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _truncate(value: str, limit: int) -> str:
    value = _clean(value)
    return value[:limit].strip()


def _source_context(paper: Dict[str, Any], limit: int = 1200) -> str:
    raw = paper.get("raw") if isinstance(paper.get("raw"), dict) else {}
    contexts: list[str] = []
    if isinstance(raw.get("context"), str):
        contexts.append(raw["context"])
    for source_value in raw.values():
        if isinstance(source_value, dict) and isinstance(source_value.get("context"), str):
            contexts.append(source_value["context"])
    joined = " ".join(contexts)
    return _truncate(joined, limit)


def _summary_quality(paper: Dict[str, Any]) -> str:
    return "abstract_based" if len(_clean(paper.get("abstract", ""))) >= 80 else "metadata_only"


def _system_prompt(config: Dict[str, Any]) -> str:
    cfg = config.get("llm_summary", {})
    if cfg.get("system_prompt"):
        return str(cfg["system_prompt"])
    profile = config.get("summary_profile", {})
    if not isinstance(profile, dict):
        return DEFAULT_SYSTEM_PROMPT
    pieces = [
        _clean(profile.get("role", "")) or DEFAULT_SYSTEM_PROMPT,
        "You only summarize papers the user has already saved; do not select or rank papers.",
        _clean(profile.get("style", "")),
        _clean(profile.get("limited_metadata_instruction", "")),
    ]
    focus = _clean(profile.get("focus", ""))
    if focus:
        pieces.append(f"Focus on: {focus}")
    output_language = _clean(profile.get("output_language", ""))
    if output_language:
        pieces.append(f"Write the summary in {output_language}.")
    pieces.append("Return JSON only, with no Markdown.")
    return "\n".join(piece for piece in pieces if piece)


def _build_user_prompt(paper: Dict[str, Any], config: Dict[str, Any]) -> str:
    profile = config.get("summary_profile", {}) if isinstance(config.get("summary_profile"), dict) else {}
    output_language = _clean(profile.get("output_language", "中文"))
    focus = _clean(profile.get("focus", ""))
    payload = {
        "title": paper.get("title", ""),
        "abstract": _truncate(str(paper.get("abstract", "") or ""), 2200),
        "venue": paper.get("venue", ""),
        "year": paper.get("year"),
        "authors": paper.get("authors", []),
        "sources": paper.get("sources", []),
        "task_labels": paper.get("task_labels", []),
        "target_labels": paper.get("target_labels", []),
        "recommendation_reason": paper.get("recommendation_reason", ""),
        "source_context": _source_context(paper),
        "summary_quality_hint": _summary_quality(paper),
    }
    return (
        f"请为下面这篇 Saved paper 生成结构化摘要，输出语言：{output_language}。"
        + (f"重点关注：{focus}。" if focus else "")
        +
        "输出 JSON 字段必须包含：summary_zh, why_relevant, key_points, task, target, limitations, "
        "read_priority, summary_quality, confidence。"
        "read_priority 只能是 high/medium/low；summary_quality 只能是 abstract_based/metadata_only。"
        "示例结构：{\"summary_zh\":\"...\",\"why_relevant\":\"...\",\"key_points\":[\"...\"],"
        "\"task\":\"...\",\"target\":\"...\",\"limitations\":[\"...\"],\"read_priority\":\"medium\","
        "\"summary_quality\":\"abstract_based\",\"confidence\":0.8}。"
        "summary_zh 写 4-6 句；key_points 和 limitations 都是字符串数组。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _normalize_summary(payload: Dict[str, Any], fallback_quality: str) -> Dict[str, Any]:
    priority = str(payload.get("read_priority", "medium") or "medium").lower()
    if priority not in {"high", "medium", "low"}:
        priority = "medium"
    quality = str(payload.get("summary_quality", fallback_quality) or fallback_quality)
    if quality not in {"abstract_based", "metadata_only"}:
        quality = fallback_quality
    try:
        confidence = float(payload.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    key_points = payload.get("key_points", [])
    if not isinstance(key_points, list):
        key_points = []
    limitations = payload.get("limitations", [])
    if not isinstance(limitations, list):
        limitations = []

    return {
        "summary_zh": _clean(payload.get("summary_zh", "")),
        "why_relevant": _clean(payload.get("why_relevant", "")),
        "key_points": [_clean(item) for item in key_points if _clean(item)][:6],
        "task": _clean(payload.get("task", "")),
        "target": _clean(payload.get("target", "")),
        "limitations": [_clean(item) for item in limitations if _clean(item)][:5],
        "read_priority": priority,
        "summary_quality": quality,
        "confidence": round(confidence, 3),
    }


def _deepseek_chat_completion(paper: Dict[str, Any], config: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    parsed, model = chat_json(
        config,
        "llm_summary",
        _system_prompt(config),
        _build_user_prompt(paper, config),
    )
    summary = _normalize_summary(parsed, _summary_quality(paper))
    if not summary["summary_zh"]:
        raise RuntimeError("DeepSeek returned empty summary.")
    return summary, model


def summarize_saved_papers(config_path: str | None = None, limit: int | None = None) -> Dict[str, Any]:
    config = load_config(config_path)
    cfg = config.get("llm_summary", {})
    if not cfg.get("enabled", True):
        return {"checked": 0, "summarized": 0, "skipped": 0, "errors": 0, "disabled": True}

    resolved_limit = int(limit or cfg.get("max_per_update", 10) or 10)
    db_path = config["storage"]["database_path_resolved"]
    db.init_db(db_path)
    summary = {"checked": 0, "summarized": 0, "skipped": 0, "errors": 0, "error_messages": []}

    with db.connect(db_path) as conn:
        papers = db.list_saved_papers_for_summary(conn, limit=resolved_limit)
        if not papers:
            return summary
        for paper in papers:
            summary["checked"] += 1
            if paper.get("llm_summary"):
                summary["skipped"] += 1
                continue
            try:
                llm_summary, model = _deepseek_chat_completion(paper, config)
                db.update_llm_summary(conn, int(paper["id"]), llm_summary, model)
                conn.commit()
                summary["summarized"] += 1
            except Exception as exc:  # noqa: BLE001 - one bad/API-failed paper must not block the batch.
                db.update_llm_summary_error(conn, int(paper["id"]), str(exc))
                conn.commit()
                summary["errors"] += 1
                if len(summary["error_messages"]) < 5:
                    summary["error_messages"].append({"paper_id": paper["id"], "error": str(exc)})
    return summary
