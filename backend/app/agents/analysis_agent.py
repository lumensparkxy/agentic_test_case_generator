import logging
import re
from typing import Any, Dict, List, Optional

from google.adk.agents import Agent

from .adk_runtime import json_generation_config
from .prompting import REAL_WORLD_QA_POLICY, TEST_DESIGN_PROMPT_GUARDRAILS, human_feedback_section
from ..models import Requirement, RequirementAnalysisOutput

ALLOWED_RULE_TYPES = {
    "Business",
    "Validation",
    "Authorization",
    "State Transition",
    "Integration",
    "Notification",
    "Data",
    "Constraint",
    "Other",
}
RULE_TYPE_ALIASES = {
    "business": "Business",
    "validation": "Validation",
    "authorization": "Authorization",
    "auth": "Authorization",
    "state": "State Transition",
    "state transition": "State Transition",
    "workflow": "State Transition",
    "integration": "Integration",
    "notification": "Notification",
    "data": "Data",
    "constraint": "Constraint",
}

ALLOWED_CONSTRAINT_TYPES = {
    "Required",
    "Format",
    "Length",
    "Range",
    "File Type",
    "File Size",
    "Allowed Values",
    "Uniqueness",
    "Dependency",
    "Other",
}
CONSTRAINT_TYPE_ALIASES = {
    "required": "Required",
    "format": "Format",
    "length": "Length",
    "range": "Range",
    "file type": "File Type",
    "file size": "File Size",
    "allowed values": "Allowed Values",
    "uniqueness": "Uniqueness",
    "dependency": "Dependency",
}

ALLOWED_EFFECTS = {"Allow", "Deny", "Conditional"}
EFFECT_ALIASES = {
    "allow": "Allow",
    "allowed": "Allow",
    "deny": "Deny",
    "denied": "Deny",
    "conditional": "Conditional",
}

ALLOWED_RISK_CATEGORIES = {
    "Security",
    "Data Integrity",
    "Availability",
    "Usability",
    "Compliance",
    "Workflow",
    "Validation",
    "Integration",
    "Other",
}
RISK_CATEGORY_ALIASES = {
    "security": "Security",
    "data integrity": "Data Integrity",
    "availability": "Availability",
    "usability": "Usability",
    "compliance": "Compliance",
    "workflow": "Workflow",
    "validation": "Validation",
    "integration": "Integration",
}

ALLOWED_SEVERITIES = {"Critical", "High", "Medium", "Low"}
SEVERITY_ALIASES = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}

KNOWN_STATES = [
    "Draft",
    "Submitted",
    "Approved",
    "Rejected",
    "Pending",
    "Active",
    "Locked",
    "Signed Out",
    "Signed In",
]

ROLE_PATTERNS = [
    "users",
    "user",
    "employees",
    "employee",
    "managers",
    "manager",
    "finance administrators",
    "finance administrator",
    "administrators",
    "administrator",
    "admins",
    "admin",
]


