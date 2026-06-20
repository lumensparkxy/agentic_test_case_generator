import json
import re
from typing import Any, Dict, List, Optional


def _clean_string_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    cleaned: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _clean_object_list(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _is_blank_output(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _valid_test_case_shape(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return bool(str(item.get("id") or "").strip()) and bool(str(item.get("title") or "").strip()) and "steps" in item


def _coerce_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)

    normalized = str(value).strip()
    if not normalized:
        return default

    try:
        return int(float(normalized))
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", normalized)
        if not match:
            return default
        try:
            return int(float(match.group(0)))
        except (TypeError, ValueError):
            return default


def extract_json(text: Any) -> Optional[str]:
    """Extract JSON from text that may contain markdown fences."""
    if not text:
        return None

    if hasattr(text, "model_dump"):
        text = text.model_dump()

    if isinstance(text, (dict, list)):
        return json.dumps(text, default=str)

    normalized = str(text).strip()
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


def _recover_complete_array_items(json_text: str, *, key: str) -> List[Any]:
    """Return complete items from a JSON array prefix after a decode failure."""
    normalized = str(json_text or "")
    stripped = normalized.lstrip()
    offset = len(normalized) - len(stripped)

    if stripped.startswith("["):
        array_start = offset
    else:
        key_match = re.search(rf'"{re.escape(key)}"\s*:', normalized)
        if not key_match:
            return []
        array_start = normalized.find("[", key_match.end())
        if array_start == -1:
            return []

    decoder = json.JSONDecoder()
    items: List[Any] = []
    index = array_start + 1
    length = len(normalized)

    while index < length:
        while index < length and normalized[index] in " \r\n\t,":
            index += 1

        if index >= length or normalized[index] == "]":
            break

        try:
            item, index = decoder.raw_decode(normalized, index)
        except json.JSONDecodeError:
            break
        items.append(item)

    return items


def parse_requirements_json_detailed(text: Any) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse requirements payload from model output and return an error when invalid."""
    if _is_blank_output(text):
        return [], "empty output"

    json_text = extract_json(text)
    if not json_text:
        return [], "no JSON payload found"

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return [], f"invalid JSON payload: {exc.msg}"

    if isinstance(data, dict) and "requirements" in data:
        data = data["requirements"]

    if isinstance(data, list):
        valid: List[Dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict) and "id" in item and "text" in item:
                payload = dict(item)
                payload["id"] = str(item["id"])
                payload["text"] = str(item["text"])
                valid.append(payload)

        if valid:
            return valid, None
        if not data:
            return [], "requirements list was empty"
        return [], "requirements payload did not contain valid id/text objects"

    return [], "requirements payload must be a JSON array or an object with a requirements array"


def parse_requirements_json(text: Any) -> List[Dict[str, Any]]:
    """Parse requirements payload from model output."""
    parsed, _ = parse_requirements_json_detailed(text)
    return parsed


def parse_test_cases_json_detailed(text: Any) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse test-case payload from model output and return an error when invalid."""
    if _is_blank_output(text):
        return [], "empty output"

    json_text = extract_json(text)
    if not json_text:
        return [], "no JSON payload found"

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return [], f"invalid JSON payload: {exc.msg}"

    if isinstance(data, list):
        valid = [item for item in data if _valid_test_case_shape(item)]
        if valid:
            return valid, None
        if data:
            return [], "test_cases payload did not contain valid id/title/steps objects"
        return [], "test_cases list was empty"

    if isinstance(data, dict) and "test_cases" in data and isinstance(data["test_cases"], list):
        valid = [item for item in data["test_cases"] if _valid_test_case_shape(item)]
        if valid:
            return valid, None
        if data["test_cases"]:
            return [], "test_cases payload did not contain valid id/title/steps objects"
        return [], "test_cases list was empty"

    return [], "test-case payload must be a JSON array or an object with a test_cases array"


def parse_test_cases_json(text: Any) -> List[Dict[str, Any]]:
    """Parse test-case payload from model output."""
    parsed, _ = parse_test_cases_json_detailed(text)
    return parsed


def _valid_coverage_plan_entries(data: Any) -> List[Dict[str, Any]]:
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


def parse_coverage_plan_json_detailed(text: Any) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse requirement coverage plan payload from model output and return an error when invalid."""
    if _is_blank_output(text):
        return [], "empty output"

    json_text = extract_json(text)
    if not json_text:
        return [], "no JSON payload found"

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        recovered_plan = _recover_complete_array_items(json_text, key="coverage_plan")
        recovered_valid = _valid_coverage_plan_entries(recovered_plan)
        if recovered_valid:
            return recovered_valid, f"invalid JSON payload: {exc.msg}; recovered {len(recovered_valid)} complete coverage_plan entries"
        return [], f"invalid JSON payload: {exc.msg}"

    if isinstance(data, list):
        plan = data
    elif isinstance(data, dict) and "coverage_plan" in data and isinstance(data["coverage_plan"], list):
        plan = data["coverage_plan"]
    else:
        return [], "coverage-plan payload must be a JSON array or an object with a coverage_plan array"

    valid = _valid_coverage_plan_entries(plan)
    if valid:
        return valid, None
    if not plan:
        return [], "coverage_plan list was empty"
    return [], "coverage_plan payload did not contain valid requirement_id/scenarios entries"


def parse_coverage_plan_json(text: Any) -> List[Dict[str, Any]]:
    """Parse requirement coverage plan payload from model output."""
    parsed, _ = parse_coverage_plan_json_detailed(text)
    return parsed


def parse_requirement_analysis_json_detailed(text: Any) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse requirement analysis payload from model output and return an error when invalid."""
    if _is_blank_output(text):
        return [], "empty output"

    json_text = extract_json(text)
    if not json_text:
        return [], "no JSON payload found"

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return [], f"invalid JSON payload: {exc.msg}"

    analysis_candidates: Any = data
    if isinstance(data, dict):
        for key in ("requirement_analysis", "requirement_analyses", "analysis", "analyses"):
            if isinstance(data.get(key), list):
                analysis_candidates = data[key]
                break

    if not isinstance(analysis_candidates, list):
        return [], "requirement-analysis payload must be a JSON array or an object with a requirement_analysis array"

    valid: List[Dict[str, Any]] = []
    for item in analysis_candidates:
        if not isinstance(item, dict):
            continue

        requirement_id = str(item.get("requirement_id") or "").strip()
        requirement_text = str(item.get("requirement_text") or item.get("text") or "").strip()
        if not requirement_id:
            continue

        valid.append(
            {
                "requirement_id": requirement_id,
                "requirement_text": requirement_text,
                "business_rules": _clean_object_list(item.get("business_rules")),
                "field_constraints": _clean_object_list(item.get("field_constraints")),
                "role_permissions": _clean_object_list(item.get("role_permissions")),
                "state_transitions": _clean_object_list(item.get("state_transitions")),
                "risk_signals": _clean_object_list(item.get("risk_signals")),
                "suggested_scenarios": _clean_string_list(item.get("suggested_scenarios")),
                "dependencies": _clean_string_list(item.get("dependencies")),
            }
        )

    if valid:
        return valid, None
    if not analysis_candidates:
        return [], "requirement_analysis list was empty"
    return [], "requirement-analysis payload did not contain valid requirement_id entries"


def parse_requirement_analysis_json(text: Any) -> List[Dict[str, Any]]:
    """Parse requirement analysis payload from model output."""
    parsed, _ = parse_requirement_analysis_json_detailed(text)
    return parsed


def parse_review_json_detailed(text: Any, default_threshold: int = 0) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse a structured reviewer/validator result from model output and return an error when invalid."""
    if _is_blank_output(text):
        return None, "empty output"

    normalized = str(text).strip() if isinstance(text, str) else ""
    if normalized.upper() == "APPROVED":
        return {
            "approved": True,
            "score": 100,
            "threshold": default_threshold,
            "summary": "Approved by reviewer.",
            "blocking_issues": [],
            "suggestions": [],
            "unmet_criteria": [],
        }, None

    json_text = extract_json(normalized or text)
    if not json_text:
        return None, "no JSON payload found"

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON payload: {exc.msg}"

    if not isinstance(data, dict):
        return None, "review payload must be a JSON object"

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

    score = _coerce_int(raw_score, default=0)
    threshold = _coerce_int(raw_threshold, default=default_threshold)

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
    }, None


def parse_review_json(text: Any, default_threshold: int = 0) -> Optional[Dict[str, Any]]:
    """Parse a structured reviewer/validator result from model output."""
    parsed, _ = parse_review_json_detailed(text, default_threshold=default_threshold)
    return parsed
