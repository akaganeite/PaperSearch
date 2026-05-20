from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any


USER_AGENT = "PaperSearch/0.1 (+local research assistant)"


class SourceError(RuntimeError):
    pass


def http_get_text(url: str, timeout: int = 25, retries: int = 2) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
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


def http_get_json(url: str, timeout: int = 25) -> Any:
    return json.loads(http_get_text(url, timeout=timeout))