def _dedupe_strings(items: List[str]) -> List[str]:
    seen: set[str] = set()
    unique: List[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _normalize_rule_type(raw_type: Any) -> str:
    raw = str(raw_type or "Business").strip()
    if raw in ALLOWED_RULE_TYPES:
        return raw
    return RULE_TYPE_ALIASES.get(raw.lower(), "Business")


def _normalize_constraint_type(raw_type: Any) -> str:
    raw = str(raw_type or "Other").strip()
    if raw in ALLOWED_CONSTRAINT_TYPES:
        return raw
    return CONSTRAINT_TYPE_ALIASES.get(raw.lower(), "Other")


def _normalize_effect(raw_effect: Any) -> str:
    raw = str(raw_effect or "Allow").strip()
    if raw in ALLOWED_EFFECTS:
        return raw
    return EFFECT_ALIASES.get(raw.lower(), "Allow")


def _normalize_risk_category(raw_category: Any) -> str:
    raw = str(raw_category or "Other").strip()
    if raw in ALLOWED_RISK_CATEGORIES:
        return raw
    return RISK_CATEGORY_ALIASES.get(raw.lower(), "Other")


def _normalize_severity(raw_severity: Any) -> str:
    raw = str(raw_severity or "Medium").strip()
    if raw in ALLOWED_SEVERITIES:
        return raw
    return SEVERITY_ALIASES.get(raw.lower(), "Medium")


def _title_from_requirement(requirement: Requirement) -> str:
    text = requirement.text.strip().rstrip(".")
    if len(text) <= 72:
        return text
    return f"{text[:69].rstrip()}..."


def _infer_rule_type(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("allow only", "permission", "role", "admin", "manager", "employee")):
        return "Authorization"
    if any(keyword in lowered for keyword in ("status", "state", "draft", "submitted", "approved", "rejected", "lock")):
        return "State Transition"
    if any(keyword in lowered for keyword in ("validate", "validation", "required", "must", "prevent", "future date")):
        return "Validation"
    if any(keyword in lowered for keyword in ("email", "send", "export", "upload")):
        return "Integration"
    if any(keyword in lowered for keyword in ("search", "sort", "keyword", "relevance")):
        return "Data"
    return "Business"


def _build_business_rules(requirement: Requirement) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"{requirement.id}-BR-01",
            "requirement_id": requirement.id,
            "title": _title_from_requirement(requirement),
            "description": requirement.text,
            "rule_type": _infer_rule_type(requirement.text),
        }
    ]


def _extract_field_constraints(requirement: Requirement) -> List[Dict[str, Any]]:
    text = requirement.text
    lowered = text.lower()
    constraints: List[Dict[str, Any]] = []

    size_match = re.search(r"max\s+(\d+(?:\.\d+)?)\s*(kb|mb|gb)", lowered)
    if size_match:
        value = f"{size_match.group(1)} {size_match.group(2).upper()}"
        constraints.append(
            {
                "id": f"{requirement.id}-FC-{len(constraints) + 1:02d}",
                "requirement_id": requirement.id,
                "field_name": "uploaded file",
                "description": f"Uploaded file size must not exceed {value}.",
                "constraint_type": "File Size",
                "operator": "<=",
                "value": value,
                "negative_example": f"File larger than {value}",
            }
        )

    allowed_extensions = re.findall(r"\b(jpg|png|pdf|csv|xlsx|docx|json)\b", lowered)
    if allowed_extensions:
        allowed_values = ", ".join(extension.upper() for extension in _dedupe_strings(allowed_extensions))
        constraints.append(
            {
                "id": f"{requirement.id}-FC-{len(constraints) + 1:02d}",
                "requirement_id": requirement.id,
                "field_name": "uploaded file",
                "description": f"Only the following file types are allowed: {allowed_values}.",
                "constraint_type": "File Type",
                "value": allowed_values,
                "negative_example": "Upload an unsupported file type",
            }
        )

    login_match = re.search(r"after\s+(\d+)\s+failed login attempts?\s+within\s+(\d+)\s+minutes?", lowered)
    if login_match:
        attempts = login_match.group(1)
        minutes = login_match.group(2)
        constraints.append(
            {
                "id": f"{requirement.id}-FC-{len(constraints) + 1:02d}",
                "requirement_id": requirement.id,
                "field_name": "failed login attempts",
                "description": f"Lock the account after {attempts} failed attempts within {minutes} minutes.",
                "constraint_type": "Range",
                "operator": "<=",
                "value": f"{attempts} in {minutes} minutes",
                "negative_example": "Exceeded failed login threshold",
            }
        )

    if "future" in lowered and "date" in lowered:
        constraints.append(
            {
                "id": f"{requirement.id}-FC-{len(constraints) + 1:02d}",
                "requirement_id": requirement.id,
                "field_name": "date",
                "description": "Future dates are not allowed.",
                "constraint_type": "Range",
                "negative_example": "Expense date is tomorrow",
            }
        )

    if "policy limit" in lowered or "exceeds the configured policy limit" in lowered:
        constraints.append(
            {
                "id": f"{requirement.id}-FC-{len(constraints) + 1:02d}",
                "requirement_id": requirement.id,
                "field_name": "total claimed amount",
                "description": "Total claimed amount must not exceed the configured policy limit.",
                "constraint_type": "Range",
                "operator": "<=",
                "value": "Configured policy limit",
                "negative_example": "Claim amount above policy limit",
            }
        )

    if "require" in lowered and "reason" in lowered:
        constraints.append(
            {
                "id": f"{requirement.id}-FC-{len(constraints) + 1:02d}",
                "requirement_id": requirement.id,
                "field_name": "reason",
                "description": "A reason is required to complete this action.",
                "constraint_type": "Required",
                "negative_example": "Submit or reject without entering a reason",
            }
        )

    return constraints


