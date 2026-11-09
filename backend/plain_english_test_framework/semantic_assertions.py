"""Helpers for guarding ambiguous browser assertion text."""

from __future__ import annotations

import re
from typing import Any


QUOTED_BROWSER_LOCATOR = r"""(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*')"""
MAX_BROWSER_COUNT = (1 << 53) - 1


def unquote_browser_locator(quoted: str) -> str:
    r"""Decode the outer quotes while preserving CSS escapes such as \#."""
    delimiter = re.escape(quoted[0])
    return re.sub(rf"\\([{delimiter}\\])", r"\1", quoted[1:-1])


def parse_browser_count(text: str) -> int | None:
    """Read an exact JavaScript integer without converting unbounded input."""
    if not text.isdecimal():
        return None
    digits = text.lstrip("0") or "0"
    if len(digits) > len(str(MAX_BROWSER_COUNT)):
        return None
    count = int(digits)
    return count if count <= MAX_BROWSER_COUNT else None


AMBIGUOUS_SEMANTIC_VISIBLE_TEXTS = frozenset(
    {
        "button",
        "buttons",
        "field",
        "fields",
        "form",
        "forms",
        "header",
        "headers",
        "heading",
        "headings",
        "href",
        "input",
        "inputs",
        "label",
        "labels",
        "link",
        "links",
        "locator",
        "locators",
        "page",
        "pages",
        "screen",
        "screens",
        "section",
        "sections",
        "selector",
        "selectors",
        "url",
    }
)


def is_ambiguous_semantic_visible_text(value: Any) -> bool:
    """Return true when a visible-text assertion names a UI concept, not page copy."""

    normalized = re.sub(r"\s+", " ", str(value or "")).strip().strip(" .").lower()
    return normalized in AMBIGUOUS_SEMANTIC_VISIBLE_TEXTS
