#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

requirements_agent = importlib.import_module("app.agents.requirements_agent")
adk_client = importlib.import_module("app.adk_client")

DEFAULT_REQUIREMENT_THRESHOLD = adk_client.DEFAULT_REQUIREMENT_THRESHOLD
_build_workflow_response = requirements_agent._build_workflow_response
_compute_requirement_coverage_metrics = requirements_agent._compute_requirement_coverage_metrics
_convert_to_requirements = requirements_agent._convert_to_requirements
_finalize_requirements = requirements_agent._finalize_requirements
_heuristic_extract = requirements_agent._heuristic_extract
_heuristic_requirement_review = adk_client._heuristic_requirement_review
_make_history_entry = adk_client._make_history_entry
extract_requirements = requirements_agent.extract_requirements
refine_requirements = requirements_agent.refine_requirements


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _has_model_credentials() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def _requirements_to_dicts(requirements: list[Any]) -> list[dict[str, str]]:
    serialized: list[dict[str, str]] = []
    for requirement in requirements:
        if hasattr(requirement, "model_dump"):
            payload = requirement.model_dump()
        elif isinstance(requirement, dict):
            payload = requirement
        else:
            payload = {
                "id": getattr(requirement, "id", ""),
                "text": getattr(requirement, "text", ""),
            }
        serialized.append(
            {
                "id": str(payload.get("id") or "").strip(),
                "text": str(payload.get("text") or "").strip(),
            }
        )
    return serialized


def _offline_extract_requirements(document_text: str, document_count: int) -> dict[str, Any]:
    requirements = _finalize_requirements(_heuristic_extract(document_text))
    serialized = _requirements_to_dicts(requirements)
    review = _heuristic_requirement_review(serialized, DEFAULT_REQUIREMENT_THRESHOLD, document_count)
    workflow = {
        "approved": review["approved"],
        "review": review,
        "iteration_history": [
            _make_history_entry(
                iteration=1,
                actor="OfflineHeuristicReview",
                review=review,
                requirements=serialized,
            )
        ],
        "coverage_metrics": _compute_requirement_coverage_metrics(requirements, document_count),
    }
    return _build_workflow_response(requirements, workflow, document_count=document_count)


def _offline_refine_requirements(existing_requirements: list[dict[str, Any]], feedback: str) -> dict[str, Any]:
    requirements = _convert_to_requirements(existing_requirements)
    serialized = _requirements_to_dicts(requirements)
    review = _heuristic_requirement_review(serialized, DEFAULT_REQUIREMENT_THRESHOLD, 1)
    review["approved"] = False
    review["score"] = min(review["score"], DEFAULT_REQUIREMENT_THRESHOLD - 5)
    review["summary"] = "Offline mode preserved the provided requirements; model-backed refinement was skipped."
    review["blocking_issues"] = list(review.get("blocking_issues") or []) + [
        "Offline fallback does not execute the feedback-aware requirement refiner."
    ]
    review["suggestions"] = list(review.get("suggestions") or []) + [
        f"Run in model-backed mode to evaluate refinement feedback like: {feedback[:80].strip() or 'No feedback provided.'}"
    ]
    workflow = {
        "approved": False,
        "review": review,
        "iteration_history": [
            _make_history_entry(
                iteration=1,
                actor="OfflineRefinementFallback",
                review=review,
                requirements=serialized,
            )
        ],
        "coverage_metrics": _compute_requirement_coverage_metrics(requirements, 1),
    }
    return _build_workflow_response(requirements, workflow, document_count=1)


def _compute_structural_metrics(requirements: list[Any]) -> dict[str, Any]:
    serialized = _requirements_to_dicts(requirements)
    total = len(serialized)
    expected_ids = [f"REQ-{index:03d}" for index in range(1, total + 1)]
    ids = [item["id"] for item in serialized]
    sequential_id_matches = sum(1 for actual, expected in zip(ids, expected_ids) if actual == expected)
    short_requirements = [
        item["id"]
        for item in serialized
        if len(item["text"].split()) < 6
    ]
    empty_requirements = [item["id"] or f"REQ-{index + 1:03d}" for index, item in enumerate(serialized) if not item["text"]]

    return {
        "total_requirements": total,
        "sequential_ids": ids == expected_ids,
        "sequential_id_ratio": round(sequential_id_matches / total, 2) if total else 1.0,
        "short_requirements": short_requirements,
        "empty_requirements": empty_requirements,
    }


