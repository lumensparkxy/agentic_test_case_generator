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
_STATE_NAMES = "|".join(state.lower() for state in KNOWN_STATES)
_FILE_EXTENSION = r"jpg|png|pdf|csv|xlsx|docx|json"
_FILE_FORMAT_LIST = rf"\.?(?:{_FILE_EXTENSION})(?:(?:\s*[,/]\s*(?:(?:or|and)\s+)?|\s+(?:or|and)\s+)\.?(?:{_FILE_EXTENSION}))*"
_QUOTED_VALUE = re.compile(r"""["“]([^"”\n]+)["”]|(?<!\w)['‘]([^'’\n]+)['’]|`([^`\n]+)`""")
_POLICY_VALUE = re.compile(rf"(?:{_STATE_NAMES}|\.?(?:{_FILE_EXTENSION}))")
_POLICY_CLAUSE = re.compile(
    r"[;\n]|\.(?=\s|$)|\s+(?:but\b|and\s+(?=(?:reject|disallow|prevent|accept|upload|allow|export|download|show|display|present|return)\b))\s*"
)
_UPLOAD_ACTION = r"(?:upload(?:s|ed|ing)?|attach(?:es|ed|ing)?)"
_UPLOAD_CONTEXT = re.compile(rf"\b{_UPLOAD_ACTION}\b")
_ATTACHMENT_CONTEXT = re.compile(r"\battachments?\b")
_FILE_CLAUSE = re.compile(
    rf",?\s+which\b|,?\s+and\s+(?!(?:\.?(?:{_FILE_EXTENSION})|must|may|can|shall|should|with|up to)\b)"
    r"|,\s+(?=(?:export|download|show|display|present|return)\b)"
)
_NEGATED_UPLOAD = re.compile(
    r"\b(?:reject(?:s|ed|ing)?|disallow(?:s|ed|ing)?|prohibit(?:s|ed|ing)?|unsupported|(?:not|never)\s+(?:allow|accept|upload)"
    r"|not\s+(?:be\s+)?(?:uploaded|accepted|allowed))\b"
)
_EXCLUSIVE_UPLOAD_FORMATS = re.compile(
    rf"\b(?:only\s+(?P<files>{_FILE_FORMAT_LIST})\s+(?:[a-z-]+\s+)?(?:uploads?|attachments?"
    r"|files?(?: types?)?\s+(?:(?:may|can|must|shall) be uploaded|are allowed for upload))\b"
    rf"|(?:upload|accept|allow|support)(?:s|ed)?\s+only\s+(?P<direct>{_FILE_FORMAT_LIST})"
    r"(?=\s+(?:(?:[a-z-]+\s+)?(?:files?|attachments?|uploads?|images?|photos?|documents?)|with|up to)\b|\s*$))"
)
_SUPPORTED_UPLOAD_FORMATS = re.compile(
    rf"\b{_UPLOAD_ACTION}\b[^,]{{0,80}}?\s+(?:in|as)\s+(?P<formats>{_FILE_FORMAT_LIST})\s+(?:file\s+)?formats?\b"
    rf"|\b(?:upload|accept|allow|support)(?:s|ed)?\s+(?P<files>{_FILE_FORMAT_LIST})\s+(?:files?|uploads?|attachments?)\b"
)
_UPLOAD_SIZE = re.compile(r"\b(?:max(?:imum)?|up to|at most|no more than|must not exceed)\s+(\d+(?:\.\d+)?)\s*(kb|mb|gb)\b")
_STATUS_REFERENCE = re.compile(
    rf"\b(?:(?:{_STATE_NAMES})\s+(?:status|state|expense reports?)|(?:status|state)\s+is\s+(?:{_STATE_NAMES})"
    rf"|(?:when|while|if|unless)\s+[\w -]{{1,50}}?\s+(?:is|remains)\s+(?:{_STATE_NAMES}))\b"
)
_STATUS_CONDITION = re.compile(r"\b(?:only|when|while|until|if|unless|keep|remain)\b")
_WORKFLOW_ACTION = r"(?:chang(?:e|ing)|mov(?:e|ing)|transition(?:ing)?|revert(?:ing)?|set(?:ting)?|lock(?:ing)?|reject(?:ing)?|approv(?:e|ing)|submit(?:ting)?)"
_BLOCKED_WORKFLOW_ACTION = re.compile(
    rf"\b(?:(?:not|never)\s+(?:(?:allow|permit)\b[^,]{{0,80}}?\s+)?{_WORKFLOW_ACTION}"
    rf"|(?:prevent|prohibit|disallow|forbid)(?:s|ed)?\b[^,]{{0,80}}?\s+{_WORKFLOW_ACTION}"
    r"|(?:is|are)\s+(?:prohibited|disallowed|forbidden|not allowed))\b"
)
_ORDERED_TRANSITION = re.compile(
    rf"\b(?:change|move|transition|revert|set)(?:s|d|ed)?\s+(?:(?:(?P<object>[^,]{{0,80}}?)\bfrom|the)\s+)?"
    rf"(?P<before>{_STATE_NAMES})(?:\s+(?P<kind>expense reports?|status|state))?\s+to\s+(?P<after>{_STATE_NAMES})\b"
)
_EXPENSE_REPORT = re.compile(r"\bexpense reports?\b")
_EXPENSE_REJECTION = re.compile(r"\breject(?:s|ing)?\s+(?:(?:an?|the|submitted)\s+)*expense reports?\b(?!\s+(?:attachments?|files?|payloads?))")
_REQUIRED_REASON = re.compile(r"\brequir(?:e[sd]?|ing)\b[^.]*\breason\b|\breason\b[^.]*\bis required\b")
_OPTIONAL_REASON = re.compile(r"\b(?:not|never)\s+requir(?:e[sd]?|ing)\b")
_ACTION_START = r"(?:^\s*|,\s*|\b(?:to|can|may|must|shall|should|will|then|and|system|application|users?|customers?)\s+(?:(?:not|never)\s+)?)"
_DISPLAY_OBJECT = r"(?:buttons?|labels?|captions?|links?|tabs?|menus?|icons?|previews?|fields?|history|pages?|dialogs?|options?|status)"
_DISPLAY_PHRASE = rf"(?:[\w-]+\s+){{0,2}}{_DISPLAY_OBJECT}\b"
_FILE_TRANSFER = r"(?:export|download|import|upload)"
_FILE_OR_INSTALL_ACTION = re.compile(
    rf"{_ACTION_START}(?:{_FILE_TRANSFER}|attach|install|uninstall|upgrade)(?:s|es)?\b(?!\s+{_DISPLAY_PHRASE})\s+\S"
    r"|\b(?:be|is|are)\s+(?:exported|downloaded|imported|uploaded|attached|installed|uninstalled|upgraded)\b"
    r"|\buploaded\s+(?:file|attachment)\s+(?:must|shall|may|can)\b"
    rf"|{_ACTION_START}(?:provide|offer|generate)s?\s+(?:(?:a|an|the)\s+)?(?:{_FILE_EXTENSION})\s+(?:download|export)\b(?!\s+{_DISPLAY_PHRASE})"
    rf"|\ballows?\s+(?:(?:authenticated|authorized|secure)\s+)?{_FILE_TRANSFER}\s+of\b"
)
_DELIVERY_CHANNEL = r"(?:e-?mails?(?!\s+(?:address(?:es)?|fields?|inputs?|values?|preferences?)\b)|sms|webhooks?)"
_DELIVERY_ACTION = re.compile(
    rf"{_ACTION_START}(?:send|deliver|receive)(?:s)?\s+[^,]{{0,72}}?\b{_DELIVERY_CHANNEL}\b"
    rf"|\b{_DELIVERY_CHANNEL}\s+(?:is|are|(?:must|may|can|should|will|shall) be)\s+(?:sent|delivered|received)\b"
    rf"|{_ACTION_START}e-?mail(?:s)?\s+(?:a|an|the)\s+(?!{_DISPLAY_OBJECT}\b)\S"
    rf"|{_ACTION_START}notif(?:y|ies)\s+[^,]{{0,60}}?\b(?:by|via)\s+{_DELIVERY_CHANNEL}\b"
    r"|\breset\b[^,]{0,72}?\bvia (?:an? )?e-?mail (?:link|message)\b"
)
_EXTERNAL_RESOURCE = r"(?:apis?|endpoints?|webhooks?|providers?|services?)"
_EXTERNAL_TARGET = rf"(?:[\w-]+\s+){{0,5}}{_EXTERNAL_RESOURCE}\b(?!\s+(?:keys?|tokens?|accounts?|{_DISPLAY_OBJECT})\b)"
_EXTERNAL_EXCHANGE = re.compile(
    rf"{_ACTION_START}(?:call|invoke|query|use|contact|integrate with)s?\s+{_EXTERNAL_TARGET}"
    rf"|\b(?:send|post|forward)s?\s+[^,]{{0,72}}?\bto\s+{_EXTERNAL_TARGET}"
    rf"|\bdepends? on\s+{_EXTERNAL_TARGET}"
)
_EXTERNAL_NAME = re.compile(rf"[\w-]+(?:\s+[\w-]+){{0,4}}\s+{_EXTERNAL_RESOURCE}")
_TARGET_CONTEXT = re.compile(r"\b(?:to|on|with|via|call|invoke|query|use|contact)\s+(?:(?:a|an|the)\s+)?$")
_OPERATION_CONDITION = re.compile(r"\b(?:when|if|unless|while|before|after)\b")
_INPUT_VALIDATION = re.compile(r"\b(?:future dates?|validation|validates?)\b")

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


