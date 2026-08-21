from __future__ import annotations

import json
import os
import platform
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any


USER_AGENT = "PaperSearch/0.1 (+local research assistant)"


class SourceError(RuntimeError):
    pass


_NETWORK_CONFIG: dict[str, Any] = {}
_OPENER: urllib.request.OpenerDirector | None = None
_OPENER_KEY: tuple[str, ...] | None = None
_PROXY_URL_CACHE = ""
_PROXY_URL_CACHE_AT = 0.0


def configure_network(config: dict[str, Any] | None) -> None:
    global _NETWORK_CONFIG, _OPENER, _OPENER_KEY, _PROXY_URL_CACHE, _PROXY_URL_CACHE_AT
    _NETWORK_CONFIG = dict(config or {})
    _OPENER = None
    _OPENER_KEY = None
    _PROXY_URL_CACHE = ""
    _PROXY_URL_CACHE_AT = 0.0


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _macos_system_proxy_url() -> str:
    if platform.system() != "Darwin":
        return ""
    try:
        result = subprocess.run(
            ["scutil", "--proxy"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    for prefix in ("HTTP", "HTTPS"):
        if values.get(f"{prefix}Enable") != "1":
            continue
        host = values.get(f"{prefix}Proxy", "")
        port = values.get(f"{prefix}Port", "")
        if host and port:
            return f"http://{host}:{port}"
    return ""


def current_proxy_url() -> str:
    global _PROXY_URL_CACHE, _PROXY_URL_CACHE_AT
    configured = str(_NETWORK_CONFIG.get("proxy_url") or os.environ.get("PAPERSEARCH_PROXY_URL") or "").strip()
    if configured:
        return configured
    if _truthy(_NETWORK_CONFIG.get("use_macos_system_proxy"), default=True):
        cache_seconds = int(_NETWORK_CONFIG.get("system_proxy_cache_seconds", 60) or 0)
        now = time.monotonic()
        if cache_seconds > 0 and _PROXY_URL_CACHE_AT and now - _PROXY_URL_CACHE_AT < cache_seconds:
            return _PROXY_URL_CACHE
        _PROXY_URL_CACHE = _macos_system_proxy_url()
        _PROXY_URL_CACHE_AT = now
        return _PROXY_URL_CACHE
    return ""


def _opener(allow_insecure_tls: bool = False) -> urllib.request.OpenerDirector:
    global _OPENER, _OPENER_KEY
    proxy = current_proxy_url()
    key = (proxy, "insecure" if allow_insecure_tls else "secure")
    if _OPENER is not None and _OPENER_KEY == key:
        return _OPENER
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    if allow_insecure_tls:
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    _OPENER = urllib.request.build_opener(*handlers)
    _OPENER_KEY = key
    return _OPENER


def http_get_text(url: str, timeout: int = 25, retries: int = 2) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _opener().open(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                raise SourceError(f"HTTP Error {exc.code}: {exc.reason}") from exc
            retry_after = exc.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 3 * (attempt + 1)
            time.sleep(delay)
        except (urllib.error.URLError, socket.timeout) as exc:
            last_error = exc
            if attempt >= retries:
                raise SourceError(str(exc)) from exc
            time.sleep(2 * (attempt + 1))
    raise SourceError(str(last_error) if last_error else "Unknown source error")


def http_get_bytes(
    url: str,
    timeout: int = 25,
    retries: int = 2,
    headers: dict[str, str] | None = None,
    allow_insecure_tls: bool = False,
) -> tuple[bytes, str]:
    request_headers = {"User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _opener(allow_insecure_tls=allow_insecure_tls).open(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type") or ""
                return response.read(), content_type
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                raise SourceError(f"HTTP Error {exc.code}: {exc.reason}") from exc
            retry_after = exc.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 3 * (attempt + 1)
            time.sleep(delay)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt >= retries:
                raise SourceError(str(exc)) from exc
            time.sleep(2 * (attempt + 1))
    raise SourceError(str(last_error) if last_error else "Unknown source error")


def http_get_json(url: str, timeout: int = 25) -> Any:
    text = http_get_text(url, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = re.sub(r"\s+", " ", text[:160]).strip()
        raise SourceError(f"Invalid JSON response: {snippet}") from exc
