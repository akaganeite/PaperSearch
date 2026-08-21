from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict

from .sources.common import USER_AGENT


_CONNECTION_KEYS = {
    "provider",
    "model",
    "base_url",
    "api_key",
    "api_key_env",
    "thinking",
    "temperature",
    "max_tokens",
    "timeout_seconds",
}


def section_config(config: Dict[str, Any], section: str) -> Dict[str, Any]:
    base = config.get("llm_summary", {}) if section != "llm_summary" else {}
    resolved = {key: value for key, value in base.items() if key in _CONNECTION_KEYS}
    resolved.update(config.get(section, {}))
    return resolved


def _looks_like_direct_key(value: str) -> bool:
    return value.startswith("sk" + "-") or (len(value) >= 32 and not re.fullmatch(r"[A-Z0-9_]+", value))


def resolve_api_key(cfg: Dict[str, Any]) -> str:
    direct_key = str(cfg.get("api_key", "") or "").strip()
    if direct_key:
        return direct_key
    env_name = str(cfg.get("api_key_env", "DEEPSEEK_API_KEY") or "DEEPSEEK_API_KEY").strip()
    env_value = os.environ.get(env_name, "")
    if env_value:
        return env_value
    if _looks_like_direct_key(env_name):
        return env_name
    return ""


def extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    parsed = json.loads(match.group(0))
    return parsed if isinstance(parsed, dict) else {}


def chat_json(
    config: Dict[str, Any],
    section: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[Dict[str, Any], str]:
    cfg = section_config(config, section)
    api_key = resolve_api_key(cfg)
    if not api_key:
        raise RuntimeError(f"Missing LLM API key for {section}; set the configured api_key_env.")

    provider = str(cfg.get("provider", "deepseek") or "deepseek").lower()
    if provider not in {"deepseek", "openai-compatible", "openai_compatible"}:
        raise RuntimeError(f"Unsupported LLM provider for {section}: {provider}")
    model = str(cfg.get("model", "deepseek-chat") or "deepseek-chat")
    base_url = str(cfg.get("base_url", "https://api.deepseek.com") or "https://api.deepseek.com").rstrip("/")
    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": float(cfg.get("temperature", 0.0) or 0.0),
        "max_tokens": int(cfg.get("max_tokens", 900) or 900),
        "response_format": {"type": "json_object"},
    }
    if provider == "deepseek":
        body["thinking"] = {"type": "enabled" if cfg.get("thinking", False) else "disabled"}

    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    timeout = int(cfg.get("timeout_seconds", 60) or 60)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc

    content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    parsed = extract_json(content)
    if not parsed:
        raise RuntimeError("LLM returned invalid or empty JSON.")
    return parsed, model
