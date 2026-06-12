from typing import Any, Iterable


def _iter_response_parts(response: Any) -> Iterable[Any]:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            yield part


def extract_response_text(response: Any) -> str:
    """Safely concatenate text parts from a google-genai response without using `response.text`."""
    if response is None:
        return ""

    text_parts: list[str] = []
    for part in _iter_response_parts(response):
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            text_parts.append(text)

    if text_parts:
        return "\n".join(text_parts).strip()

    return ""
