from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict

from .config import all_top_venues
from .text import contains_term, matched_terms


def _combined_text(paper: Dict[str, Any]) -> str:
    return " ".join(
        str(paper.get(key, "") or "")
        for key in ("title", "abstract", "venue")
    )


def _venue_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def is_top_venue(venue: str, interest: Dict[str, Any]) -> bool:
    if not venue or not interest.get("top_venue_exception", {}).get("enabled", False):
        return False
    key = _venue_key(venue)
    for top_venue in all_top_venues(interest):
        top_key = _venue_key(top_venue)
        if key == top_key or top_key in key or key in top_key:
            return True
    return False


def traditional_topic_matches(text: str, interest: Dict[str, Any]) -> list[str]:
    return matched_terms(text, interest.get("traditional_topic_terms", []))


def prefilter_candidate(paper: Dict[str, Any], interest: Dict[str, Any]) -> tuple[bool, Dict[str, Any], str]:
    text = _combined_text(paper)
    exclude_matches = matched_terms(text, interest.get("exclude_keywords", []))
    if exclude_matches:
        reason = _template(interest, "reject_exclude", "Excluded by: {terms}", terms=", ".join(exclude_matches[:5]))
        return False, {"exclude_matches": exclude_matches}, reason

    llm_matches = matched_terms(text, interest.get("llm_agent_terms", []))
    agent_tool_matches = matched_terms(text, interest.get("agent_tool_terms", []))
    topic_matches = matched_terms(text, interest.get("security_software_terms", []))
    priority_matches = matched_terms(text, interest.get("priority_topics", []))
    traditional_matches = traditional_topic_matches(text, interest)
    top_venue_match = is_top_venue(str(paper.get("venue", "") or ""), interest)

    broad_matches = _merge_terms(
        llm_matches,
        agent_tool_matches,
        topic_matches,
        priority_matches,
        traditional_matches,
    )
    if not broad_matches:
        return False, {}, _template(interest, "reject_no_match", "No broad interest hint matched")

    merged_topic_matches = _merge_terms(topic_matches, priority_matches)
    merged_llm_matches = _merge_terms(llm_matches, agent_tool_matches)
    has_llm_agent = bool(merged_llm_matches)
    has_traditional = bool(traditional_matches)
    task_labels = classify_task_labels(text, has_llm_agent, top_venue_match, has_traditional, interest)
    target_labels = classify_target_labels(text, interest)
    rule_score = score_paper(
        paper,
        text,
        merged_llm_matches,
        agent_tool_matches,
        merged_topic_matches,
        top_venue_match,
        task_labels,
        target_labels,
        interest,
    )
    agent_tool_priority = 10000.0 if agent_tool_matches and merged_topic_matches else 0.0
    priority = agent_tool_priority + rule_score * 100.0 + _recency_score(str(paper.get("published_at", "") or ""))
    return True, {
        "llm_matches": llm_matches,
        "agent_tool_matches": agent_tool_matches,
        "topic_matches": topic_matches,
        "priority_matches": priority_matches,
        "traditional_matches": traditional_matches,
        "top_venue_match": top_venue_match,
        "rule_score": rule_score,
        "priority": round(priority, 2),
    }, ""


