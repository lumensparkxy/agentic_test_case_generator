"""Bound context by relevance without stripping behavior-bearing fields."""

import json
import re

from .evaluation_limits import current_evaluation_limits

MAX_CONTEXT_CHARS = 256_000


def prepare_context_evidence(requirements, context, *, budget=None):
    if context is None:
        return "No additional context provided."
    if budget is None:
        budget = current_evaluation_limits.get().context_chars or MAX_CONTEXT_CHARS
    data = context.model_dump(mode="json", exclude_none=True)
    data.pop("requirements", None)
    for key in ("project_id", "base_project_revision"):
        data.pop(key, None)
    full = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if len(full) <= budget:
        return full
    terms = set(re.findall(r"[a-z0-9]+", " ".join(r.text for r in requirements).lower()))
    candidates = []
    grounded = data.pop("grounded_context", {})
    for category, items in grounded.items():
        if isinstance(items, list):
            for item in items:
                text = json.dumps(item, ensure_ascii=False, sort_keys=True)
                relevance = len(terms & set(re.findall(r"[a-z0-9]+", text.lower())))
                candidates.append((-relevance, category, str(item.get("id", "")), item))
        else:
            data[f"grounded_{category}"] = items
    # Budget entire values, never truncate a field into a misleading fact.
    selected, omitted = {}, []
    used = 0
    for key, value in data.items():
        size = len(json.dumps({key: value}, ensure_ascii=False))
        if used + size <= budget // 2:
            selected[key] = value
            used += size
        else:
            omitted.append(key)
    selected["grounded_context"] = {}
    for _, category, identity, item in sorted(candidates, key=lambda row: row[:3]):
        size = len(json.dumps(item, ensure_ascii=False)) + len(category) + 8
        if used + size <= budget - 2000:
            selected["grounded_context"].setdefault(category, []).append(item)
            used += size
        else:
            omitted.append(f"{category}/{identity}")
    listed_ids: list[str] = []
    selected["omissions"] = {"count": len(omitted), "ids": listed_ids, "listed_count": 0}
    selected["evidence_limit"] = (
        "Context was budgeted by relevance. Omitted evidence is unavailable: report affected prerequisites as unknown/blocked; do not invent facts."
    )
    # An omitted item's ID may itself be large. Keep its count even if the ID cannot fit.
    remaining = budget - len(json.dumps(selected, ensure_ascii=False, sort_keys=True))
    for identity in omitted:
        if len(listed_ids) == 40:
            break
        count = len(listed_ids)
        size = len(json.dumps(identity, ensure_ascii=False)) + (2 if count else 0) + len(str(count + 1)) - len(str(count))
        if size <= remaining:
            listed_ids.append(identity)
            remaining -= size
    selected["omissions"]["listed_count"] = len(listed_ids)
    return json.dumps(selected, ensure_ascii=False, sort_keys=True)
