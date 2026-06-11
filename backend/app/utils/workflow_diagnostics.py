from typing import Any, Dict


RETRYABLE_PARSER_FAILURE_KEY = "_retryable_parser_failure"
RETRY_REASON_KEY = "_retry_reason"


def mark_retryable_parser_failure(diagnostics: Dict[str, Any], reason: str) -> None:
    diagnostics[RETRYABLE_PARSER_FAILURE_KEY] = True
    diagnostics[RETRY_REASON_KEY] = str(reason or "parser_failure").strip() or "parser_failure"


def has_retryable_parser_failure(diagnostics: Dict[str, Any]) -> bool:
    return bool(diagnostics.get(RETRYABLE_PARSER_FAILURE_KEY))


def retry_reason(diagnostics: Dict[str, Any]) -> str:
    return str(diagnostics.get(RETRY_REASON_KEY) or "parser_failure")


def public_workflow_diagnostics(diagnostics: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in diagnostics.items() if not str(key).startswith("_")}