def _merge_terms(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for term in group:
            if term not in merged:
                merged.append(term)
    return merged


def _rule_matches(
    rule: Dict[str, Any],
    text: str,
    has_llm_agent: bool,
    top_venue_match: bool,
    traditional_match: bool,
) -> bool:
    match_mode = str(rule.get("match", "") or "")
    if match_mode == "llm_agent":
        return has_llm_agent
    if match_mode == "top_venue_traditional":
        return top_venue_match and traditional_match
    if match_mode == "top_venue_traditional_without_llm":
        return top_venue_match and traditional_match and not has_llm_agent
    terms = rule.get("terms", [])
    return bool(matched_terms(text, terms)) if isinstance(terms, list) else False


def classify_task_labels(
    text: str,
    has_llm_agent: bool,
    top_venue_match: bool,
    traditional_match: bool,
    interest: Dict[str, Any],
) -> list[str]:
    labels: list[str] = []
    for rule in interest.get("task_label_rules", []):
        if not isinstance(rule, dict):
            continue
        label = str(rule.get("label", "") or "")
        if label and _rule_matches(rule, text, has_llm_agent, top_venue_match, traditional_match):
            labels.append(label)
    return labels or [str(interest.get("fallback_task_label", "Other") or "Other")]


def classify_target_labels(text: str, interest: Dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for rule in interest.get("target_label_rules", []):
        if not isinstance(rule, dict):
            continue
        label = str(rule.get("label", "") or "")
        terms = rule.get("terms", [])
        if label and isinstance(terms, list) and matched_terms(text, terms):
            labels.append(label)
    return labels or [str(interest.get("fallback_target_label", "Unknown/Unclear") or "Unknown/Unclear")]


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _recency_score(published_at: str) -> float:
    date = _parse_datetime(published_at)
    if not date:
        return 0.0
    age_days = max(0, (datetime.now(timezone.utc) - date).days)
    return max(0.0, 8.0 - math.log1p(age_days))


def score_paper(
    paper: Dict[str, Any],
    text: str,
    llm_matches: list[str],
    agent_tool_matches: list[str],
    topic_matches: list[str],
    top_venue_match: bool,
    task_labels: list[str],
    target_labels: list[str],
    interest: Dict[str, Any],
) -> float:
    fallback_task = str(interest.get("fallback_task_label", "Other") or "Other")
    fallback_target = str(interest.get("fallback_target_label", "Unknown/Unclear") or "Unknown/Unclear")
    score = 0.0
    if llm_matches and topic_matches:
        score += 36.0
    if top_venue_match and topic_matches:
        score += 28.0
    if agent_tool_matches and topic_matches:
        score += float(interest.get("agent_tool_score_boost", 18.0) or 0)
    score += min(18.0, len(topic_matches) * 3.0)
    score += min(10.0, len(llm_matches) * 2.0)
    score += max(0, len([label for label in task_labels if label != fallback_task]) - 1) * 2.0
    score += max(0, len([label for label in target_labels if label != fallback_target])) * 1.5
    score += _recency_score(str(paper.get("published_at", "") or ""))
    if contains_term(text, "survey") or contains_term(text, "position paper"):
        score -= 10.0
    return round(max(0.0, score), 2)


def _template(interest: Dict[str, Any], key: str, default: str, **values: str) -> str:
    template = str(interest.get("reason_templates", {}).get(key, default) or default)
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return default


def recommendation_reason(
    llm_matches: list[str],
    agent_tool_matches: list[str],
    topic_matches: list[str],
    top_venue_match: bool,
    venue: str,
    task_labels: list[str],
    target_labels: list[str],
    interest: Dict[str, Any],
) -> str:
    pieces: list[str] = []
    fallback_task = str(interest.get("fallback_task_label", "Other") or "Other")
    fallback_target = str(interest.get("fallback_target_label", "Unknown/Unclear") or "Unknown/Unclear")
    if agent_tool_matches and topic_matches:
        pieces.append(_template(interest, "agent_tool_topic", "Matched coding-agent tool: {tools}", tools=", ".join(agent_tool_matches[:3])))
    if llm_matches and topic_matches:
        pieces.append(_template(interest, "llm_topic", "Matched LLM/Agent application topic"))
    if top_venue_match:
        pieces.append(_template(interest, "top_venue", "Matched top venue exception: {venue}", venue=venue))
    if topic_matches:
        pieces.append(_template(interest, "topic", "Topics: {terms}", terms=", ".join(topic_matches[:3])))
    non_fallback_tasks = [label for label in task_labels if label != fallback_task]
    if non_fallback_tasks:
        pieces.append(_template(interest, "tasks", "Tasks: {labels}", labels=", ".join(non_fallback_tasks)[:80]))
    if target_labels and target_labels != [fallback_target]:
        pieces.append(_template(interest, "targets", "Targets: {labels}", labels=", ".join(target_labels[:2])))
    return "；".join(pieces) or _template(interest, "default", "Matched default interest rules")


def enrich_and_filter(paper: Dict[str, Any], interest: Dict[str, Any]) -> tuple[bool, Dict[str, Any], str]:
    text = _combined_text(paper)
    exclude_matches = matched_terms(text, interest.get("exclude_keywords", []))
    if exclude_matches:
        reason = _template(interest, "reject_exclude", "Excluded by: {terms}", terms=", ".join(exclude_matches[:5]))
        return False, paper, reason

    llm_matches = matched_terms(text, interest.get("llm_agent_terms", []))
    agent_tool_matches = matched_terms(text, interest.get("agent_tool_terms", []))
    topic_matches = matched_terms(text, interest.get("security_software_terms", []))
    priority_matches = matched_terms(text, interest.get("priority_topics", []))
    merged_topic_matches = _merge_terms(topic_matches, priority_matches)
    merged_llm_matches = _merge_terms(llm_matches, agent_tool_matches)

    venue = str(paper.get("venue", "") or "")
    top_venue_match = is_top_venue(venue, interest)
    has_llm_agent = bool(merged_llm_matches)
    has_topic = bool(merged_topic_matches)

    traditional_matches = traditional_topic_matches(text, interest)
    has_traditional_topic = bool(traditional_matches)

    keep = (has_llm_agent and has_topic) or (top_venue_match and has_traditional_topic)
    if not keep:
        if has_llm_agent and not has_topic:
            return False, paper, _template(interest, "reject_llm_no_topic", "LLM/Agent matched, but target topic did not")
        if top_venue_match and not has_traditional_topic:
            return False, paper, _template(interest, "reject_top_no_traditional", "Top venue matched, but strict traditional topics did not")
        return False, paper, _template(interest, "reject_no_match", "No interest rule matched")

    task_labels = classify_task_labels(text, has_llm_agent, top_venue_match, has_traditional_topic, interest)
    if agent_tool_matches and has_topic:
        agent_tool_label = str(interest.get("agent_tool_task_label", "Agent工具评估") or "")
        if agent_tool_label and agent_tool_label not in task_labels:
            task_labels.append(agent_tool_label)
    target_labels = classify_target_labels(text, interest)
    score = score_paper(
        paper,
        text,
        merged_llm_matches,
        agent_tool_matches,
        merged_topic_matches,
        top_venue_match,
        task_labels,
        target_labels,
        interest,
    )
    min_score = float(interest.get("min_relevance_score", 0) or 0)
    if score < min_score:
        return False, paper, _template(
            interest,
            "reject_low_score",
            "Relevance score below threshold: {score} < {threshold}",
            score=f"{score:.1f}",
            threshold=f"{min_score:.1f}",
        )
    reason = recommendation_reason(
        llm_matches,
        agent_tool_matches,
        merged_topic_matches,
        top_venue_match,
        venue,
        task_labels,
        target_labels,
        interest,
    )

    enriched = dict(paper)
    enriched["task_labels"] = task_labels
    enriched["target_labels"] = target_labels
    enriched["relevance_score"] = score
    enriched["recommendation_reason"] = reason
    return True, enriched, ""