def _policy_clauses(text: str, *, keep_external_targets: bool = False) -> list[str]:
    # Retain exact quoted state names and file extensions inside policy syntax.
    # A UI message or sample name cannot supply the surrounding policy.
    def policy_value(match: re.Match[str]) -> str:
        value = next(value for value in match.groups() if value is not None)
        if _POLICY_VALUE.fullmatch(value):
            return value
        if keep_external_targets and _EXTERNAL_NAME.fullmatch(value) and _TARGET_CONTEXT.search(match.string[max(0, match.start() - 32) : match.start()]):
            return value.split()[-1]
        return " "

    return _POLICY_CLAUSE.split(_QUOTED_VALUE.sub(policy_value, text.lower()))


def has_integration_evidence(text: str) -> bool:
    """Recognize observable transfer, delivery, or explicitly external operations."""
    return any(
        pattern.search(clause)
        for policy_clause in _policy_clauses(text, keep_external_targets=True)
        for clause in _OPERATION_CONDITION.split(policy_clause)
        for pattern in (_FILE_OR_INSTALL_ACTION, _DELIVERY_ACTION, _EXTERNAL_EXCHANGE)
    )


def _infer_rule_type(text: str, workflow_evidence: bool, integration_evidence: bool) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("allow only", "permission", "role", "admin", "manager", "employee")):
        return "Authorization"
    if workflow_evidence:
        return "State Transition"
    if integration_evidence:
        return "Integration"
    if any(keyword in lowered for keyword in ("validate", "validation", "required", "must", "prevent", "future date")):
        return "Validation"
    if any(keyword in lowered for keyword in ("search", "sort", "keyword", "relevance")):
        return "Data"
    return "Business"


