#!/usr/bin/env python3
"""Matched four-arm evaluation. Offline mode checks inputs, never claims quality.

Live mode calls the existing agents on synthetic fixtures only. No Firestore
records or rollout flags are changed. Independent reviewers fill the rubric;
model self-assessment is not substituted for unsupported-assumption counts.
"""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
from pathlib import Path
import sys
import time
from threading import Lock
from statistics import mean
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import DEFAULT_MODEL_NAME, get_settings
from app.contracts.guidance import MemoryGuidance
from app.models import AutomationInput, GenerateTestCasesInput, TestCase, WorkflowSettings
from app.services.guidance_service import STAGES, build_manifest, guidance_scope, public_manifest

ARMS = {"baseline": (False, False), "skills_only": (True, False), "memory_only": (False, True), "combined": (True, True)}
FIXTURES = {
    "auth": [
        {"id": "REQ-001", "text": "The system shall allow users to sign in using email and password."},
        {"id": "REQ-002", "text": "The system shall lock an account after 5 failed login attempts within 10 minutes."},
        {"id": "REQ-003", "text": "The system shall show a no-results message when no search matches are found."},
    ],
    "expenses": [
        {"id": "REQ-001", "text": "The system shall allow an employee to save an expense as a draft without submitting it."},
        {"id": "REQ-002", "text": "The system shall require a receipt for expenses above 100 EUR."},
        {"id": "REQ-003", "text": "The system shall allow a Supervisor to approve submitted expenses and deny employees that action."},
    ],
}
LESSONS = {
    "requirements": "Keep each stated business rule atomic and preserve its source terminology. Flag ambiguity instead of inventing details.",
    "use_cases": "Include negative, boundary and authorization cases where the source defines the relevant behavior. Mark unresolved outcomes explicitly.",
    "test_cases": "Reviewers need concrete preconditions and observable expected results. Do not invent missing thresholds or business facts.",
    "automation": "Reviewers require evidenced selectors and assertions. Leave a case manual when the source provides no reliable selector evidence.",
}


def manifest_for(stage, arm, model):
    skills, memory = ARMS[arm]
    entries = [
        MemoryGuidance(
            id=f"fixture-{stage}",
            revision=1,
            scope="project",
            text=LESSONS[stage],
            stages=(stage,),
            source="Synthetic approved reviewer lesson",
            reason="project_stage",
        )
    ]
    with patch.dict(
        os.environ, {"ADK_SKILLS_ENABLED": str(skills).lower(), "ADK_MEMORY_ENABLED": str(memory).lower(), "ADK_GUIDANCE_STAGES": ",".join(STAGES)}
    ):
        return build_manifest(stage, model, project_id="evaluation-fixture", memories=entries)


def payload_for(rows):
    return GenerateTestCasesInput(
        requirements=rows,
        template={"name": "default", "format": "table", "fields": ["id", "title", "steps", "expected_result"]},
        workflow_settings=WorkflowSettings(max_iterations=1, retry_attempts=0, timeout_seconds=90),
    )


def run_stage(stage, rows):
    from app.agents.requirements_agent import extract_requirements
    from app.agents.use_case_agent import generate_use_cases
    from app.agents.test_case_agent import generate_test_cases
    from app.agents.automation_agent import generate_playwright_pom

    if stage == "requirements":
        return extract_requirements(
            "\n".join(f"{r['id']}: {r['text']}" for r in rows), workflow_settings=WorkflowSettings(max_iterations=1, retry_attempts=0, timeout_seconds=90)
        )
    if stage == "use_cases":
        return generate_use_cases(payload_for(rows))
    if stage == "test_cases":
        return generate_test_cases(payload_for(rows))
    cases = [
        TestCase(
            id=f"TC-{i}",
            title=row["text"],
            linked_requirement_ids=[row["id"]],
            steps=[{"step": 1, "action": "Exercise the behavior specified by the linked requirement; UI selectors are not supplied.", "expected": row["text"]}],
        )
        for i, row in enumerate(rows, 1)
    ]
    return generate_playwright_pom(AutomationInput(test_cases=cases))


