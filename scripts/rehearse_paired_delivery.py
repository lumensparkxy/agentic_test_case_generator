"""Exercise saved delivery with authored controls on an isolated app pin."""

import argparse
import asyncio
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import re
from time import monotonic_ns
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

from prepare_case_adjudication import checked_delivery, digest, exact, json_bytes, markdown_source, read_json, require
from rehearse_paired_inputs import DEMO_PROJECTS, InputRehearsal, generation_inputs


DEFAULT_TEMPLATE = {"name": "Default", "format": "json", "fields": []}
SOURCE_CHANGE = "W11-source-update"
NEGATIVE_CONTROL = "W05-missing-prerequisites"
DRAFT_REASON = "Authored route rehearsal. The suite has no independent acceptance."
TARGET_PATHS = {"room-booking": "/", "payload-editor": "/payload-editor"}


def resolved_context(source, targets):
    context = deepcopy(source["context"])
    target = source.get("healthy_target_fixture")
    if target is not None:
        url = targets[target]
        notes = context["notes"]
        require("{target}" in notes, "Target alias is missing from frozen Context")
        context["notes"] = notes.replace("{{target}}", url).replace("{target}", url)
    return context


def _target(value, path):
    url = urlsplit(value)
    require(
        url.scheme == "http"
        and url.hostname == "127.0.0.1"
        and url.port
        and not url.username
        and not url.password
        and url.path == path
        and not url.query
        and not url.fragment,
        "Target must be the exact expected loopback fixture path",
    )
    return value


def control_cases(controls, saved_ids, plan, context):
    from app.services.browser_samples import declared_browser_samples, materialize_browser_sample

    scenario_ids = {group["requirement_id"]: group["scenarios"][0]["id"] for group in plan}
    samples = declared_browser_samples(context.get("notes") or "")
    cases = []
    for index, control in enumerate(controls, start=1):
        requirement_id = saved_ids[control["requirement_id"]]
        require(requirement_id in scenario_ids, "Control refers to a requirement outside the approved scope")
        case = deepcopy(control["case"])
        require(set(case) <= {"title", "description", "preconditions", "steps", "test_data", "expected_result"}, "Unexpected authored case field")
        require(isinstance(case["title"], str) and case["title"].strip() and case["steps"], "Authored control needs a title and observable steps")
        for number, step in enumerate(case["steps"], start=1):
            require(set(step) <= {"action", "expected", "test_data"} and step["action"].strip() and step["expected"].strip(), "Invalid authored step")
            step["step"] = number
            if materialized := materialize_browser_sample(step["action"], step["expected"], samples):
                step["action"], step["expected"], step["test_data"] = materialized
        cases.append(
            {
                **case,
                "id": f"TC-REHEARSAL-{index:03d}",
                "linked_requirement_ids": [requirement_id],
                "scenario_refs": [scenario_ids[requirement_id]],
                "execution_prerequisites": [
                    row for row in context.get("execution_prerequisites", []) if not row.get("requirement_ids") or requirement_id in row["requirement_ids"]
                ],
            }
        )
    return cases


class Capture:
    def __init__(self, root, identity):
        self.root = root
        self.record = {**identity, "files": {}}
        self.path = root / identity["arm"] / identity["workflow"]
        self.path.mkdir()
        self.started = monotonic_ns()

    def write(self, kind, value, *, raw=False):
        data = value if raw else json_bytes(value)
        path = self.path / f"{kind}.json"
        with path.open("xb") as file:
            file.write(data)
        self.record["files"][kind] = {"path": str(path.relative_to(self.root)), "sha256": sha256(data).hexdigest()}

    def finish(self, status, reason, routes):
        self.write("requests", {"routes": routes, "physical_mock_requests": [], "authored_specialist_controls": True})
        run = {key: value for key, value in self.record.items() if key != "files"}
        self.write("run", {**run, "status": status, "reason": reason, "elapsed_local_ns": monotonic_ns() - self.started})
        self.record.update(status=status, reason=reason)
        (self.path / "record.json").write_bytes(json_bytes(self.record))


def _budget_mock():
    from google.adk.models import LlmRequest

    from app.agents.adk_runtime import bounded_adk_model
    from app.config import DEFAULT_MODEL_NAME

    async def invoke():
        model = bounded_adk_model(DEFAULT_MODEL_NAME)
        return [response async for response in model.generate_content_async(LlmRequest())]

    require(len(asyncio.run(invoke())) == 1, "Expected one authored response at the budget seam")


async def _mock_generate(_model, _request, stream=False):
    del stream
    yield object()


