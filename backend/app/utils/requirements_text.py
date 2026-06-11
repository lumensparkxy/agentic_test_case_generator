import re
from typing import Any, Dict, List


ACTION_VERBS = (
    "add",
    "allow",
    "authenticate",
    "authorize",
    "create",
    "delete",
    "display",
    "download",
    "enable",
    "export",
    "generate",
    "import",
    "load",
    "lock",
    "parse",
    "prevent",
    "process",
    "provide",
    "require",
    "reset",
    "save",
    "send",
    "show",
    "sort",
    "support",
    "update",
    "upload",
    "validate",
    "verify",
    "view",
)

USER_SUBJECTS = (
    "admin",
    "administrator",
    "administrators",
    "customer",
    "customers",
    "employee",
    "employees",
    "finance administrator",
    "finance administrators",
    "guest",
    "guests",
    "manager",
    "managers",
    "user",
    "users",
)


def clean_requirement_text(text: Any) -> str:
    value = str(text or "")
    if not value:
        return ""

    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"\*([^*]+)\*", r"\1", value)
    value = re.sub(r"__([^_]+)__", r"\1", value)
    value = re.sub(r"_([^_]+)_", r"\1", value)
    value = re.sub(r"^[-*•│├└]\s*", "", value)
    value = re.sub(r"^\d+\.\s*", "", value)
    value = value.replace(" (stub)", "").replace("(stub)", "")
    value = " ".join(value.strip().strip(":").split())
    return value


def normalize_requirement_text(text: Any) -> str:
    value = clean_requirement_text(text)
    if not value:
        return ""

    match = re.match(r"^(?:the\s+)?system\s+shall\b\s*(.*)$", value, re.IGNORECASE)
    if match:
        rest = match.group(1).strip()
        return f"The system shall {rest}".strip()

    match = re.match(r"^(?:the\s+)?system\s+(?:should|must|will|can)\b\s*(.*)$", value, re.IGNORECASE)
    if match:
        rest = match.group(1).strip()
        if rest:
            return f"The system shall {rest}"

    match = re.match(
        r"^(?:the\s+)?(?:application|app|platform|portal|service)\s+(?:shall|should|must|will|can)\b\s*(.*)$",
        value,
        re.IGNORECASE,
    )
    if match:
        rest = match.group(1).strip()
        if rest:
            return f"The system shall {rest}"

    subject_pattern = "|".join(re.escape(subject) for subject in sorted(USER_SUBJECTS, key=len, reverse=True))
    match = re.match(
        rf"^(?:the\s+)?(?P<subject>{subject_pattern})\s+(?:can|should|must|will|shall)\b\s*(?P<body>.+)$",
        value,
        re.IGNORECASE,
    )
    if match:
        subject = match.group("subject").lower()
        body = re.sub(r"^be\s+able\s+to\s+", "", match.group("body").strip(), flags=re.IGNORECASE)
        if body:
            return f"The system shall allow {subject} to {body}"

    action_pattern = "|".join(re.escape(verb) for verb in ACTION_VERBS)
    match = re.match(rf"^(?P<verb>{action_pattern})\b\s*(?P<body>.+)$", value, re.IGNORECASE)
    if match:
        verb = match.group("verb").lower()
        body = match.group("body").strip()
        if body:
            return f"The system shall {verb} {body}"

    return value


def normalize_requirement_payloads(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    seen: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        text = normalize_requirement_text(item.get("text", ""))
        if not text:
            continue

        dedupe_key = text.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        requirement_id = str(item.get("id") or "").strip()
        if not requirement_id.startswith("REQ-"):
            requirement_id = f"REQ-{len(normalized) + 1:03d}"

        normalized.append({"id": requirement_id, "text": text})

    for index, item in enumerate(normalized, start=1):
        item["id"] = f"REQ-{index:03d}"

    return normalized