def _evaluate_concepts(requirements: list[Any], concepts: list[dict[str, Any]] | None) -> dict[str, Any]:
    serialized = _requirements_to_dicts(requirements)
    requirement_texts = [(item["id"], item["text"].lower()) for item in serialized]
    results: list[dict[str, Any]] = []

    for concept in concepts or []:
        label = str(concept.get("label") or "Unnamed concept").strip()
        keywords = [str(keyword).strip().lower() for keyword in concept.get("keywords") or [] if str(keyword).strip()]
        match_mode = str(concept.get("match") or "same_requirement").strip().lower()
        matched_requirement_id = None
        met = False

        if not keywords:
            continue

        if match_mode == "across_set":
            corpus = " ".join(text for _, text in requirement_texts)
            met = all(keyword in corpus for keyword in keywords)
        else:
            for requirement_id, text in requirement_texts:
                if all(keyword in text for keyword in keywords):
                    matched_requirement_id = requirement_id
                    met = True
                    break

        results.append(
            {
                "label": label,
                "keywords": keywords,
                "match": match_mode,
                "matched_requirement_id": matched_requirement_id,
                "met": met,
            }
        )

    met_count = sum(1 for result in results if result["met"])
    return {
        "results": results,
        "met_count": met_count,
        "total_count": len(results),
        "coverage_ratio": round(met_count / len(results), 2) if results else 1.0,
    }


def _find_excluded_keyword_hits(requirements: list[Any], excluded_keywords: list[str] | None) -> list[str]:
    serialized = _requirements_to_dicts(requirements)
    hits: list[str] = []

    for item in serialized:
        requirement_text = item["text"].lower()
        for keyword in excluded_keywords or []:
            normalized_keyword = str(keyword).strip().lower()
            if normalized_keyword and normalized_keyword in requirement_text:
                hits.append(f"{item['id']}: {normalized_keyword}")

    return hits