def _scenario_output(payload, **_):
    analysis = [{"requirement_id": row.id, "requirement_text": row.text} for row in payload.requirements]
    return {
        "requirement_analysis": analysis,
        "coverage_plan": [
            {
                **row,
                "scenarios": [
                    {
                        "id": f"{row['requirement_id']}-SCN-01",
                        "requirement_id": row["requirement_id"],
                        "title": f"Review {row['requirement_id']}",
                        "objective": row["requirement_text"],
                    }
                ],
            }
            for row in analysis
        ],
        "approved": True,
        "review": {"approved": True},
        "workflow_diagnostics": {"status": "completed", "used_fallback": False},
    }


def _worker_result(session, response, request_id, stage, expected_state, capture):
    from app.services.approval_scenario_worker import _handle_due
    from app.services.requirement_import_service import repository

    run = response["continuation"]
    require(run["state"] == "queued", "The approval did not queue its stage")
    require(_handle_due(repository().client, session.prefix.rsplit("/", 1)[1], run["run_id"]), "The worker did not claim the exact stage")
    status = session.call("GET", f"{session.prefix}/approval-continuations", params={"request_id": request_id, "stage": stage})
    capture.write(f"{stage}-status", status)
    require(status["receipt"]["run_id"] == run["run_id"], "Worker result does not match its approval receipt")
    completed = next(item for item in status["runs"] if item["run_id"] == run["run_id"])
    require(completed["state"] == expected_state, f"{stage} ended as {completed['state']} ({completed.get('reason')})")
    return completed["result_snapshot_id"]


def _prepare_plan(session, source, context, rows, capture):
    from app.models import QaProjectDetail
    from app.services import use_case_generation_service
    from app.services.requirement_scope import approved_generation_requirements

    candidate = session.arm == "candidate"
    workflow = capture.record["workflow"]
    document = source.get("source_document", markdown_source(rows))
    project, mapping = session.apply_source(session.read(), rows, document)
    deferred = {"R2"} if workflow == NEGATIVE_CONTROL else set()
    for frozen_id in deferred:
        project = session.defer_ambiguous_scope(project, mapping[frozen_id]["id"])
    if candidate:
        project = session.context(project, rows, context)

    key = uuid4().hex
    saved = project["current_snapshots"]["requirements"]["payload"]["requirements"]
    approval = {
        "base_project_revision": project["current_revision"],
        "idempotency_key": key,
        "reviews": [{"requirement_uid": row["requirement_uid"], "review_status": "Approved"} for row in saved if row.get("clarification_status") != "deferred"],
    }

    def generate(payload, **kwargs):
        if candidate:
            _budget_mock()
        return _scenario_output(payload, **kwargs)

    with patch.object(use_case_generation_service, "generate_use_cases", side_effect=generate) as specialist:
        if candidate:
            response = session.call("POST", f"{session.prefix}/requirements/approve-and-prepare-scenarios", request=key, json=approval)
            result_id = _worker_result(session, response, key, "prepare_scenarios", "awaiting_user", capture)
            project = session.read()
            require(project["current_snapshots"]["use_cases"]["snapshot_id"] == result_id, "Worker published a different scenario plan")
        else:
            project = session.call("PATCH", f"{session.prefix}/requirements/reviews", request=key, json=approval)
            project = session.context(project, rows, context)
            project = session.call("POST", f"{session.prefix}/use-cases/generate", json={"base_project_revision": project["current_revision"]})
        specialist.assert_called_once()

    plan = project["current_snapshots"]["use_cases"]
    scoped = approved_generation_requirements(QaProjectDetail.model_validate(project))
    require([row.text for row in scoped] == [row["text"] for row in rows if row["id"] not in deferred], "Saved approved scope differs from frozen source")
    require(not project["stage_state"]["use_cases"]["approved"], "The saved plan did not await explicit approval")
    require([group["requirement_id"] for group in plan["payload"]["coverage_plan"]] == [row.id for row in scoped], "Scenario plan omitted approved scope")
    return project, plan, scoped, {key: item["id"] for key, item in mapping.items()}