def _build_business_rules(requirement: Requirement, workflow_evidence: bool, integration_evidence: bool) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"{requirement.id}-BR-01",
            "requirement_id": requirement.id,
            "title": _title_from_requirement(requirement),
            "description": requirement.text,
            "rule_type": _infer_rule_type(requirement.text, workflow_evidence, integration_evidence),
        }
    ]


def _extract_field_constraints(requirement: Requirement, policy_clauses: list[str]) -> List[Dict[str, Any]]:
    lowered = requirement.text.lower()
    constraints: List[Dict[str, Any]] = []

    file_clauses = [
        clause
        for policy_clause in policy_clauses
        for clause in _FILE_CLAUSE.split(policy_clause)
        if (_UPLOAD_CONTEXT.search(clause) or _ATTACHMENT_CONTEXT.search(clause)) and not _NEGATED_UPLOAD.search(clause)
    ]
    size_match = next((match for clause in file_clauses if _UPLOAD_CONTEXT.search(clause) and (match := _UPLOAD_SIZE.search(clause))), None)
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

    exclusive = next((match for clause in file_clauses if (match := _EXCLUSIVE_UPLOAD_FORMATS.search(clause))), None)
    if format_match := exclusive or next((match for clause in file_clauses if (match := _SUPPORTED_UPLOAD_FORMATS.search(clause))), None):
        listed_formats = next(group for group in format_match.groups() if group is not None)
        extensions = re.findall(rf"\b({_FILE_EXTENSION})\b", listed_formats)
        allowed_values = ", ".join(extension.upper() for extension in _dedupe_strings(extensions))
        description = f"Only the following file types are allowed: {allowed_values}." if exclusive else f"Supported upload file types: {allowed_values}."
        constraints.append(
            {
                "id": f"{requirement.id}-FC-{len(constraints) + 1:02d}",
                "requirement_id": requirement.id,
                "field_name": "uploaded file",
                "description": description,
                "constraint_type": "File Type",
                "value": allowed_values,
                "negative_example": "Upload an unsupported file type" if exclusive else None,
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

    if any(_REQUIRED_REASON.search(clause) and not _OPTIONAL_REASON.search(clause) for clause in policy_clauses):
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


def _extract_state_transitions(requirement: Requirement, policy_clauses: list[str]) -> List[Dict[str, Any]]:
    transitions: List[Dict[str, Any]] = []

    def add_transition(entity: str, from_state: str, to_state: str, guards: Optional[str] = None) -> None:
        transitions.append(
            {
                "id": f"{requirement.id}-ST-{len(transitions) + 1:02d}",
                "requirement_id": requirement.id,
                "entity": entity,
                "from_state": from_state,
                "to_state": to_state,
                "trigger": _title_from_requirement(requirement),
                "guards": guards,
            }
        )

    for clause in policy_clauses:
        if _BLOCKED_WORKFLOW_ACTION.search(clause):
            continue
        expense = bool(_EXPENSE_REPORT.search(clause))
        rejection = bool(_EXPENSE_REJECTION.search(clause))
        reason = (
            "Rejection reason entered"
            if rejection and _REQUIRED_REASON.search(clause) and not _OPTIONAL_REASON.search(clause) and "rejection reason" in clause
            else None
        )
        if change := _ORDERED_TRANSITION.search(clause):
            subject = change["object"] or change["kind"] or clause[: change.start()].rsplit(",", 1)[-1]
            entity = "Workflow item"
            if _EXPENSE_REPORT.search(subject) or (expense and re.search(r"\breports?\s*$", subject)):
                entity = "Expense report"
            elif re.search(r"\baccount\b", subject):
                entity = "Account"
            guards = reason if entity == "Expense report" and change["after"] == "rejected" else None
            add_transition(entity, change["before"].title(), change["after"].title(), guards)
        elif re.search(r"\block(?:s|ed|ing)?\s+(?:(?:an?|the)\s+)?account\b", clause):
            add_transition("Account", "Unknown", "Locked")
        elif rejection:
            source = "Submitted" if re.search(r"\bsubmitted (?:expense reports?|status|state)\b", clause) else "Unknown"
            add_transition("Expense report", source, "Rejected", reason)
        elif expense and re.search(r"\bdraft status\b.*\buntil\b.*\bsubmit(?:s|ted)?\b", clause):
            add_transition("Expense report", "Draft", "Submitted")
        elif expense and re.search(r"\bapprove(?:s|ing)?\b.*\bsubmitted status\b", clause):
            add_transition("Expense report", "Submitted", "Approved")

    return transitions


def _extract_risk_signals(requirement: Requirement, workflow_evidence: bool, integration_evidence: bool, validation_evidence: bool) -> List[Dict[str, Any]]:
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
    if validation_evidence:
        add_risk("Input validation gaps", "The stated input constraints need negative coverage.", "Validation", "Medium")
    if workflow_evidence:
        add_risk("Workflow transition errors", "Status-driven behavior can fail at guard conditions or wrong states.", "Workflow", "High")
    if integration_evidence:
        add_risk("Integration outcome", "Verify the observable result of the stated operation.", "Integration", "Medium")

    return risks


def _suggested_scenarios(
    requirement: Requirement,
    field_constraints: List[Dict[str, Any]],
    role_permissions: List[Dict[str, Any]],
    state_transitions: List[Dict[str, Any]],
    integration_evidence: bool,
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
    if integration_evidence:
        suggestions.append("Integration")
    if any(keyword in lowered for keyword in ("search", "sort", "keyword", "results")):
        suggestions.append("Data Variation")

    return _dedupe_strings(suggestions)


def fallback_requirement_analysis(requirements: List[Requirement]) -> List[Dict[str, Any]]:
    analysis: List[Dict[str, Any]] = []
    for requirement in requirements:
        policy_clauses = _policy_clauses(requirement.text)
        integration_evidence = has_integration_evidence(requirement.text)
        state_transitions = _extract_state_transitions(requirement, policy_clauses)
        workflow_evidence = bool(state_transitions or any(_STATUS_REFERENCE.search(clause) and _STATUS_CONDITION.search(clause) for clause in policy_clauses))
        business_rules = _build_business_rules(requirement, workflow_evidence, integration_evidence)
        field_constraints = _extract_field_constraints(requirement, policy_clauses)
        role_permissions = _extract_role_permissions(requirement)
        validation_evidence = any(_INPUT_VALIDATION.search(clause) for clause in policy_clauses) or any(
            constraint["constraint_type"] in {"File Type", "File Size"} for constraint in field_constraints
        )
        risk_signals = _extract_risk_signals(requirement, workflow_evidence, integration_evidence, validation_evidence)
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
                    integration_evidence,
                ),
                "dependencies": [],
            }
        )
    return analysis


def normalize_requirement_text(value: Any, source_text: str) -> str:
    """Use the exact source text when the model echo differs only at the boundaries."""
    text = str(value or "").strip()
    return source_text if not text or text == source_text.strip() else text


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
            "requirement_text": normalize_requirement_text(item.get("requirement_text"), requirement.text),
            "business_rules": business_rules or fallback["business_rules"],
            "field_constraints": field_constraints or fallback["field_constraints"],
            "role_permissions": role_permissions or fallback["role_permissions"],
            "state_transitions": state_transitions or fallback["state_transitions"],
            "risk_signals": risk_signals or fallback["risk_signals"],
            "suggested_scenarios": _dedupe_strings(list(item.get("suggested_scenarios") or fallback["suggested_scenarios"])),
            "dependencies": _dedupe_strings(list(item.get("dependencies") or [])),
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
3. Extract supported rules and scenarios. When source behavior is ambiguous or absent, record the unresolved gap; do not invent a rule to meet a minimum count.
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