def _evaluate_expectations(
    expectation: dict[str, Any] | None,
    review: dict[str, Any],
    coverage_metrics: dict[str, Any],
    structural_metrics: dict[str, Any],
    concept_metrics: dict[str, Any],
    excluded_keyword_hits: list[str],
) -> dict[str, Any]:
    if not expectation:
        return {"checks": [], "all_met": True}

    checks: list[dict[str, Any]] = []

    def add_check(name: str, expected: Any, actual: Any, met: bool) -> None:
        checks.append({"name": name, "expected": expected, "actual": actual, "met": bool(met)})

    if "minimum_requirements" in expectation:
        add_check(
            "minimum_requirements",
            expectation["minimum_requirements"],
            structural_metrics["total_requirements"],
            structural_metrics["total_requirements"] >= int(expectation["minimum_requirements"]),
        )

    if "maximum_duplicate_requirements" in expectation:
        add_check(
            "maximum_duplicate_requirements",
            expectation["maximum_duplicate_requirements"],
            coverage_metrics.get("duplicate_requirements", 0),
            int(coverage_metrics.get("duplicate_requirements", 0)) <= int(expectation["maximum_duplicate_requirements"]),
        )

    if "minimum_shall_format_ratio" in expectation:
        add_check(
            "minimum_shall_format_ratio",
            expectation["minimum_shall_format_ratio"],
            coverage_metrics.get("shall_format_ratio", 0.0),
            float(coverage_metrics.get("shall_format_ratio", 0.0)) >= float(expectation["minimum_shall_format_ratio"]),
        )

    if "minimum_review_score" in expectation:
        add_check(
            "minimum_review_score",
            expectation["minimum_review_score"],
            review.get("score", 0),
            int(review.get("score", 0)) >= int(expectation["minimum_review_score"]),
        )

    if "minimum_average_word_count" in expectation:
        add_check(
            "minimum_average_word_count",
            expectation["minimum_average_word_count"],
            coverage_metrics.get("average_word_count", 0.0),
            float(coverage_metrics.get("average_word_count", 0.0)) >= float(expectation["minimum_average_word_count"]),
        )

    if expectation.get("require_sequential_ids"):
        add_check(
            "require_sequential_ids",
            True,
            structural_metrics["sequential_ids"],
            bool(structural_metrics["sequential_ids"]),
        )

    if "maximum_short_requirements" in expectation:
        add_check(
            "maximum_short_requirements",
            expectation["maximum_short_requirements"],
            len(structural_metrics["short_requirements"]),
            len(structural_metrics["short_requirements"]) <= int(expectation["maximum_short_requirements"]),
        )

    if "minimum_concept_coverage_ratio" in expectation:
        add_check(
            "minimum_concept_coverage_ratio",
            expectation["minimum_concept_coverage_ratio"],
            concept_metrics["coverage_ratio"],
            float(concept_metrics["coverage_ratio"]) >= float(expectation["minimum_concept_coverage_ratio"]),
        )

    for concept_result in concept_metrics["results"]:
        add_check(
            f"concept_present:{concept_result['label']}",
            True,
            concept_result["met"],
            concept_result["met"],
        )

    if expectation.get("must_exclude_keywords"):
        add_check(
            "must_exclude_keywords",
            [],
            excluded_keyword_hits,
            not excluded_keyword_hits,
        )

    return {
        "checks": checks,
        "all_met": all(check["met"] for check in checks),
    }


def _run_requirement_fixture(fixture: dict[str, Any], execution_mode: str) -> dict[str, Any]:
    mode = str(fixture.get("mode") or "extract").strip().lower()

    if mode == "refine":
        existing_requirements = list(fixture.get("existing_requirements") or [])
        feedback = str(fixture.get("feedback") or "").strip()
        if execution_mode == "model-backed":
            return refine_requirements(existing_requirements, feedback)
        return _offline_refine_requirements(existing_requirements, feedback)

    document_text = str(fixture.get("document_text") or "")
    document_count = max(1, int(fixture.get("document_count") or 1))
    if execution_mode == "model-backed":
        return extract_requirements(document_text, document_count=document_count)
    return _offline_extract_requirements(document_text, document_count=document_count)


def _build_benchmark_result(
    input_path: Path,
    expectation_path: Path | None,
    fixture: dict[str, Any],
    execution_mode: str,
) -> dict[str, Any]:
    expectation = _load_json(expectation_path) if expectation_path and expectation_path.exists() else None
    workflow_result = _run_requirement_fixture(fixture, execution_mode)
    requirements = workflow_result.get("requirements") or []
    review = dict(workflow_result.get("review") or {})
    coverage_metrics = dict(workflow_result.get("coverage_metrics") or {})
    structural_metrics = _compute_structural_metrics(requirements)
    concept_metrics = _evaluate_concepts(requirements, (expectation or {}).get("must_include_concepts"))
    excluded_keyword_hits = _find_excluded_keyword_hits(requirements, (expectation or {}).get("must_exclude_keywords"))
    expectation_result = _evaluate_expectations(
        expectation,
        review,
        coverage_metrics,
        structural_metrics,
        concept_metrics,
        excluded_keyword_hits,
    )

    return {
        "name": str((expectation or {}).get("name") or fixture.get("name") or input_path.stem),
        "input_file": str(input_path.relative_to(REPO_ROOT)),
        "expectation_file": str(expectation_path.relative_to(REPO_ROOT)) if expectation_path and expectation_path.exists() else None,
        "description": str((expectation or {}).get("description") or fixture.get("description") or ""),
        "execution_mode": execution_mode,
        "mode": str(fixture.get("mode") or "extract"),
        "review": review,
        "coverage_metrics": coverage_metrics,
        "structural_metrics": structural_metrics,
        "concept_metrics": concept_metrics,
        "excluded_keyword_hits": excluded_keyword_hits,
        "expectation_result": expectation_result,
    }


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
        "average_review_score": round(mean(float(result["review"].get("score", 0)) for result in results), 2),
        "average_requirement_count": round(mean(float(result["structural_metrics"].get("total_requirements", 0)) for result in results), 2),
        "average_shall_format_ratio": round(mean(float(result["coverage_metrics"].get("shall_format_ratio", 0.0)) for result in results), 2),
        "average_concept_coverage_ratio": round(mean(float(result["concept_metrics"].get("coverage_ratio", 0.0)) for result in results), 2),
    }


