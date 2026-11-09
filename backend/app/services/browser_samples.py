"""Bind a generated browser operand to a literal declared in authored Context notes."""

import json
import re
from collections.abc import Mapping

from plain_english_test_framework.validation import find_secret_issues

from .execution_service import QUOTED_FILL_LITERAL, STEP_DATA_ASSERTION, STEP_DATA_FILL_ACTION, _assertion_statement


_NAME = r"[a-z][a-z0-9_-]{0,63}"
_FLAGS = re.IGNORECASE | re.ASCII
_DECLARATION = re.compile(rf'Browser sample "(?P<name>{_NAME})"(?P<tail>.*)', _FLAGS)
_DECLARATION_VALUE = re.compile(r"\s+is\s+(?P<encoded>.+)", _FLAGS)
_FILL_PREFIX = re.compile(r"(?:enter|fill|type|input)\s+(?=browser\s+sample\b)", _FLAGS)
_ASSERTION_PREFIX = re.compile(
    rf"(?:field|css|test\s+id)\s+{QUOTED_FILL_LITERAL}\s+(?:value|text)\s+should\s+equal\s+(?=browser\s+sample\b)",
    _FLAGS,
)
_OPERAND = re.compile(rf'browser\s+sample\s+"(?P<name>{_NAME})"', _FLAGS)
_MAX_SAMPLE_BYTES = 16_000


def declared_browser_samples(notes: str | None) -> dict[str, str]:
    samples: dict[str, str] = {}
    seen: set[str] = set()
    for line in (notes or "").splitlines():
        match = _DECLARATION.fullmatch(line.strip())
        if not match:
            continue
        name = match["name"].lower()
        if name in seen:
            samples.pop(name, None)
            continue
        seen.add(name)
        operand = _DECLARATION_VALUE.fullmatch(match["tail"])
        if not operand:
            continue
        try:
            value = json.loads(operand["encoded"])
            if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_SAMPLE_BYTES:
                continue
        except ValueError, RecursionError:
            continue
        if not find_secret_issues(value, message="Credential-looking sample", code="secret.raw_value"):
            samples[name] = value
    return samples


def _replace_sample_operand(text: str) -> tuple[str, str | None]:
    statement = _assertion_statement(text)
    fill = _FILL_PREFIX.match(statement)
    prefix = fill or _ASSERTION_PREFIX.match(statement)
    if not prefix:
        return text, None
    operand = _OPERAND.match(statement, prefix.end())
    if operand is None:
        raise ValueError("Use a quoted browser sample name.")
    resolved = statement[: prefix.end()] + "step data" + statement[operand.end() :]
    grammar = STEP_DATA_FILL_ACTION if fill else STEP_DATA_ASSERTION
    if grammar.fullmatch(resolved) is None:
        raise ValueError("Use the browser sample in one complete action or check.")
    return resolved, operand["name"].lower()


def materialize_browser_sample(action: str, expected: str, samples: Mapping[str, str]) -> tuple[str, str, str] | None:
    """Return ordinary step-data instructions and their exact value, if named."""
    action, input_name = _replace_sample_operand(action)
    expected, expected_name = _replace_sample_operand(expected)
    if input_name and expected_name and input_name != expected_name:
        raise ValueError("A step can refer to only one browser sample. Separate different samples into two steps.")
    name = input_name or expected_name
    if name is None:
        return None
    if name not in samples:
        raise ValueError(f'Browser sample "{name}" is missing, duplicate, or invalid in Context notes.')
    return action, expected, samples[name]
