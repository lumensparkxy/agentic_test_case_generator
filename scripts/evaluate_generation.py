#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

test_case_agent = importlib.import_module("app.agents.test_case_agent")
models_module = importlib.import_module("app.models")

DEFAULT_TEST_CASE_THRESHOLD = test_case_agent.DEFAULT_TEST_CASE_THRESHOLD
_build_response = test_case_agent._build_response
_compute_planned_scenario_metrics = test_case_agent._compute_planned_scenario_metrics
_compute_test_case_coverage_metrics = test_case_agent._compute_test_case_coverage_metrics
_fallback_coverage_plan = test_case_agent._fallback_coverage_plan
_fallback_raw_test_cases = test_case_agent._fallback_raw_test_cases
_heuristic_test_case_review = test_case_agent._heuristic_test_case_review
_hydrate_test_cases = test_case_agent._hydrate_test_cases
_make_history_entry = test_case_agent._make_history_entry
generate_test_cases = test_case_agent.generate_test_cases
GenerateTestCasesInput = models_module.GenerateTestCasesInput

ALLOWED_PRIORITIES = {"Critical", "High", "Medium", "Low"}
ALLOWED_TYPES = {
    "Functional",
    "Integration",
    "E2E",
    "Regression",
    "Smoke",
    "Security",
    "Performance",
    "Usability",
    "UAT",
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _slugify_scenario(name: str) -> str:
    return str(name).strip().lower().replace("_", "-").replace(" ", "-")


def _has_model_credentials() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def _generate_offline_fallback(payload: Any) -> dict[str, Any]:
    coverage_plan = _fallback_coverage_plan(payload.requirements)
    raw_test_cases = _fallback_raw_test_cases(
        payload.requirements,
        payload.context,
        coverage_plan=coverage_plan,
    )
    review = _heuristic_test_case_review(
        raw_test_cases,
        payload.requirements,
        DEFAULT_TEST_CASE_THRESHOLD,
        coverage_plan=coverage_plan,
        context=payload.context,
    )
    workflow = {
        "test_cases": raw_test_cases,
        "coverage_plan": coverage_plan,
        "review": review,
        "approved": review["approved"],
        "iteration_history": [
            _make_history_entry(
                iteration=1,
                actor="OfflineFallback",
                review=review,
                test_cases=raw_test_cases,
            )
        ],
        "coverage_metrics": {
            **_compute_test_case_coverage_metrics(raw_test_cases, payload.requirements),
            **_compute_planned_scenario_metrics(coverage_plan, raw_test_cases, payload.requirements),
        },
    }
    test_cases = _hydrate_test_cases(raw_test_cases)
    return _build_response(test_cases, workflow, payload.requirements, payload.context)


def _test_cases_to_dicts(test_cases: list[Any]) -> list[dict[str, Any]]:
    return [test_case.model_dump() for test_case in test_cases]


def _compute_structural_metrics(test_cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(test_cases)
    with_descriptions = sum(1 for case in test_cases if str(case.get("description") or "").strip())
    with_expected_results = sum(1 for case in test_cases if str(case.get("expected_result") or "").strip())
    with_preconditions = sum(1 for case in test_cases if str(case.get("preconditions") or "").strip())
    with_two_or_more_steps = sum(1 for case in test_cases if len(case.get("steps") or []) >= 2)
    with_requirement_tags = sum(1 for case in test_cases if any(str(tag).strip().startswith("REQ-") for tag in (case.get("tags") or [])))
    invalid_priorities = [case["id"] for case in test_cases if case.get("priority") not in ALLOWED_PRIORITIES]
    invalid_types = [case["id"] for case in test_cases if case.get("type") not in ALLOWED_TYPES]
    untitled_cases = [case["id"] for case in test_cases if not str(case.get("title") or "").strip() or "untitled" in str(case.get("title") or "").lower()]

    def ratio(count: int) -> float:
        return round(count / total, 2) if total else 0.0

    return {
        "total_test_cases": total,
        "with_descriptions": with_descriptions,
        "with_description_ratio": ratio(with_descriptions),
        "with_expected_results": with_expected_results,
        "with_expected_result_ratio": ratio(with_expected_results),
        "with_preconditions": with_preconditions,
        "with_precondition_ratio": ratio(with_preconditions),
        "with_two_or_more_steps": with_two_or_more_steps,
        "with_two_or_more_steps_ratio": ratio(with_two_or_more_steps),
        "with_requirement_tags": with_requirement_tags,
        "with_requirement_tag_ratio": ratio(with_requirement_tags),
        "invalid_priorities": invalid_priorities,
        "invalid_types": invalid_types,
        "untitled_cases": untitled_cases,
    }


def _compute_scenario_distribution(test_cases: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for case in test_cases:
        for tag in case.get("tags") or []:
            normalized = str(tag).strip().lower()
            if normalized.startswith("scenario:"):
                counter[normalized.split(":", 1)[1]] += 1
    return dict(sorted(counter.items()))


def _evaluate_expectations(
    expectation: dict[str, Any] | None,
    review: dict[str, Any],
    coverage_metrics: dict[str, Any],
    structural_metrics: dict[str, Any],
    scenario_distribution: dict[str, int],
) -> dict[str, Any]:
    if not expectation:
        return {"checks": [], "all_met": True}

    checks: list[dict[str, Any]] = []

    def add_check(name: str, expected: Any, actual: Any, met: bool) -> None:
        checks.append({"name": name, "expected": expected, "actual": actual, "met": bool(met)})

    if "minimum_test_cases" in expectation:
        add_check(
            "minimum_test_cases",
            expectation["minimum_test_cases"],
            structural_metrics["total_test_cases"],
            structural_metrics["total_test_cases"] >= int(expectation["minimum_test_cases"]),
        )

    if "minimum_review_score" in expectation:
        add_check(
            "minimum_review_score",
            expectation["minimum_review_score"],
            review.get("score", 0),
            int(review.get("score", 0)) >= int(expectation["minimum_review_score"]),
        )

    if "minimum_structured_case_ratio" in expectation:
        add_check(
            "minimum_structured_case_ratio",
            expectation["minimum_structured_case_ratio"],
            structural_metrics["with_two_or_more_steps_ratio"],
            structural_metrics["with_two_or_more_steps_ratio"] >= float(expectation["minimum_structured_case_ratio"]),
        )

    if "minimum_traceability_ratio" in expectation:
        add_check(
            "minimum_traceability_ratio",
            expectation["minimum_traceability_ratio"],
            coverage_metrics.get("traceability_coverage_ratio", 0.0),
            float(coverage_metrics.get("traceability_coverage_ratio", 0.0)) >= float(expectation["minimum_traceability_ratio"]),
        )

    if "minimum_expected_result_ratio" in expectation:
        add_check(
            "minimum_expected_result_ratio",
            expectation["minimum_expected_result_ratio"],
            structural_metrics["with_expected_result_ratio"],
            structural_metrics["with_expected_result_ratio"] >= float(expectation["minimum_expected_result_ratio"]),
        )

    if "minimum_description_ratio" in expectation:
        add_check(
            "minimum_description_ratio",
            expectation["minimum_description_ratio"],
            structural_metrics["with_description_ratio"],
            structural_metrics["with_description_ratio"] >= float(expectation["minimum_description_ratio"]),
        )

    if expectation.get("must_cover_all_requirements"):
        add_check(
            "must_cover_all_requirements",
            True,
            coverage_metrics.get("requirements_without_tests", []),
            not bool(coverage_metrics.get("requirements_without_tests")),
        )

    if expectation.get("recommended_scenarios"):
        for scenario_name in expectation["recommended_scenarios"]:
            slug = _slugify_scenario(scenario_name)
            add_check(
                f"scenario_present:{slug}",
                True,
                slug in scenario_distribution,
                slug in scenario_distribution,
            )

    return {
        "checks": checks,
        "all_met": all(check["met"] for check in checks),
    }


def _build_benchmark_result(
    input_path: Path,
    expectation_path: Path | None,
    payload: Any,
    execution_mode: str,
) -> dict[str, Any]:
    expectation = _load_json(expectation_path) if expectation_path and expectation_path.exists() else None
    generation_result = generate_test_cases(payload) if execution_mode == "model-backed" else _generate_offline_fallback(payload)
    test_cases = _test_cases_to_dicts(generation_result["test_cases"])
    review = generation_result["review"]
    coverage_metrics = generation_result["coverage_metrics"]
    structural_metrics = _compute_structural_metrics(test_cases)
    scenario_distribution = _compute_scenario_distribution(test_cases)
    expectation_result = _evaluate_expectations(
        expectation,
        review,
        coverage_metrics,
        structural_metrics,
        scenario_distribution,
    )

    return {
        "name": input_path.stem,
        "input_file": str(input_path.relative_to(REPO_ROOT)),
        "expectation_file": str(expectation_path.relative_to(REPO_ROOT)) if expectation_path and expectation_path.exists() else None,
        "description": (expectation or {}).get("description", ""),
        "execution_mode": execution_mode,
        "requirement_count": len(payload.requirements),
        "review": review,
        "coverage_metrics": coverage_metrics,
        "structural_metrics": structural_metrics,
        "scenario_distribution": scenario_distribution,
        "expectation_result": expectation_result,
    }


def _load_payloads(input_dir: Path, expectation_dir: Path) -> list[tuple[Path, Path | None, Any]]:
    payloads: list[tuple[Path, Path | None, Any]] = []
    for input_path in sorted(input_dir.glob("*.json")):
        payload = GenerateTestCasesInput.model_validate(_load_json(input_path))
        expectation_path = expectation_dir / input_path.name
        payloads.append((input_path, expectation_path if expectation_path.exists() else None, payload))
    return payloads


def _build_overall_summary(results: list[dict[str, Any]], strict: bool) -> dict[str, Any]:
    if not results:
        return {
            "benchmark_count": 0,
            "all_expectations_met": True,
            "strict_mode": strict,
        }

    return {
        "benchmark_count": len(results),
        "all_expectations_met": all(result["expectation_result"]["all_met"] for result in results),
        "strict_mode": strict,
        "execution_modes": sorted({result["execution_mode"] for result in results}),
        "average_review_score": round(mean(result["review"].get("score", 0) for result in results), 2),
        "average_traceability_coverage_ratio": round(
            mean(float(result["coverage_metrics"].get("traceability_coverage_ratio", 0.0)) for result in results),
            2,
        ),
        "average_scenario_coverage_ratio": round(
            mean(float(result["coverage_metrics"].get("scenario_coverage_ratio", 0.0)) for result in results),
            2,
        ),
        "average_structured_case_ratio": round(
            mean(float(result["structural_metrics"].get("with_two_or_more_steps_ratio", 0.0)) for result in results),
            2,
        ),
    }


def _print_result(result: dict[str, Any]) -> None:
    review = result["review"]
    coverage = result["coverage_metrics"]
    structural = result["structural_metrics"]
    expectation_result = result["expectation_result"]
    status = "PASS" if expectation_result["all_met"] else "WARN"
    print(
        f"[{status}] {result['name']} | mode={result['execution_mode']} | "
        f"score={review.get('score', 0)}/{review.get('threshold', 0)} | "
        f"cases={structural['total_test_cases']} | "
        f"traceability={coverage.get('traceability_coverage_ratio', 0.0):.2f} | "
        f"scenario_coverage={coverage.get('scenario_coverage_ratio', 0.0):.2f} | "
        f"structured={structural['with_two_or_more_steps_ratio']:.2f}"
    )
    if result.get("description"):
        print(f"  {result['description']}")
    if result["scenario_distribution"]:
        pretty_distribution = ", ".join(f"{scenario}={count}" for scenario, count in result["scenario_distribution"].items())
        print(f"  scenario tags: {pretty_distribution}")
    unmet_checks = [check for check in expectation_result["checks"] if not check["met"]]
    if unmet_checks:
        for check in unmet_checks:
            print(f"  unmet: {check['name']} expected={check['expected']} actual={check['actual']}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate benchmark generation quality across predefined fixtures.")
    parser.add_argument(
        "--input-dir",
        default=str(REPO_ROOT / "scripts" / "benchmark_inputs"),
        help="Directory containing benchmark input JSON payloads.",
    )
    parser.add_argument(
        "--expectation-dir",
        default=str(REPO_ROOT / "scripts" / "benchmark_expectations"),
        help="Directory containing benchmark expectation JSON files.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to write the evaluation report as JSON.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force offline fallback mode even when model credentials are available.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with a non-zero status code if any expectation is unmet.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    input_dir = Path(args.input_dir).resolve()
    expectation_dir = Path(args.expectation_dir).resolve()
    execution_mode = "offline-fallback" if args.offline or not _has_model_credentials() else "model-backed"

    if not input_dir.exists():
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        return 2

    payloads = _load_payloads(input_dir, expectation_dir)
    if not payloads:
        print(f"No benchmark input files were found in {input_dir}", file=sys.stderr)
        return 2

    if execution_mode == "offline-fallback" and not args.offline:
        print("No GOOGLE_API_KEY or GEMINI_API_KEY detected; using offline fallback mode.")

    results = [_build_benchmark_result(input_path, expectation_path, payload, execution_mode) for input_path, expectation_path, payload in payloads]
    overall = _build_overall_summary(results, strict=args.strict)

    print(f"Evaluated {overall['benchmark_count']} benchmark fixture(s).")
    for result in results:
        _print_result(result)
    print(
        "Overall | "
        f"avg_score={overall.get('average_review_score', 0)} | "
        f"avg_traceability={overall.get('average_traceability_coverage_ratio', 0.0):.2f} | "
        f"avg_scenario_coverage={overall.get('average_scenario_coverage_ratio', 0.0):.2f} | "
        f"avg_structured={overall.get('average_structured_case_ratio', 0.0):.2f}"
    )

    report = {
        "overall": overall,
        "benchmarks": results,
    }
    if args.output_json:
        output_path = Path(args.output_json).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote JSON report to {output_path}")

    if args.strict and not overall["all_expectations_met"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
