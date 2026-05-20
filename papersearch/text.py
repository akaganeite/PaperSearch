from __future__ import annotations

import re
import unicodedata
from typing import Iterable


_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def compact_spaces(value: str) -> str:
    return _SPACE_RE.sub(" ", value or "").strip()


def normalize_title(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    normalized = _PUNCT_RE.sub(" ", ascii_title.lower())
    return compact_spaces(normalized)


def normalize_for_match(value: str) -> str:
    return compact_spaces((value or "").lower())


def contains_term(text: str, term: str) -> bool:
    haystack = normalize_for_match(text)
    needle = normalize_for_match(term)
    if not needle:
        return False

    if len(needle) <= 3 and needle.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None
    return needle in haystack


def matched_terms(text: str, terms: Iterable[str]) -> list[str]:
    return [term for term in terms if contains_term(text, term)]


def first_non_empty(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if not isinstance(value, str):
            return str(value)
    return ""