def _print_result(result: dict[str, Any]) -> None:
    review = result["review"]
    coverage = result["coverage_metrics"]
    structural = result["structural_metrics"]
    concepts = result["concept_metrics"]
    expectation_result = result["expectation_result"]
    status = "PASS" if expectation_result["all_met"] else "WARN"

    print(
        f"[{status}] {result['name']} | mode={result['execution_mode']} | "
        f"score={review.get('score', 0)}/{review.get('threshold', 0)} | "
        f"requirements={structural.get('total_requirements', 0)} | "
        f"shall={coverage.get('shall_format_ratio', 0.0):.2f} | "
        f"duplicates={coverage.get('duplicate_requirements', 0)} | "
        f"concepts={concepts.get('met_count', 0)}/{concepts.get('total_count', 0)}"
    )
    if result.get("description"):
        print(f"  {result['description']}")

    missing_concepts = [item["label"] for item in concepts["results"] if not item["met"]]
    if missing_concepts:
        print(f"  missing concepts: {', '.join(missing_concepts)}")
    if result["excluded_keyword_hits"]:
        print(f"  excluded keyword hits: {', '.join(result['excluded_keyword_hits'])}")

    unmet_checks = [check for check in expectation_result["checks"] if not check["met"]]
    for check in unmet_checks:
        print(f"  unmet: {check['name']} expected={check['expected']} actual={check['actual']}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate requirement extraction/refinement quality across benchmark fixtures.")
    parser.add_argument(
        "--input-dir",
        default=str(REPO_ROOT / "scripts" / "benchmark_requirement_inputs"),
        help="Directory containing requirement benchmark input JSON payloads.",
    )
    parser.add_argument(
        "--expectation-dir",
        default=str(REPO_ROOT / "scripts" / "benchmark_requirement_expectations"),
        help="Directory containing requirement benchmark expectation JSON files.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to write the evaluation report as JSON.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force offline heuristic mode even when model credentials are available.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with a non-zero status code if any benchmark expectation is unmet.",
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

    input_paths = sorted(input_dir.glob("*.json"))
    if not input_paths:
        print(f"No benchmark input files were found in {input_dir}", file=sys.stderr)
        return 2

    if execution_mode == "offline-fallback" and not args.offline:
        print("No GOOGLE_API_KEY or GEMINI_API_KEY detected; using offline fallback mode.")

    results = []
    for input_path in input_paths:
        fixture = _load_json(input_path)
        expectation_path = expectation_dir / input_path.name
        results.append(
            _build_benchmark_result(
                input_path,
                expectation_path if expectation_path.exists() else None,
                fixture,
                execution_mode,
            )
        )

    overall = _build_overall_summary(results, strict=args.strict)
    print(f"Evaluated {overall['benchmark_count']} requirement benchmark fixture(s).")
    for result in results:
        _print_result(result)
    print(
        "Overall | "
        f"avg_score={overall.get('average_review_score', 0)} | "
        f"avg_requirements={overall.get('average_requirement_count', 0)} | "
        f"avg_shall={overall.get('average_shall_format_ratio', 0.0):.2f} | "
        f"avg_concepts={overall.get('average_concept_coverage_ratio', 0.0):.2f}"
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