def _extract_role_permissions(requirement: Requirement) -> List[Dict[str, Any]]:
    text = requirement.text.strip().rstrip(".")
    lowered = text.lower()
    permissions: List[Dict[str, Any]] = []

    exclusive_match = re.search(r"allow only ([a-z][a-z\s-]+?) to ([^.]+)", lowered)
    if exclusive_match:
        role = exclusive_match.group(1).strip().title()
        action = exclusive_match.group(2).strip()
        permissions.append(
            {
                "id": f"{requirement.id}-RP-{len(permissions) + 1:02d}",
                "requirement_id": requirement.id,
                "role": role,
                "action": action,
                "effect": "Allow",
                "conditions": "Exclusive permission",
            }
        )
        return permissions

    general_match = re.search(r"allow ([a-z][a-z\s-]+?) to ([^.]+)", lowered)
    if general_match:
        role = general_match.group(1).strip().title()
        action = general_match.group(2).strip()
        permissions.append(
            {
                "id": f"{requirement.id}-RP-{len(permissions) + 1:02d}",
                "requirement_id": requirement.id,
                "role": role,
                "action": action,
                "effect": "Allow",
                "conditions": None,
            }
        )
        return permissions

    for role in ROLE_PATTERNS:
        if role in lowered:
            permissions.append(
                {
                    "id": f"{requirement.id}-RP-{len(permissions) + 1:02d}",
                    "requirement_id": requirement.id,
                    "role": role.title(),
                    "action": _title_from_requirement(requirement),
                    "effect": "Allow",
                    "conditions": None,
                }
            )
            break

    return permissions


def _extract_state_transitions(requirement: Requirement) -> List[Dict[str, Any]]:
    text = requirement.text.strip().rstrip(".")
    lowered = text.lower()
    transitions: List[Dict[str, Any]] = []

    def add_transition(entity: str, from_state: str, to_state: str, trigger: str, guards: Optional[str] = None) -> None:
        transitions.append(
            {
                "id": f"{requirement.id}-ST-{len(transitions) + 1:02d}",
                "requirement_id": requirement.id,
                "entity": entity,
                "from_state": from_state,
                "to_state": to_state,
                "trigger": trigger,
                "guards": guards,
            }
        )

    if "lock the account" in lowered:
        add_transition("Account", "Active", "Locked", "Exceed failed login threshold")

    if "draft status" in lowered and "submit" in lowered:
        add_transition("Expense report", "Draft", "Submitted", "Employee submits report")

    if "approve" in lowered and "submitted status" in lowered:
        add_transition("Expense report", "Submitted", "Approved", "Manager approves report")

    if "reject" in lowered:
        add_transition("Expense report", "Submitted", "Rejected", "Manager rejects report", "Rejection reason entered")

    if not transitions:
        mentioned_states = [state for state in KNOWN_STATES if state.lower() in lowered]
        if len(mentioned_states) >= 2:
            add_transition("Workflow item", mentioned_states[0], mentioned_states[1], _title_from_requirement(requirement))

    return transitions


def _extract_risk_signals(requirement: Requirement) -> List[Dict[str, Any]]:
    lowered = requirement.text.lower()
    risks: List[Dict[str, Any]] = []

    def add_risk(title: str, rationale: str, category: str, severity: str) -> None:
        risks.append(
            {
                "id": f"{requirement.id}-RS-{len(risks) + 1:02d}",
                "requirement_id": requirement.id,
                "title": title,
                "rationale": rationale,
                "category": category,
                "severity": severity,
            }
        )

    if any(keyword in lowered for keyword in ("sign in", "password", "reset password", "lock the account")):
        add_risk("Authentication edge cases", "Authentication flows are security-sensitive and failure-prone.", "Security", "High")
    if any(keyword in lowered for keyword in ("upload", "file", "jpg", "png", "future date", "validation")):
        add_risk("Input validation gaps", "Uploaded content and validated fields need negative coverage.", "Validation", "Medium")
    if any(keyword in lowered for keyword in ("status", "draft", "submitted", "approved", "rejected")):
        add_risk("Workflow transition errors", "Status-driven behavior can fail at guard conditions or wrong states.", "Workflow", "High")
    if any(keyword in lowered for keyword in ("email", "send", "confirmation email", "export", "csv")):
        add_risk("External dependency behavior", "Integrations or export paths can fail independently of core UI flows.", "Integration", "Medium")

    return risks