def _generate_suite(session, project, plan, scoped, cases, source, capture):
    from app.contracts.grounding import EnrichInput
    from app.routers import testcases

    candidate = session.arm == "candidate"
    template = source.get("template", DEFAULT_TEMPLATE)
    key = uuid4().hex
    decision = {
        "snapshot_id": plan["snapshot_id"],
        "base_project_revision": project["current_revision"],
        "decision": "approve",
        "comment": "Explicit approval for an authored delivery rehearsal.",
    }

    def generate(payload, **kwargs):
        require(exact(payload.template.model_dump(mode="json"), template), "The generator received a different template")
        require([row.id for row in payload.requirements] == [row.id for row in scoped], "The generator received a different approved scope")
        if candidate:
            _budget_mock()
        return {
            "test_cases": cases,
            "coverage_plan": kwargs["source_coverage_plan"],
            "requirement_analysis": kwargs["source_requirement_analysis"],
            "approved": False,
            "review": {"approved": False, "blocking_issues": [DRAFT_REASON]},
            "workflow_diagnostics": {"status": "completed"},
        }

    with patch.object(testcases, "generate_test_cases", side_effect=generate) as specialist:
        if candidate:
            if "template" in source:
                decision["template"] = template
            response = session.call("POST", f"{session.prefix}/use-cases/approve-and-generate-tests", request=key, json=decision)
            result_id = _worker_result(session, response, key, "generate_tests", "completed", capture)
        else:
            session.call("POST", f"{session.prefix}/use-cases/reviews", request=key, json=decision)
            current = session.read()
            context = EnrichInput.model_validate(
                {
                    **current["current_snapshots"]["context"]["payload"],
                    "requirements": scoped,
                    "project_id": current["project_id"],
                    "base_project_revision": current["current_revision"],
                }
            )
            session.call(
                "POST",
                "/testcases/generate",
                json={
                    "project_id": current["project_id"],
                    "base_project_revision": current["current_revision"],
                    "requirements": [row.model_dump(mode="json") for row in scoped],
                    "context": context.model_dump(mode="json"),
                    "template": template,
                },
            )
            result_id = None
        specialist.assert_called_once()
        capture.record["generator_template_sha256"] = digest(specialist.call_args.args[0].template.model_dump(mode="json"))
    return result_id


def _deliver(session, project, capture, refuse_export, expected_count):
    suite = project["current_snapshots"]["test_cases"]
    capture.write("project", project)
    stored = suite["payload"]["test_cases"]
    require(len(stored) == expected_count and not suite["approved"], "Authored route suite was dropped, expanded, or approved")
    envelopes = [
        session.call("GET", f"{session.prefix}/test-case-evidence", params={"snapshot_id": suite["snapshot_id"], "case_id": row["id"]}) for row in stored
    ]
    require(all(item["snapshot_id"] == suite["snapshot_id"] and not item["historical"] for item in envelopes), "Exact case read is not current")
    hydrated = [item["test_case"] for item in envelopes]
    capture.record.update(suite_snapshot_id=suite["snapshot_id"], case_count=len(hydrated))
    capture.write("case-envelopes", envelopes)
    capture.write("hydrated", hydrated)
    payload = {
        "project_id": project["project_id"],
        "base_project_revision": project["current_revision"],
        "source_snapshot_id": suite["snapshot_id"],
        "test_cases": hydrated,
    }
    if not refuse_export:
        payload.update(draft_override_requested=True, draft_override_reason=DRAFT_REASON)
    request_id = uuid4().hex
    response = session.request("POST", "/export/json", request=request_id, json=payload)
    if not response.is_success:
        capture.write("refusal", {"status": response.status_code, "request_id": request_id, "response": response.json()})
    if refuse_export:
        require(response.status_code == 422 and response.json()["detail"]["code"] == "draft_override_required", "Expected the ordinary draft export refusal")
        require(session.read()["current_revision"] == project["current_revision"], "Refused export changed the project")
        return "partial", "draft_override_required"
    require(response.is_success, f"Draft export failed with HTTP {response.status_code}")
    capture.write("exported", response.content, raw=True)
    report = session.read()["current_snapshots"]["reports"]
    capture.write("report", report)
    require(response.json()["audit_metadata"]["is_draft"] and report["request_id"] == request_id, "Export lost its reasoned draft or request identity")
    checked_delivery(project, hydrated, response.json(), report)
    return "delivered", None


def rehearse(client, workflow, source, controls, targets, capture, *, refuse_export=False):
    from app.routers import requirements

    session = None
    status, reason = "interrupted", "Rehearsal was interrupted"
    try:
        candidate = capture.record["arm"] == "candidate"
        session = InputRehearsal(client, requirements, capture.record["arm"], workflow)
        context = resolved_context(source, targets)
        project, plan, scoped, saved_ids = _prepare_plan(session, source, context, source["requirements"], capture)
        capture.record.update(project_id=project["project_id"], scenario_snapshot_id=plan["snapshot_id"], applied_context_sha256=digest(context))
        capture.write("pending-plan", project)
        cases = control_cases(controls, saved_ids, plan["payload"]["coverage_plan"], project["current_snapshots"]["context"]["payload"])
        result_id = _generate_suite(session, project, plan, scoped, cases, source, capture)
        project = session.read()
        require(not candidate or project["current_snapshots"]["test_cases"]["snapshot_id"] == result_id, "Worker published a different suite")
        status, reason = _deliver(session, project, capture, refuse_export, len(cases))
    except Exception as error:
        status, reason = ("partial" if "project" in capture.record["files"] else "failed"), f"{type(error).__name__}: {error}"
    finally:
        capture.finish(status, reason, session.routes if session is not None else [])
    return status, reason


