from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_EXAMPLE_CONFIG_PATH = ROOT_DIR / "configs" / "app.example.json"
LOCAL_CONFIG_PATH = ROOT_DIR / "configs" / "local.json"
LEGACY_CONFIG_PATH = ROOT_DIR / "configs" / "interests.json"
DEFAULT_CONFIG_PATH = LEGACY_CONFIG_PATH


def resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return payload


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _resolve_config_path(config_path: str | None = None) -> Path:
    if config_path:
        return resolve_path(config_path)
    env_path = os.environ.get("PAPERSEARCH_CONFIG")
    if env_path:
        return resolve_path(env_path)
    if LOCAL_CONFIG_PATH.exists():
        return LOCAL_CONFIG_PATH
    if LEGACY_CONFIG_PATH.exists():
        return LEGACY_CONFIG_PATH
    return APP_EXAMPLE_CONFIG_PATH


def _merge_interests(profile_interests: list[Any], config_interests: list[Any]) -> list[Dict[str, Any]]:
    profile_by_id = {
        str(item.get("id", "")): item
        for item in profile_interests
        if isinstance(item, dict) and item.get("id")
    }
    merged: list[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in config_interests:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        base = profile_by_id.get(item_id, {})
        merged.append(_deep_merge(base, item))
        if item_id:
            seen_ids.add(item_id)
    for item_id, item in profile_by_id.items():
        if item_id not in seen_ids:
            merged.append(copy.deepcopy(item))
    return merged


def _merge_profile(config: Dict[str, Any]) -> Dict[str, Any]:
    profile_path_value = config.get("profile_path")
    if not profile_path_value:
        return config
    profile_path = resolve_path(str(profile_path_value))
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile file not found: {profile_path}")
    profile = _load_json(profile_path)
    merged = _deep_merge(profile, config)
    profile_interests = profile.get("interests", [])
    config_interests = config.get("interests", [])
    if isinstance(profile_interests, list) and isinstance(config_interests, list) and config_interests:
        merged["interests"] = _merge_interests(profile_interests, config_interests)
    merged["profile_path_resolved"] = str(profile_path)
    return merged


def load_config(config_path: str | None = None) -> Dict[str, Any]:
    selected_path = _resolve_config_path(config_path)
    if not selected_path.exists():
        raise FileNotFoundError(f"Config file not found: {selected_path}")

    config = _load_json(APP_EXAMPLE_CONFIG_PATH) if APP_EXAMPLE_CONFIG_PATH.exists() else {}
    if selected_path != APP_EXAMPLE_CONFIG_PATH:
        config = _deep_merge(config, _load_json(selected_path))
    config = _merge_profile(config)

    db_path = resolve_path(config.get("storage", {}).get("database_path", "data/papersearch.sqlite"))
    config.setdefault("storage", {})["database_path_resolved"] = str(db_path)
    config["config_path_resolved"] = str(selected_path)
    return config


def all_top_venues(interest: Dict[str, Any]) -> list[str]:
    top = interest.get("top_venue_exception", {})
    venues: list[str] = []
    for key in ("security_venues", "software_engineering_venues", "systems_architecture_venues"):
        venues.extend(top.get(key, []))
    return venues


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_walk_strings(item))
        return strings
    return []


def config_check(config_path: str | None = None) -> Dict[str, Any]:
    config = load_config(config_path)
    errors: list[str] = []
    warnings: list[str] = []

    if not config.get("interests"):
        errors.append("No interests are configured.")
    for interest in config.get("interests", []):
        if not isinstance(interest, dict):
            errors.append("Every interest must be a JSON object.")
            continue
        if not interest.get("id"):
            errors.append("Every interest must have an id.")
        if not interest.get("task_label_rules"):
            warnings.append(f"Interest {interest.get('id', '<unknown>')} has no task_label_rules.")
        if not interest.get("target_label_rules"):
            warnings.append(f"Interest {interest.get('id', '<unknown>')} has no target_label_rules.")

    api_key_env = str(config.get("llm_summary", {}).get("api_key_env", "") or "")
    if api_key_env.startswith("sk" + "-") or (len(api_key_env) >= 32 and not re.fullmatch(r"[A-Z0-9_]+", api_key_env)):
        warnings.append("llm_summary.api_key_env looks like a direct secret; use an environment variable name instead.")
    if config.get("llm_summary", {}).get("api_key"):
        warnings.append("llm_summary.api_key is set; do not commit local configs containing direct keys.")

    prefilter = config.get("llm_prefilter", {})
    if prefilter.get("enabled", False):
        threshold = float(prefilter.get("threshold", 50) or 50)
        if not 0 <= threshold <= 100:
            errors.append("llm_prefilter.threshold must be between 0 and 100.")
        if int(prefilter.get("max_per_update", 100) or 0) <= 0:
            errors.append("llm_prefilter.max_per_update must be positive.")
        if int(prefilter.get("max_workers", 4) or 0) <= 0:
            errors.append("llm_prefilter.max_workers must be positive.")
        prefilter_key_env = str(prefilter.get("api_key_env", "") or "")
        if prefilter_key_env.startswith("sk" + "-") or (
            len(prefilter_key_env) >= 32 and not re.fullmatch(r"[A-Z0-9_]+", prefilter_key_env)
        ):
            warnings.append("llm_prefilter.api_key_env looks like a direct secret; use an environment variable name instead.")
        if prefilter.get("api_key"):
            warnings.append("llm_prefilter.api_key is set; do not commit local configs containing direct keys.")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "config_path": config.get("config_path_resolved", ""),
        "profile_path": config.get("profile_path_resolved", ""),
        "database_path": config.get("storage", {}).get("database_path_resolved", ""),
        "interests": [item.get("id", "") for item in config.get("interests", []) if isinstance(item, dict)],
    }


def _secret_like(value: str) -> bool:
    key_prefixes = ("sk" + "-", "sk" + "_")
    return value.startswith(key_prefixes)


def redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            key_lower = str(key).lower()
            if key_lower in {"api_key", "token", "secret", "password"}:
                redacted[key] = "[redacted]" if item else item
            elif key_lower == "api_key_env" and isinstance(item, str) and _secret_like(item):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_config(item)
        return redacted
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    if isinstance(value, str) and _secret_like(value):
        return "[redacted]"
    return value