def _extract_dependencies(requirement: Requirement) -> List[str]:
    lowered = requirement.text.lower()
    dependencies: List[str] = []

    if "email" in lowered:
        dependencies.append("Email delivery service")
    if any(keyword in lowered for keyword in ("upload", "photo", "file")):
        dependencies.append("File storage service")
    if any(keyword in lowered for keyword in ("search", "relevance", "keyword")):
        dependencies.append("Search indexing service")
    if any(keyword in lowered for keyword in ("export", "csv")):
        dependencies.append("Export service")

    return _dedupe_strings(dependencies)


def _suggested_scenarios(
    requirement: Requirement,
    field_constraints: List[Dict[str, Any]],
    role_permissions: List[Dict[str, Any]],
    state_transitions: List[Dict[str, Any]],
    dependencies: List[str],
) -> List[str]:
    lowered = requirement.text.lower()
    suggestions = ["Happy Path"]

    if any(keyword in lowered for keyword in ("prevent", "lock", "no results", "failed", "reject", "future", "invalid")):
        suggestions.append("Negative")
    if field_constraints:
        suggestions.append("Validation")
    if any(keyword in lowered for keyword in ("max", "min", "limit", "up to", "within")):
        suggestions.append("Boundary")
    if role_permissions:
        suggestions.append("Authorization")
    if state_transitions:
        suggestions.append("State Transition")
    if dependencies:
        suggestions.append("Integration")
    if any(keyword in lowered for keyword in ("search", "sort", "keyword", "results")):
        suggestions.append("Data Variation")

    return _dedupe_strings(suggestions)


def fallback_requirement_analysis(requirements: List[Requirement]) -> List[Dict[str, Any]]:
    analysis: List[Dict[str, Any]] = []
    for requirement in requirements:
        business_rules = _build_business_rules(requirement)
        field_constraints = _extract_field_constraints(requirement)
        role_permissions = _extract_role_permissions(requirement)
        state_transitions = _extract_state_transitions(requirement)
        risk_signals = _extract_risk_signals(requirement)
        dependencies = _extract_dependencies(requirement)
        analysis.append(
            {
                "requirement_id": requirement.id,
                "requirement_text": requirement.text,
                "business_rules": business_rules,
                "field_constraints": field_constraints,
                "role_permissions": role_permissions,
                "state_transitions": state_transitions,
                "risk_signals": risk_signals,
                "suggested_scenarios": _suggested_scenarios(
                    requirement,
                    field_constraints,
                    role_permissions,
                    state_transitions,
                    dependencies,
                ),
                "dependencies": dependencies,
            }
        )
    return analysis