def _local_environment(arm):
    host = re.fullmatch(r"127\.0\.0\.1:([1-9][0-9]{0,4})", os.environ.get("FIRESTORE_EMULATOR_HOST", ""))
    require(host and int(host[1]) <= 65535 and os.environ.get("FIREBASE_PROJECT_ID") == DEMO_PROJECTS[arm], "Use the matching disposable loopback emulator")
    expected = {
        "PYTHON_DOTENV_DISABLED": "1",
        "AUTH_TOKEN_MODE": "firebase-or-backend-jwt",
        "BILLING_SHADOW_MODE": "true",
        "ADK_MEMORY_ENABLED": "false",
        "ADK_SKILLS_ENABLED": "false",
        "TCG_APPROVAL_SCENARIOS_ENABLED": "true" if arm == "candidate" else "false",
    }
    require(all(os.environ.get(key) == value for key, value in expected.items()), "Use the isolated offline rehearsal settings")
    forbidden = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")
    require(not any(os.environ.get(key) for key in forbidden), "Provider credentials are outside this rehearsal")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", required=True, type=Path)
    parser.add_argument("--arm", required=True, choices=DEMO_PROJECTS)
    parser.add_argument("--app-commit", required=True)
    parser.add_argument("--controls", required=True, type=Path)
    parser.add_argument("--controls-sha256", required=True)
    parser.add_argument("--booking-url", required=True)
    parser.add_argument("--payload-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--refuse-export", action="store_true", help="Run only the W05 negative draft-export control.")
    args = parser.parse_args()
    _local_environment(args.arm)
    manifest, workflows = generation_inputs(args.frozen)
    pin = args.app_commit
    require(re.fullmatch("[0-9a-f]{40}", pin) and (pin == manifest["baseline"]) == (args.arm == "baseline"), "App pin and arm disagree")
    targets = {key: _target(url, TARGET_PATHS[key]) for key, url in (("room-booking", args.booking_url), ("payload-editor", args.payload_url))}
    raw_controls = args.controls.read_bytes()
    require(sha256(raw_controls).hexdigest() == args.controls_sha256, "Changed authored route controls")
    controls = read_json(raw_controls)
    common = workflows.keys() - {SOURCE_CHANGE}
    require(controls.keys() == common and all(isinstance(rows, list) and rows for rows in controls.values()), "Supply controls for each common workflow")
    selected = [NEGATIVE_CONTROL] if args.refuse_export else sorted(common)
    args.output.mkdir(mode=0o700, exist_ok=True)
    (args.output / args.arm).mkdir(mode=0o700)

    import firebase_admin
    from fastapi.testclient import TestClient
    from google.adk.models import Gemini
    from google.auth.credentials import AnonymousCredentials
    from google.genai._api_client import BaseApiClient

    class LocalCredential(firebase_admin.credentials.Base):
        def get_credential(self):
            return AnonymousCredentials()

    firebase_admin.initialize_app(LocalCredential(), {"projectId": DEMO_PROJECTS[args.arm]})
    records = []
    with (
        patch.object(BaseApiClient, "__init__", side_effect=AssertionError("Provider construction is forbidden")) as provider,
        patch.object(Gemini, "generate_content_async", _mock_generate),
    ):
        from app.auth.jwt_auth import create_access_token
        from app.main import app
        from app.models import AuthUser

        for workflow in selected:
            item = workflows[workflow]
            identity = {
                "workflow": workflow,
                "repeat": 1,
                "arm": args.arm,
                "cohort": "rehearsal",
                "commit": pin,
                "input_sha256": item["generation_input_sha256"],
                "authored_controls_sha256": args.controls_sha256,
            }
            capture = Capture(args.output, identity)
            actor = AuthUser(sub=f"paired-delivery-{uuid4().hex}", name="Offline QA control", email="qa@example.test")
            token, _ = create_access_token(actor)
            with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
                status, reason = rehearse(client, workflow, item["generation_input"], controls[workflow], targets, capture, refuse_export=args.refuse_export)
            records.append(capture.record)
            print(f"{status.upper()} {args.arm} {workflow}: {reason or capture.record['case_count']}", flush=True)
        require(provider.call_count == 0, "Provider construction was attempted")
    (args.output / args.arm / "records.json").write_bytes(json_bytes(records))
    expected = ("partial", "draft_override_required") if args.refuse_export else ("delivered", None)
    return 0 if all((row["status"], row["reason"]) == expected for row in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