def serialize(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    return value


@contextmanager
def collect_usage():
    from google.genai.models import Models, AsyncModels

    stats = {"calls": 0, "failed_calls": 0, "input_tokens": 0, "output_tokens": 0, "missing_usage": 0}
    lock = Lock()
    original_sync, original_async = Models.generate_content, AsyncModels.generate_content

    def record(response):
        with lock:
            usage = response.usage_metadata
            if usage is None:
                stats["missing_usage"] += 1
            else:
                stats["input_tokens"] += usage.prompt_token_count or 0
                stats["output_tokens"] += (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
            return response

    def sync(client, *args, **kwargs):
        with lock:
            stats["calls"] += 1
        try:
            return record(original_sync(client, *args, **kwargs))
        except Exception:
            with lock:
                stats["failed_calls"] += 1
            raise

    async def asynchronous(client, *args, **kwargs):
        with lock:
            stats["calls"] += 1
        try:
            return record(await original_async(client, *args, **kwargs))
        except Exception:
            with lock:
                stats["failed_calls"] += 1
            raise

    with patch.object(Models, "generate_content", sync), patch.object(AsyncModels, "generate_content", asynchronous):
        yield stats


def validate_review(review):
    if review is None:
        return {"coverage": None, "unsupported_assumptions": None, "repeated_reviewer_corrections": None, "reviewer": None}
    if not review.get("reviewer") or not 0 <= review["coverage"] <= 1:
        raise ValueError("Independent reviewer and coverage ratio are required")
    for field in ("unsupported_assumptions", "repeated_reviewer_corrections"):
        if not isinstance(review[field], int) or review[field] < 0:
            raise ValueError(f"{field} must be a non-negative adjudicated count")
    return review


def contains_fallback(value):
    """A successful HTTP call can still yield a deterministic workflow fallback."""
    if isinstance(value, dict):
        if value.get("used_fallback") or value.get("fallback_shards", 0) or value.get("fallback_shard_count", 0):
            return True
        if value.get("status") in {"fallback", "failed", "timeout"}:
            return True
        return any(contains_fallback(item) for item in value.values())
    return isinstance(value, list) and any(contains_fallback(item) for item in value)


def validate_artifact(stage, output, rows):
    """Check delivered contracts; never import or execute model output."""
    if stage == "test_cases":
        cases = output.get("test_cases", [])
        expected_count = output.get("generation_evidence", {}).get("final_test_case_count")
        errors = []
        if not cases:
            errors.append("no_delivered_test_cases")
        if expected_count is not None and expected_count != len(cases):
            errors.append("delivered_case_count_differs_from_generation_evidence")
        covered = {rid for case in cases for rid in case.get("linked_requirement_ids", [])}
        if {row["id"] for row in rows} - covered:
            errors.append("missing_delivered_requirement_coverage")
        return errors
    if stage != "automation":
        return []
    code = output.get("notes", "")
    sections = re.split(r"(?m)^#\s*=== FILE: [^\n]+===\s*$", code)
    errors, tests = [], []
    for index, section in enumerate(sections):
        if not section.strip():
            continue
        try:
            tree = ast.parse(section)
            compile(tree, f"generated-section-{index}", "exec")
            tests.extend(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
        except SyntaxError:
            errors.append(f"invalid_python_section_{index}")
    if len(tests) < len(rows):
        errors.append("missing_test_functions")
    return errors


def evaluate(*, live=False, repeats=1, model=DEFAULT_MODEL_NAME, stages=STAGES, reviews=None, input_rate=None, output_rate=None, on_result=None):
    results = []
    for repeat in range(repeats):
        # Rotate ordering to avoid assigning the same arm a systematic warm-up advantage.
        arms = list(ARMS)
        arms = arms[repeat % 4 :] + arms[: repeat % 4]
        for fixture, rows in FIXTURES.items():
            for stage in stages:
                for arm in arms:
                    manifest = manifest_for(stage, arm, model)
                    key = f"{fixture}/{stage}/{arm}/{repeat + 1}"
                    result = {
                        "id": key,
                        "fixture": fixture,
                        "stage": stage,
                        "arm": arm,
                        "repeat": repeat + 1,
                        "input_hash": sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                        "guidance": public_manifest(manifest),
                        "status": "contract_only",
                        "latency_seconds": None,
                        "usage": None,
                        "estimated_cost_usd": None,
                        "review": validate_review((reviews or {}).get(key)),
                    }
                    if live:
                        start = time.monotonic()
                        with guidance_scope(manifest), collect_usage() as usage:
                            try:
                                result["output"] = serialize(run_stage(stage, rows))
                                result["output_hash"] = sha256(json.dumps(result["output"], sort_keys=True).encode()).hexdigest()
                                result["status"] = "generated"
                                result["validation_errors"] = validate_artifact(stage, result["output"], rows)
                                if result["validation_errors"]:
                                    result["status"] = "invalid_artifact"
                            except Exception as exc:
                                result.update(status="failed", error_type=type(exc).__name__)
                        result.update(latency_seconds=round(time.monotonic() - start, 3), usage=usage)
                        if usage["calls"] == 0 or usage["failed_calls"] or contains_fallback(result.get("output")):
                            result["status"] = "fallback_or_failure"
                        if input_rate is not None and output_rate is not None and not usage["missing_usage"]:
                            result["estimated_cost_usd"] = round((usage["input_tokens"] * input_rate + usage["output_tokens"] * output_rate) / 1_000_000, 6)
                    results.append(result)
                    if on_result:
                        on_result(results)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "model_backed" if live else "contracts_only",
        "model": model,
        "repeats": repeats,
        "pricing_usd_per_million": {"input": input_rate, "output_including_thoughts": output_rate},
        "results": results,
        "enablement": "disabled_pending_independent_review_and_paired_improvement",
        "limitations": [
            "Offline mode proves input contracts only, not model quality.",
            "Live output requires independent rubric review; missing measurements are null, never zero.",
            "Review latency and cost tradeoffs before enabling any stage; this script never changes flags.",
        ],
    }


def adjudicate(report, reviews):
    """Attach human judgments only to the exact recorded output they reviewed."""
    for result in report["results"]:
        review = reviews.get(result["id"])
        if review:
            if not result.get("output_hash") or review.get("output_hash") != result["output_hash"]:
                raise ValueError("Review output hash does not match the recorded generation")
            result["review"] = validate_review(review)
    summaries = []
    for stage in STAGES:
        for arm in ARMS:
            samples = [r for r in report["results"] if r["stage"] == stage and r["arm"] == arm]
            if not samples:
                continue

            def average(field, reviewed=False):
                values = [(r["review"] if reviewed else r).get(field) for r in samples]
                return round(mean(values), 4) if all(v is not None for v in values) else None

            summaries.append(
                {
                    "stage": stage,
                    "arm": arm,
                    "sample_count": len(samples),
                    "coverage": average("coverage", True),
                    "unsupported_assumptions": average("unsupported_assumptions", True),
                    "repeated_reviewer_corrections": average("repeated_reviewer_corrections", True),
                    "latency_seconds": average("latency_seconds"),
                    "estimated_cost_usd": average("estimated_cost_usd"),
                    "all_generated": all(r["status"] == "generated" for r in samples),
                }
            )
    report["comparison"] = summaries
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call the configured Gemini model on synthetic fixtures")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--stage", choices=STAGES, action="append")
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--review-report", type=Path, help="Attach rubric judgments to an existing report without generating again")
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("repeats must be between 1 and 10")
    reviews = json.loads(args.reviews.read_text()) if args.reviews else {}
    model = os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME)
    if args.review_report:
        if args.live or not args.reviews:
            parser.error("--review-report requires --reviews and cannot be combined with --live")
        report = adjudicate(json.loads(args.review_report.read_text()), reviews)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print("Adjudicated existing output; no model calls or flag changes.")
        return 0
    if args.reviews:
        parser.error("Use --review-report to attach judgments to exact prior outputs")
    if args.live:
        from google import genai

        settings = get_settings()
        try:
            with genai.Client(api_key=settings.gemini_api_key) as client:
                client.models.get(model=model)
        except Exception as exc:
            report = {
                "mode": "blocked",
                "model": model,
                "provider_status": getattr(exc, "code", None),
                "reason": "SERVICE_DISABLED" if "SERVICE_DISABLED" in str(exc) else type(exc).__name__,
                "results": [],
                "enablement": "disabled",
            }
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report))
            return 2

    def checkpoint(results):
        args.output.write_text(json.dumps({"mode": "in_progress", "model": model, "results": results, "enablement": "disabled"}, indent=2) + "\n")
        latest = results[-1]
        print(f"{latest['id']}: {latest['status']} ({latest['latency_seconds']}s)", flush=True)

    report = evaluate(
        live=args.live,
        repeats=args.repeats,
        model=model,
        stages=args.stage or STAGES,
        reviews=reviews,
        input_rate=args.input_usd_per_million,
        output_rate=args.output_usd_per_million,
        on_result=checkpoint if args.live else None,
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['mode']}: {len(report['results'])} matched samples. Enablement remains disabled. Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