def normalize_requirement_analysis(raw_analysis: List[Dict[str, Any]], requirements: List[Requirement]) -> List[Dict[str, Any]]:
    requirement_lookup = {requirement.id: requirement for requirement in requirements}
    fallback_lookup = {item["requirement_id"]: item for item in fallback_requirement_analysis(requirements)}
    normalized_by_requirement: Dict[str, Dict[str, Any]] = {}

    for item in raw_analysis or []:
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("requirement_id") or "").strip()
        if requirement_id not in requirement_lookup:
            continue

        requirement = requirement_lookup[requirement_id]
        fallback = fallback_lookup[requirement_id]

        business_rules: List[Dict[str, Any]] = []
        for index, rule in enumerate(item.get("business_rules") or [], start=1):
            if not isinstance(rule, dict):
                continue
            title = str(rule.get("title") or rule.get("description") or _title_from_requirement(requirement)).strip()
            description = str(rule.get("description") or rule.get("title") or requirement.text).strip()
            if not title or not description:
                continue
            business_rules.append(
                {
                    "id": str(rule.get("id") or f"{requirement_id}-BR-{index:02d}"),
                    "requirement_id": requirement_id,
                    "title": title,
                    "description": description,
                    "rule_type": _normalize_rule_type(rule.get("rule_type")),
                }
            )

        field_constraints: List[Dict[str, Any]] = []
        for index, constraint in enumerate(item.get("field_constraints") or [], start=1):
            if not isinstance(constraint, dict):
                continue
            field_name = str(constraint.get("field_name") or "field").strip()
            description = str(constraint.get("description") or "").strip()
            if not description:
                continue
            field_constraints.append(
                {
                    "id": str(constraint.get("id") or f"{requirement_id}-FC-{index:02d}"),
                    "requirement_id": requirement_id,
                    "field_name": field_name,
                    "description": description,
                    "constraint_type": _normalize_constraint_type(constraint.get("constraint_type")),
                    "operator": str(constraint.get("operator") or "").strip() or None,
                    "value": str(constraint.get("value") or "").strip() or None,
                    "negative_example": str(constraint.get("negative_example") or "").strip() or None,
                }
            )

        role_permissions: List[Dict[str, Any]] = []
        for index, permission in enumerate(item.get("role_permissions") or [], start=1):
            if not isinstance(permission, dict):
                continue
            role = str(permission.get("role") or "").strip()
            action = str(permission.get("action") or "").strip()
            if not role or not action:
                continue
            role_permissions.append(
                {
                    "id": str(permission.get("id") or f"{requirement_id}-RP-{index:02d}"),
                    "requirement_id": requirement_id,
                    "role": role,
                    "action": action,
                    "effect": _normalize_effect(permission.get("effect")),
                    "conditions": str(permission.get("conditions") or "").strip() or None,
                }
            )

        state_transitions: List[Dict[str, Any]] = []
        for index, transition in enumerate(item.get("state_transitions") or [], start=1):
            if not isinstance(transition, dict):
                continue
            entity = str(transition.get("entity") or "Workflow item").strip()
            from_state = str(transition.get("from_state") or "Unknown").strip()
            to_state = str(transition.get("to_state") or "Unknown").strip()
            if not from_state or not to_state:
                continue
            state_transitions.append(
                {
                    "id": str(transition.get("id") or f"{requirement_id}-ST-{index:02d}"),
                    "requirement_id": requirement_id,
                    "entity": entity,
                    "from_state": from_state,
                    "to_state": to_state,
                    "trigger": str(transition.get("trigger") or "").strip() or None,
                    "guards": str(transition.get("guards") or "").strip() or None,
                }
            )

        risk_signals: List[Dict[str, Any]] = []
        for index, risk in enumerate(item.get("risk_signals") or [], start=1):
            if not isinstance(risk, dict):
                continue
            title = str(risk.get("title") or "").strip()
            rationale = str(risk.get("rationale") or "").strip()
            if not title or not rationale:
                continue
            risk_signals.append(
                {
                    "id": str(risk.get("id") or f"{requirement_id}-RS-{index:02d}"),
                    "requirement_id": requirement_id,
                    "title": title,
                    "rationale": rationale,
                    "category": _normalize_risk_category(risk.get("category")),
                    "severity": _normalize_severity(risk.get("severity")),
                }
            )

        normalized_by_requirement[requirement_id] = {
            "requirement_id": requirement_id,
            "requirement_text": str(item.get("requirement_text") or requirement.text).strip() or requirement.text,
            "business_rules": business_rules or fallback["business_rules"],
            "field_constraints": field_constraints or fallback["field_constraints"],
            "role_permissions": role_permissions or fallback["role_permissions"],
            "state_transitions": state_transitions or fallback["state_transitions"],
            "risk_signals": risk_signals or fallback["risk_signals"],
            "suggested_scenarios": _dedupe_strings(list(item.get("suggested_scenarios") or fallback["suggested_scenarios"])),
            "dependencies": _dedupe_strings(list(item.get("dependencies") or fallback["dependencies"])),
        }

    normalized: List[Dict[str, Any]] = []
    for requirement in requirements:
        item = normalized_by_requirement.get(requirement.id)
        if not item:
            item = fallback_lookup[requirement.id]
        if "Happy Path" not in item["suggested_scenarios"]:
            item["suggested_scenarios"] = ["Happy Path"] + [scenario for scenario in item["suggested_scenarios"] if scenario != "Happy Path"]
        normalized.append(item)

    logging.info("[RequirementAnalysis] Prepared analysis for %s requirement(s)", len(normalized))
    return normalized


