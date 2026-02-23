import json
from typing import Any, Dict, List, Optional


def extract_json(text: str) -> Optional[str]:
    """Extract JSON from text that may contain markdown fences."""
    if not text:
        return None

    normalized = text.strip()
    if normalized.startswith("```json"):
        normalized = normalized[7:]
    elif normalized.startswith("```"):
        normalized = normalized[3:]

    if normalized.endswith("```"):
        normalized = normalized[:-3]

    normalized = normalized.strip()
    if normalized.startswith("{") or normalized.startswith("["):
        return normalized

    start = min(
        [pos for pos in [normalized.find("{"), normalized.find("[")] if pos != -1],
        default=-1,
    )
    if start == -1:
        return None

    end = max(normalized.rfind("}"), normalized.rfind("]"))
    if end == -1:
        return None

    return normalized[start : end + 1]


def parse_requirements_json(text: str) -> List[Dict[str, str]]:
    """Parse requirements payload from model output."""
    json_text = extract_json(text)
    if not json_text:
        return []

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    if isinstance(data, list):
        valid: List[Dict[str, str]] = []
        for item in data:
            if isinstance(item, dict) and "id" in item and "text" in item:
                valid.append({"id": str(item["id"]), "text": str(item["text"])})
        return valid

    if isinstance(data, dict) and "requirements" in data:
        return parse_requirements_json(json.dumps(data["requirements"]))

    return []


def parse_test_cases_json(text: str) -> List[Dict[str, Any]]:
    """Parse test-case payload from model output."""
    json_text = extract_json(text)
    if not json_text:
        return []

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict) and "test_cases" in data and isinstance(data["test_cases"], list):
        return data["test_cases"]

    return []
