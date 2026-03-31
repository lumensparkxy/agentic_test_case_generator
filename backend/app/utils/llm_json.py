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


def parse_coverage_plan_json(text: str) -> List[Dict[str, Any]]:
    """Parse requirement coverage plan payload from model output."""
    json_text = extract_json(text)
    if not json_text:
        return []

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    if isinstance(data, list):
        plan = data
    elif isinstance(data, dict) and "coverage_plan" in data and isinstance(data["coverage_plan"], list):
        plan = data["coverage_plan"]
    else:
        return []

    valid: List[Dict[str, Any]] = []
    for item in plan:
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("requirement_id") or "").strip()
        requirement_text = str(item.get("requirement_text") or item.get("text") or "").strip()
        scenarios = item.get("scenarios")
        if not requirement_id or not isinstance(scenarios, list):
            continue
        valid.append(
            {
                "requirement_id": requirement_id,
                "requirement_text": requirement_text,
                "scenarios": [scenario for scenario in scenarios if isinstance(scenario, dict)],
            }
        )

    return valid


def parse_review_json(text: str, default_threshold: int = 0) -> Optional[Dict[str, Any]]:
    """Parse a structured reviewer/validator result from model output."""
    if not text:
        return None

    normalized = text.strip()
    if normalized.upper() == "APPROVED":
        return {
            "approved": True,
            "score": 100,
            "threshold": default_threshold,
            "summary": "Approved by reviewer.",
            "blocking_issues": [],
            "suggestions": [],
            "unmet_criteria": [],
        }

    json_text = extract_json(normalized)
    if not json_text:
        return None

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    blocking_issues = data.get("blocking_issues") or []
    suggestions = data.get("suggestions") or []
    unmet_criteria = data.get("unmet_criteria") or []

    if isinstance(blocking_issues, str):
        blocking_issues = [blocking_issues]
    if isinstance(suggestions, str):
        suggestions = [suggestions]
    if isinstance(unmet_criteria, str):
        unmet_criteria = [unmet_criteria]

    raw_score = data.get("score", 0)
    raw_threshold = data.get("threshold", default_threshold)

    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        score = 0

    try:
        threshold = int(raw_threshold)
    except (TypeError, ValueError):
        threshold = default_threshold

    approved = bool(data.get("approved", False))
    summary = str(data.get("summary", "")).strip()

    return {
        "approved": approved,
        "score": max(0, min(100, score)),
        "threshold": max(0, threshold),
        "summary": summary,
        "blocking_issues": [str(item).strip() for item in blocking_issues if str(item).strip()],
        "suggestions": [str(item).strip() for item in suggestions if str(item).strip()],
        "unmet_criteria": [str(item).strip() for item in unmet_criteria if str(item).strip()],
    }
