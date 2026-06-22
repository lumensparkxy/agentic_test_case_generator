"""Helpers for guarding ambiguous browser assertion text."""

from __future__ import annotations

import re
from typing import Any


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
