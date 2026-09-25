"""Conservative static admission for supplied automation evidence; never executes code."""

import ast
import re
from urllib.parse import urlsplit, urlunsplit

from ..contracts.automation import AutomationInput


def test_function_name(case_id: str) -> str:
    return "test_" + re.sub(r"[^a-zA-Z0-9_]+", "_", case_id.lower()).strip("_")


def evidence_for(payload: AutomationInput):
    return {item.test_case_id: item for item in payload.interface_evidence}


def missing_evidence_reason(case, payload: AutomationInput) -> str | None:
    if sum(test_function_name(item.id) == test_function_name(case.id) for item in payload.test_cases) != 1:
        return "Case IDs must map to unique Python test function names."
    if len({step.step for step in case.steps}) != len(case.steps):
        return "Source step numbers must be unique before assertion mappings can be verified."
    if case.automation_status == "Manual":
        return "The source case is marked Manual."
    if not case.steps:
        return "No concrete source steps are available."
    if not payload.target_base_url:
        return "Supply an authorized target_base_url and per-case interface evidence. No default URL is invented."
    evidence = evidence_for(payload).get(case.id)
    if not evidence or not evidence.source_reference.strip():
        return "Supply per-case interface_evidence with a source reference, observed locators and assertion mappings."
    expected_steps = {step.step for step in case.steps if step.expected.strip()}
    if case.expected_result and case.expected_result not in {step.expected for step in case.steps}:
        expected_steps.add(0)
    if not expected_steps or {item.step for item in evidence.assertions} != expected_steps:
        return "Map each source step's expected result to a supported, evidenced assertion before generating executable code."
    for assertion in evidence.assertions:
        page_assertion = assertion.method in {"to_have_url", "to_have_title"}
        if not page_assertion and assertion.locator is None:
            return "Element assertions require an observed locator."
        if (
            assertion.method in {"to_have_text", "to_contain_text", "to_have_value", "to_have_url", "to_have_title", "to_have_count"}
            and assertion.expected is None
        ):
            return "Value assertions require the source-backed expected value."
    return None


def _url(value):
    parts = urlsplit(str(value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, parts.fragment))


def _literal(node, constants):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _locator(node, constants):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in {
        "locator",
        "get_by_role",
        "get_by_label",
        "get_by_text",
        "get_by_test_id",
        "get_by_placeholder",
        "get_by_alt_text",
        "get_by_title",
    }:
        return None
    if len(node.args) != 1:
        return (node.func.attr, None, None)
    keywords = {item.arg: _literal(item.value, constants) for item in node.keywords}
    if set(keywords) - {"name", "exact"}:
        return (node.func.attr, None, None)
    return (node.func.attr, _literal(node.args[0], constants), keywords.get("name"))


def _signature(locator):
    return (locator.method, locator.value, locator.name)


def validate_artifacts(files: dict[str, str], payload: AutomationInput) -> list[str]:
    """Syntax and declared evidence checks; not a sandbox or business acceptance."""
    issues = []
    trees = {}
    constants = {}
    for path, source in files.items():
        if not path.endswith(".py") or path.startswith("/") or ".." in path.replace("\\", "/").split("/"):
            issues.append(f"Unsafe or unsupported artifact path: {path}")
            continue
        try:
            tree = ast.parse(source, filename=path)
            compile(tree, path, "exec")
            trees[path] = tree
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            constants[target.id] = node.value.value
        except SyntaxError, ValueError:
            issues.append(f"Invalid Python artifact: {path}")
    evidence = evidence_for(payload)
    all_locators = {
        _signature(locator)
        for item in evidence.values()
        for locator in [*item.locators, *[assertion.locator for assertion in item.assertions if assertion.locator]]
    }
    allowed_urls = {_url(payload.target_base_url)} if payload.target_base_url else set()
    allowed_urls.update(_url(url) for item in evidence.values() for url in item.allowed_urls)
    input_values = {value for item in evidence.values() for value in item.input_values}
    test_functions = {}
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                filename = path.rsplit("/", 1)[-1]
                if node not in tree.body or not (filename.startswith("test_") or filename.endswith("_test.py")):
                    issues.append(f"Test function is not a top-level discoverable pytest case: {node.name}")
                if node.name in test_functions:
                    issues.append(f"Duplicate test function: {node.name}")
                test_functions[node.name] = node
            if isinstance(node, ast.keyword) and node.arg == "ignore_https_errors" and _literal(node.value, constants) is not False:
                issues.append("TLS verification may not be disabled.")
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if _literal(key, constants) == "ignore_https_errors" and _literal(value, constants) is not False:
                        issues.append("TLS verification may not be disabled.")
            if isinstance(node, ast.Try) and any(not any(isinstance(statement, ast.Raise) for statement in handler.body) for handler in node.handlers):
                issues.append("Exceptions or assertions may not be suppressed.")
            if not isinstance(node, ast.Call):
                continue
            method = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
            if method in {"sleep", "wait_for_timeout", "skip", "skipif", "xfail", "eval", "exec"}:
                issues.append(f"Unsupported execution shortcut: {method}")
            locator = _locator(node, constants)
            if locator and locator not in all_locators:
                issues.append(f"Locator lacks supplied interface evidence: {locator[0]}")
            if method in {"goto", "navigate"}:
                value = _literal(node.args[0], constants) if node.args else None
                if not isinstance(value, str) or _url(value) not in allowed_urls:
                    issues.append("Navigation URL is not explicitly evidenced.")
            if method in {"fill", "type", "press_sequentially"}:
                value = _literal(node.args[-1], constants) if node.args else None
                if value not in input_values:
                    issues.append("Input or credential value is not explicitly supplied.")
    expected_functions = {test_function_name(case.id) for case in payload.test_cases}
    if set(test_functions) != expected_functions:
        issues.append("Test function identities/count do not match eligible source cases.")
    for case in payload.test_cases:
        function = test_functions.get(test_function_name(case.id))
        item = evidence.get(case.id)
        if not function or not item:
            continue
        if isinstance(function, ast.AsyncFunctionDef) or any(
            isinstance(node, (ast.If, ast.For, ast.While, ast.Return, ast.Raise, ast.Yield, ast.YieldFrom)) for node in ast.walk(function)
        ):
            issues.append(f"Unsupported control flow could bypass assertions in {case.id}.")
        if function.decorator_list:
            issues.append(f"Unexpected test decorator can skip or alter {case.id}.")
        observed = set()
        # Assertions must be direct statements in the test, not dead branches,
        # comments or an unrelated helper that could never be called.
        for statement in function.body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                continue
            call = statement.value
            if not isinstance(call.func, ast.Attribute):
                continue
            receiver = call.func.value
            if not isinstance(receiver, ast.Call) or not isinstance(receiver.func, ast.Name) or receiver.func.id != "expect" or len(receiver.args) != 1:
                continue
            target = receiver.args[0]
            locator = _locator(target, constants)
            if isinstance(target, ast.Name) and target.id == "page":
                locator = None
            elif locator is None:
                continue
            expected = _literal(call.args[0], constants) if call.args else None
            observed.add((call.func.attr, locator, expected))
        for assertion in item.assertions:
            required = (assertion.method, _signature(assertion.locator) if assertion.locator else None, assertion.expected)
            if required not in observed:
                issues.append(f"Missing evidenced assertion for {case.id} step {assertion.step}.")
    return list(dict.fromkeys(issues))