def build_requirement_analysis_agent(
    model: str,
    requirements_text: str,
    context_text: str,
    *,
    output_key: str,
    human_feedback: Optional[str] = None,
) -> Agent:
    feedback_section = human_feedback_section("Human Feedback to Consider", human_feedback)

    return Agent(
        name="RequirementAnalysisAgent",
        model=model,
        include_contents="none",
        generate_content_config=json_generation_config(max_output_tokens=12000),
        output_schema=RequirementAnalysisOutput,
        instruction=f"""You are a Senior QA Analyst preparing a structured requirement analysis before scenario planning.

    {TEST_DESIGN_PROMPT_GUARDRAILS}
    {REAL_WORLD_QA_POLICY}

**Requirements:**
{requirements_text}

**Context:**
{context_text}
{feedback_section}
**Your task:**
For each requirement, extract the most relevant test-design intelligence.
Focus on rules that change expected behavior: validations, boundaries, role permissions, state transitions, data retention/integrity, notifications, integrations, audit/compliance obligations, and user-visible error handling.

**Output rules:**
1. Return ONLY a JSON object.
2. Use this exact top-level shape:
{{
  "requirement_analysis": [
    {{
      "requirement_id": "REQ-001",
      "requirement_text": "The system shall ...",
      "business_rules": [
        {{
          "id": "REQ-001-BR-01",
                    "requirement_id": "REQ-001",
          "title": "Short rule title",
          "description": "Concrete rule description",
          "rule_type": "Business"
        }}
      ],
      "field_constraints": [
        {{
          "id": "REQ-001-FC-01",
                    "requirement_id": "REQ-001",
          "field_name": "field name",
          "description": "Constraint description",
          "constraint_type": "Required",
          "operator": "<=",
          "value": "example",
          "negative_example": "Bad input example"
        }}
      ],
      "role_permissions": [
        {{
          "id": "REQ-001-RP-01",
                    "requirement_id": "REQ-001",
          "role": "Manager",
          "action": "Approve report",
          "effect": "Allow",
          "conditions": "Only when status is Submitted"
        }}
      ],
      "state_transitions": [
        {{
          "id": "REQ-001-ST-01",
                    "requirement_id": "REQ-001",
          "entity": "Expense report",
          "from_state": "Submitted",
          "to_state": "Approved",
          "trigger": "Manager approves report",
          "guards": "User has Manager role"
        }}
      ],
      "risk_signals": [
        {{
          "id": "REQ-001-RS-01",
                    "requirement_id": "REQ-001",
          "title": "Short risk title",
          "rationale": "Why this needs attention",
          "category": "Workflow",
          "severity": "High"
        }}
      ],
      "suggested_scenarios": ["Happy Path", "Negative"],
      "dependencies": ["Email delivery service"]
    }}
  ]
}}
3. Always include at least one business rule and one suggested scenario per requirement.
4. Use only these rule types: Business, Validation, Authorization, State Transition, Integration, Notification, Data, Constraint, Other.
5. Use only these constraint types: Required, Format, Length, Range, File Type, File Size, Allowed Values, Uniqueness, Dependency, Other.
6. Use only these risk categories: Security, Data Integrity, Availability, Usability, Compliance, Workflow, Validation, Integration, Other.
7. Use only these severity values: Critical, High, Medium, Low.
8. Keep every extracted item grounded in the requirement or context; if uncertain, put the uncertainty in risk_signals rather than inventing a rule.
9. Suggested scenarios should be concise labels that downstream scenario planning can turn into executable cases.
""",
        description="Extracts structured requirement analysis for downstream coverage planning and generation",
        output_key=output_key,
    )
