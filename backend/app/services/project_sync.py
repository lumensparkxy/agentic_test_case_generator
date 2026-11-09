"""Accept source corrections once, then refresh reviewable drafts in dependency order."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from os import getenv
from uuid import uuid4
import logging
from fastapi import HTTPException
from ..agents.adk_runtime import bounded_model_requests
from ..config import DEFAULT_MODEL_NAME, get_generation_settings, model_scope
from ..contracts.project_sync import ProjectDecision
from ..contracts.requirements import Requirement
from ..contracts.grounding import EnrichInput
from ..models import GenerateTestCasesInput, GenerateTestCasesResponse, TestCaseTemplate, WorkflowSettings
from . import workflow_project_service as projects
from .quality_feedback import digest, _project, _check_revision, _snapshot
from .approval_scenario_worker import approval_scenarios_enabled, approval_test_cases_enabled
from .project_draft_admission import DraftAttemptClaim, claim_draft_attempt, draft_admission_client, draft_refresh_deferred
from .project_inputs import manifest_from_payload, behavioural_version
from .guidance_runtime import prepare_run
from .guidance_service import guidance_scope, public_manifest
from .billing_service import BillingAccessContext, enforce_billing_access, record_billing_consumption
from .audit_service import record_usage_event
from .versioning_service import persist_test_case_versions
from .use_case_generation_service import _validate_output
from ..agents.use_case_agent import generate_use_cases
from ..agents.test_case_agent import generate_test_cases
from ..agents.automation_agent import generate_playwright_pom
from .test_case_quality import placeholder_reason, delivered_coverage
from .scenario_review_state import carry_scenario_reviews

LEASE_MINUTES = 20
_SYNC_SOURCES = ("requirements", "context", "use_cases", "test_cases", "reports")
_DEMO_STAGES = ("use_cases", "test_cases")
_DRAFT_ADMISSION_VERSION = 1


@dataclass(frozen=True)
class _DemoStage:
    client: object
    claim: DraftAttemptClaim
    workflow_settings: WorkflowSettings
    settings_fingerprint: str
    billing_context: BillingAccessContext


def _doc(project_id, change_id):
    return projects._get_project_doc(project_id).collection("project_changes").document(change_id)


def _current_change(project, change_id):
    if not project.project_sync or project.project_sync["id"] != change_id:
        raise HTTPException(409, "This update was superseded. Review the current project update.")
    return project


def _write(project, actor, record, *, snapshots=(), primary_snapshot=None, states=None, updates=None, writes=(), guard=None):
    revision = project.current_revision + 1
    now = projects._utcnow()
    record = {**record, "updated_at": now.isoformat(), "project_revision": revision}
    values = {"current_revision": revision, "updated_at": now, "project_sync": record, **(updates or {})}
    if states is not None:
        values["stage_state"] = states
    pending = [(projects._get_project_doc(project.project_id).collection("snapshots").document(s["snapshot_id"]), s, False) for s in snapshots]
    event = {
        "event_id": str(uuid4()),
        "project_id": project.project_id,
        "event_type": "project.sync",
        "summary": f"Project update: {record['status']}",
        "project_revision": revision,
        "actor_user_id": actor.sub,
        "metadata": {"change_id": record["id"]},
        "occurred_at": now,
    }
    if primary_snapshot:
        event.update(stage=primary_snapshot["stage"], snapshot_id=primary_snapshot["snapshot_id"])
    projects._commit_project_revision(
        project.project_id,
        actor,
        project.current_revision,
        values,
        event,
        snapshot=primary_snapshot,
        pending_writes=[*pending, *writes, (_doc(project.project_id, record["id"]), record, False)],
        transaction_guard=guard,
    )
    return projects.get_project(project.project_id, actor=actor)


def accept_finding(project_id, actor, payload, request_id):
    draft_client = draft_admission_client()
    project = _project(project_id, actor)
    change_id = "change_" + digest([actor.sub, project_id, request_id])[:24]
    existing = projects._document_to_dict(_doc(project_id, change_id).get())
    fingerprint = digest(payload.model_dump(mode="json"))
    if existing:
        if existing["fingerprint"] != fingerprint:
            raise HTTPException(409, "Request ID belongs to a different correction")
        if draft_client is None and not existing.get("draft_admission_version"):
            return resume_sync(project_id, actor, change_id)
        # The correction may have committed between the project and receipt reads.
        if project.current_revision < existing["project_revision"]:
            project = _project(project_id, actor)
        return _current_change(project, change_id)
    _check_revision(project, payload.base_project_revision)
    finding_ref = projects._get_project_doc(project_id).collection("quality_findings").document(payload.finding_id)
    finding = projects._document_to_dict(finding_ref.get())
    if not finding or finding.get("status") not in ("open", "deferred"):
        raise HTTPException(409, "Select an open project finding")
    if finding.get("category") in ("unknown", "application_defect"):
        raise HTTPException(409, "Classify the source correction first. An application defect requires verification, not rewritten expectations.")
    baseline = project.current_snapshots.get("requirements")
    if not baseline:
        raise HTTPException(409, "Import requirements before accepting a correction")
    requirements = deepcopy(baseline.payload.get("requirements", []))
    by_id = {r["id"]: r for r in requirements}
    cases = project.current_snapshots.get("test_cases")
    affected = set(finding.get("requirement_ids") or []) | {
        rid
        for case in (cases.payload.get("test_cases", []) if cases else [])
        if case["id"] in finding.get("test_case_ids", [])
        for rid in case.get("linked_requirement_ids", [])
    }
    if not affected or not affected <= by_id.keys():
        raise HTTPException(422, "Link the finding to current requirements before accepting it")
    if finding["source_snapshot_id"] not in {s.snapshot_id for s in project.current_snapshots.values()} and finding.get("requirement_source_text") != {
        r["id"]: r["text"] for r in requirements if r["id"] in affected
    }:
        raise HTTPException(409, "The finding source changed. Record a finding against the current source.")
    unresolved = {
        r["id"]
        for r in requirements
        if r.get("clarification_status") in ("open", "deferred", "excluded") and r.get("clarification_finding_id") != finding["id"]
    }
    if unresolved & affected:
        raise HTTPException(409, "Resolve other open clarifications for these requirements before refreshing drafts")
    for row in requirements:
        if row["id"] in affected:
            row["clarification_status"] = "resolved"
    corrections = {c.requirement_id: c for c in payload.corrections}
    if len(corrections) != len(payload.corrections) or not corrections.keys() <= affected:
        raise HTTPException(422, "Corrections must uniquely reference the finding's requirements")
    if finding["category"] in ("ambiguous_requirement", "unsupported_assumption") and not corrections:
        raise HTTPException(422, "Resolve the business rule explicitly in the source requirement")
    active = project.project_decisions
    overlapping = {d["id"] for d in active if set(d["requirement_ids"]) & affected}
    if set(payload.supersedes) != overlapping or (overlapping and not payload.resolution.strip()):
        raise HTTPException(409, "Review overlapping decisions and explicitly resolve or consolidate them before replacing them")
    for rid, correction in corrections.items():
        requirement = by_id[rid]
        if requirement["text"] != correction.previous_text:
            raise HTTPException(409, "Requirement text changed. Compare the current source before applying.")
        requirement.update(
            text=correction.text, source_excerpt=correction.text, content_version=requirement.get("content_version", 1) + 1, review_status="Needs Review"
        )
        requirement["sources"] = [
            *requirement.get("sources", []),
            {
                "source_id": change_id,
                "source_version": digest(correction.text),
                "import_id": change_id,
                "label": "Accepted project correction",
                "excerpt": correction.text,
                "excerpt_verified": True,
            },
        ]
    decision = ProjectDecision(
        id=change_id,
        finding_id=payload.finding_id,
        text=payload.decision,
        requirement_ids=tuple(sorted(affected)),
        supersedes=tuple(payload.supersedes),
        accepted_by=actor.sub,
        accepted_at=projects._utcnow().isoformat(),
    )
    decisions = [d for d in active if d["id"] not in overlapping] + [decision.model_dump(mode="json")]
    # Do not silently truncate mandatory facts; fail before accepting more than the document budget.
    if len(decisions) > 100:
        raise HTTPException(422, "Consolidate existing project decisions before adding another")
    states = {s: v.model_dump(mode="json") for s, v in project.stage_state.items()}
    revision = project.current_revision + 1
    identity = {"id": change_id, "revision": 1}
    snapshots = []
    if corrections:
        source, states["requirements"] = _snapshot(
            project, "requirements", {**baseline.payload, "requirements": requirements}, identity, actor, revision, {"change_id": change_id}
        )
        snapshots.append(source)
    context = project.current_snapshots.get("context")
    context_payload = {**(deepcopy(context.payload) if context else {}), "requirements": requirements, "project_decisions": decisions}
    source, states["context"] = _snapshot(project, "context", context_payload, identity, actor, revision, {"change_id": change_id})
    snapshots.append(source)
    stale_reason = "Accepted project correction; drafts are refreshing" if draft_client is None else "Accepted project correction; draft review is required"
    for stage in projects.DOWNSTREAM_STAGES["context"]:
        if stage in states:
            states[stage].update(stale=True, approved=False, stale_reason=stale_reason)
    # Include other requirements sharing a case, so mixed-scope tests are regenerated together.
    changed = True
    while changed:
        before = set(affected)
        for case in cases.payload.get("test_cases", []) if cases else []:
            if set(case.get("linked_requirement_ids", [])) & affected:
                affected.update(case.get("linked_requirement_ids", []))
        changed = before != affected
    if unresolved & affected:
        raise HTTPException(409, "A shared test also depends on an unresolved clarification. Resolve that question before refreshing drafts.")
    inputs = manifest_from_payload(
        {"project_id": project_id, "input_revision": project.input_revision + 1, "stage_state": states, "project_decisions": decisions}
    )
    reports = project.current_snapshots.get("reports")
    automation = bool(reports and reports.payload.get("artifact_kind") == "generated_code")
    record = {
        "id": change_id,
        "behaviour_version": behavioural_version(),
        "fingerprint": fingerprint,
        "finding_id": finding["id"],
        "status": "pending",
        "input_manifest": inputs.model_dump(mode="json"),
        "affected_requirement_ids": sorted(affected),
        "decision": decision.model_dump(mode="json"),
        "resolution": payload.resolution,
        "superseded_decisions": [d for d in active if d["id"] in overlapping],
        "created_at": projects._utcnow().isoformat(),
        "stages": {
            "requirements": "draft" if corrections else "preserved",
            "context": "draft",
            "use_cases": "pending",
            "test_cases": "pending",
            "automation": "pending" if automation else "not_requested",
        },
        "expected_snapshots": {s: v["current_snapshot_id"] for s, v in states.items()},
        "attempts": 0,
    }
    finding = {**finding, "status": "accepted", "change_id": change_id, "decision": payload.decision, "revision": finding.get("revision", 0) + 1}
    if draft_client is None:
        enforce_billing_access(current_user=actor, billing_key="testcases.generate")
    else:
        record["draft_admission_version"] = _DRAFT_ADMISSION_VERSION
        if automation:
            record["stages"]["automation"] = "deferred"
    saved = _write(project, actor, record, snapshots=snapshots, states=states, updates={"project_decisions": decisions}, writes=[(finding_ref, finding, False)])
    if draft_client is None:
        return resume_sync(project_id, actor, change_id)
    _current_change(saved, change_id)
    try:
        return _resume_demo(saved, actor, saved.project_sync, draft_client, request_id)
    except HTTPException, projects.ProjectConflictError:
        # Acceptance already committed. Return the persisted correction and any finished stage.
        return _current_change(_project(project_id, actor), change_id)


def _check_current(project, record):
    if record.get("behaviour_version") != behavioural_version():
        raise HTTPException(409, "Published agent behaviour changed. Start a new project update; saved drafts are preserved.")
    if project.status != "active" or not project.project_sync or project.project_sync["id"] != record["id"]:
        raise HTTPException(409, "This update was superseded. Review the current project update.")
    current = manifest_from_payload(
        {
            "project_id": project.project_id,
            "input_revision": project.input_revision,
            "stage_state": {s: v.model_dump() for s, v in project.stage_state.items()},
            "project_decisions": project.project_decisions,
        }
    )
    if current.model_dump(mode="json") != record["input_manifest"]:
        raise HTTPException(409, "Project sources changed; the old update cannot publish results")
    for stage in ("use_cases", "test_cases", "reports"):
        snapshot = project.current_snapshots.get(stage)
        if (snapshot.snapshot_id if snapshot else None) != record["expected_snapshots"].get(stage):
            raise HTTPException(409, "Project drafts changed; the old update cannot overwrite them")


def _guard(project_id, record):
    def guard(transaction, _snapshot_payload):
        client = projects.get_required_firestore_client(unavailable_message="Project storage unavailable")
        ref = client.collection(projects.QA_PROJECTS_COLLECTION).document(project_id).collection("project_changes").document(record["id"])
        saved = projects._document_to_dict(ref.get(transaction=transaction))
        if not saved or saved.get("draft_admission_version") or saved.get("lease_token") != record.get("lease_token"):
            raise HTTPException(409, "Refresh attempt was superseded")

    return guard


def _source_ids(project):
    return {stage: snapshot.snapshot_id if (snapshot := project.current_snapshots.get(stage)) else None for stage in _SYNC_SOURCES}


def _draft(project, actor, record, stage, payload, manifest, *, counts=None, pending_writes=(), demo=None, consume=None, source_event_id=None):
    _check_current(project, record)
    states = {s: v.model_dump(mode="json") for s, v in project.stage_state.items()}
    source, states[stage] = _snapshot(
        project,
        stage,
        payload,
        {"id": record["id"], "revision": 1},
        actor,
        project.current_revision + 1,
        {"change_id": record["id"], "project_inputs": record["input_manifest"], "guidance": public_manifest(manifest), "provisional": True},
    )
    for downstream in projects.DOWNSTREAM_STAGES[stage]:
        if downstream in states:
            states[downstream].update(stale=True, approved=False, stale_reason="Upstream project draft refreshed; review required")
    label = "automation" if stage == "reports" else stage
    upstream = {"use_cases": "requirements", "test_cases": "use_cases", "reports": "test_cases"}[stage]
    source_ids = (
        _source_ids(project)
        if demo
        else {name: snapshot.snapshot_id for name, snapshot in project.current_snapshots.items() if name in ("requirements", "context", "use_cases")}
    )
    source["metadata"].update(previous_snapshot_id=source.get("source_snapshot_id"), source_snapshot_ids=source_ids)
    source["source_snapshot_id"] = project.current_snapshots[upstream].snapshot_id
    source["operation"] = "project.sync.draft"
    states[stage].update(source_snapshot_id=source["source_snapshot_id"], operation=source["operation"])
    if demo:
        operation_id = demo.claim.operation_id
        source.update(
            snapshot_id=projects._stable_document_id("snapshot", project.project_id, stage, source["operation"], operation_id),
            request_id=demo.claim.request_id,
            idempotency_key=operation_id,
            source_event_id=source_event_id,
        )
        source["metadata"]["settings_fingerprint"] = demo.settings_fingerprint
        states[stage]["current_snapshot_id"] = source["snapshot_id"]
    if stage == "use_cases":
        old = project.current_snapshots.get(stage)
        reviews = carry_scenario_reviews(
            payload, old.payload if old else {}, project.stage_state[stage].metadata if old else {}, old.snapshot_id if old else "", source["snapshot_id"]
        )
        states[stage]["metadata"]["scenario_reviews"] = reviews
    updated = {**record, "stages": {**record["stages"], label: "draft"}, "expected_snapshots": {**record["expected_snapshots"], stage: source["snapshot_id"]}}
    if counts:
        updated["review"] = counts
    if demo is None:
        return _write(project, actor, updated, snapshots=[source], states=states, writes=pending_writes, guard=_guard(project.project_id, record))

    stage_receipt = {
        "snapshot_id": source["snapshot_id"],
        "request_id": source["request_id"],
        "base_revision": project.current_revision,
        "result_revision": source["project_revision"],
        "source_snapshot_ids": source_ids,
        "settings_fingerprint": demo.settings_fingerprint,
    }
    updated["stage_receipts"] = {**record.get("stage_receipts", {}), stage: stage_receipt}
    updated["status"] = "ready_for_review" if stage == "test_cases" else "partial"
    if updated["stages"].get("automation") == "pending":
        updated["stages"]["automation"] = "deferred"
    updated["error"] = None
    change_ref = demo.client.collection(projects.QA_PROJECTS_COLLECTION).document(project.project_id).collection("project_changes").document(record["id"])

    def commit_stage(transaction, snapshot):
        saved = projects._document_to_dict(change_ref.get(transaction=transaction))
        if saved != record:
            raise HTTPException(409, "The saved correction changed before this draft could publish.")
        finish = demo.claim.prepare_publication(transaction, snapshot)
        if consume is not None:
            consume(transaction)
        finish()

    return _write(project, actor, updated, primary_snapshot=source, states=states, writes=pending_writes, guard=commit_stage)


def _generate_stage(project, actor, record, stage, *, demo=None):
    affected = set(record["affected_requirement_ids"])
    reqs = [Requirement.model_validate(r) for r in project.current_snapshots["requirements"].payload["requirements"] if r["id"] in affected]
    if demo and (not reqs or {row.id for row in reqs} != affected or any(row.lifecycle_status != "active" for row in reqs)):
        raise HTTPException(409, "The saved correction no longer matches the affected requirement scope.")
    context = EnrichInput.model_validate({**project.current_snapshots["context"].payload, "requirements": reqs})
    payload = GenerateTestCasesInput(
        requirements=reqs,
        context=context,
        project_id=project.project_id,
        template=TestCaseTemplate(name="Project update", format="json", fields=["id", "title", "steps", "expected_result"]),
        **({"workflow_settings": demo.workflow_settings, "base_project_revision": project.current_revision} if demo else {}),
    )
    request_id = demo.claim.request_id if demo else f"{record['id']}:{stage}:{record['attempts']}"
    manifest = prepare_run(
        stage,
        actor,
        project.project_id,
        request_id,
        requirement_ids=sorted(affected),
        input_fingerprint=digest(record["input_manifest"]),
        **({"memory_bypass": True} if demo else {}),
    )
    with guidance_scope(manifest):
        if stage == "use_cases":
            output = _validate_output(generate_use_cases(payload, actor_user_id=actor.sub, request_id=request_id, operation="project.sync"), reqs).model_dump(
                mode="json"
            )
            old = project.current_snapshots.get("use_cases")
            old_payload = old.payload if old else {}
            merged = {
                **old_payload,
                **output,
                "approved": False,
                "coverage_metrics": {},
                "review": {"approved": False, "summary": "Project drafts require combined review."},
            }
            for field in ("requirement_analysis", "coverage_plan"):
                merged[field] = [r for r in old_payload.get(field, []) if r["requirement_id"] not in affected] + output[field]
            # Scope IDs to their requirement; never collide with preserved scenarios.
            used = {s["id"] for p in merged["coverage_plan"] if p["requirement_id"] not in affected for s in p["scenarios"]}
            for group in merged["coverage_plan"]:
                if group["requirement_id"] in affected:
                    for scenario in group["scenarios"]:
                        if scenario["id"] in used:
                            scenario["id"] = f"{group['requirement_id']}-{uuid4().hex[:8]}"
                        used.add(scenario["id"])
            return _draft(project, actor, record, "use_cases", merged, manifest, demo=demo)
        if stage == "test_cases":
            plans = project.current_snapshots["use_cases"].payload
            scoped_plan = [group for group in plans["coverage_plan"] if group["requirement_id"] in affected]
            payload = GenerateTestCasesInput.model_validate({**payload.model_dump(), "coverage_plan": scoped_plan})
            refs_by_requirement = {plan.requirement_id: {scenario.id for scenario in plan.scenarios} for plan in payload.coverage_plan}
            if demo and (len(scoped_plan) != len(affected) or set(refs_by_requirement) != affected or not all(refs_by_requirement.values())):
                raise HTTPException(409, "The saved scenario draft does not cover the exact affected requirement scope.")
            output = GenerateTestCasesResponse.model_validate(
                generate_test_cases(payload, actor_user_id=actor.sub, request_id=request_id, operation="impact.update.apply")
            )
            refs = set().union(*refs_by_requirement.values())
            if (
                not output.test_cases
                or output.generation_tasks
                or output.workflow_diagnostics.used_fallback
                or output.workflow_diagnostics.status in ("failed", "fallback")
                or any(
                    placeholder_reason(c) or not c.linked_requirement_ids or not set(c.linked_requirement_ids) <= affected or not set(c.scenario_refs) <= refs
                    for c in output.test_cases
                )
                or not refs <= {ref for c in output.test_cases for ref in c.scenario_refs}
                or (
                    demo
                    and any(
                        not case.scenario_refs
                        or not set(case.scenario_refs) <= set().union(*(refs_by_requirement.get(r, ()) for r in case.linked_requirement_ids))
                        for case in output.test_cases
                    )
                )
            ):
                raise HTTPException(502, "Test refresh was incomplete. Previous tests are preserved; retry the failed stage.")
            old = project.current_snapshots.get("test_cases")
            old_payload = old.payload if old else {}
            previous = old_payload.get("test_cases", [])
            replaced = [c for c in previous if set(c.get("linked_requirement_ids", [])) & affected]
            preserved = [c for c in previous if not set(c.get("linked_requirement_ids", [])) & affected]
            generated = []
            for index, case in enumerate(output.test_cases):
                value = case.model_dump(mode="json")
                value.update(id=replaced[index]["id"] if index < len(replaced) else f"TC-{uuid4().hex[:12].upper()}", status="Draft")
                for key in list(value):
                    if key.startswith("artifact_"):
                        value.pop(key)
                generated.append(value)
            pending_writes = []
            event_id = f"sync-{project.project_id}-{record['id']}"
            versioned = persist_test_case_versions(
                current_test_cases=preserved + generated,
                previous_test_cases=previous,
                actor=actor,
                request_id=request_id,
                workflow_run_id=None,
                source_event_id=event_id if demo else None,
                operation="project.sync",
                approved=False,
                reuse_unchanged_versions=True,
                pending_writes=pending_writes,
                **({"changed_case_ids": {case["id"] for case in generated}} if demo else {}),
            )
            generated_ids = {c["id"] for c in generated}
            preserved_by_id = {c["id"]: c for c in preserved}
            combined = []
            for case in versioned:
                value = case.model_dump(mode="json")
                if case.id in preserved_by_id:
                    # Keep canonical untouched content, including absent optional
                    # fields. Legacy cases may acquire version references only.
                    value = {
                        **deepcopy(preserved_by_id[case.id]),
                        **{key: item for key, item in value.items() if key.startswith("artifact_") and item is not None},
                    }
                combined.append(value)
            generated = [c for c in combined if c["id"] in generated_ids]
            value = {
                **old_payload,
                "test_cases": combined,
                "approved": False,
                "requirement_analysis": plans["requirement_analysis"],
                "coverage_plan": plans["coverage_plan"],
                "coverage_metrics": delivered_coverage(combined, plans["coverage_plan"]),
                "generation_tasks": [],
                "substantive_assessment": {"status": "unknown", "reason": "The refreshed suite requires combined assessment and human review."},
                "review": {"approved": False, "score": 0, "summary": "Review project changes before export."},
                # The original evidence remains on the prior immutable snapshot.
                # A scoped refresh cannot inherit its combined-suite approval.
                "iteration_history": [],
                "generation_evidence": output.generation_evidence.model_dump(mode="json"),
                "workflow_settings": output.workflow_settings.model_dump(mode="json"),
                "workflow_diagnostics": output.workflow_diagnostics.model_dump(mode="json"),
            }
            counts = {
                "changed_ids": [c["id"] for c in generated],
                "removed_ids": [c["id"] for c in replaced[len(generated) :]],
                "changed": min(len(replaced), len(generated)),
                "added": max(0, len(generated) - len(replaced)),
                "preserved": len(preserved),
            }
            billing_context = demo.billing_context if demo else enforce_billing_access(current_user=actor, billing_key="testcases.generate")
            event_id = record_usage_event(
                event_type="testcases.generated",
                billing_key="testcases.generate",
                quantity=len(generated),
                unit="test_case",
                actor=actor,
                request_id=request_id,
                workflow_run_id=None,
                status="completed",
                event_id=event_id,
                pending_writes=pending_writes,
                firestore_client=demo.client if demo else projects.get_required_firestore_client(unavailable_message="Project storage unavailable"),
            )

            def consume(transaction=None):
                record_billing_consumption(
                    current_user=actor,
                    billing_context=billing_context,
                    source_event_id=event_id,
                    request_id=request_id,
                    workflow_run_id=None,
                    billing_key="testcases.generate",
                    quantity=len(generated),
                    unit="test_case",
                    **({"transaction": transaction, "firestore_client": demo.client} if demo else {}),
                )

            if demo:
                return _draft(
                    project,
                    actor,
                    record,
                    "test_cases",
                    value,
                    manifest,
                    counts=counts,
                    pending_writes=pending_writes,
                    demo=demo,
                    consume=consume,
                    source_event_id=event_id,
                )
            result = _draft(project, actor, record, "test_cases", value, manifest, counts=counts, pending_writes=pending_writes)
            try:
                consume()
            except Exception:
                logging.warning("Project sync usage was saved; billing consumption recording needs reconciliation")
            return result
        from ..contracts.automation import AutomationInput

        previous = project.current_snapshots.get("reports")
        source = previous.payload.get("automation_input") if previous else None
        if not source:
            raise HTTPException(409, "Automation needs current interface evidence. Existing scripts are preserved; provide evidence before refreshing them.")
        automation_input = AutomationInput.model_validate(
            {**source, "project_id": project.project_id, "test_cases": project.current_snapshots["test_cases"].payload["test_cases"]}
        )
        response = generate_playwright_pom(automation_input)
        if any(c.status != "generated" for c in response.case_diagnostics) or not response.files:
            raise HTTPException(409, "Automation refresh needs current locator and assertion evidence. Existing scripts are preserved.")
        return _draft(
            project,
            actor,
            record,
            "reports",
            {
                "source": "automation_generation",
                "artifact_kind": "generated_code",
                "execution_status": "not_executed",
                **response.model_dump(mode="json"),
                "automation_input": automation_input.model_dump(mode="json"),
            },
            manifest,
        )


def _verify_demo_stage(project, record, stage, request_id, snapshot_id=None):
    result = project.current_snapshots.get(stage)
    current = project.project_sync or {}
    receipt = current.get("stage_receipts", {}).get(stage) or {}
    if (
        current.get("id") != record["id"]
        or not result
        or result.snapshot_id != receipt.get("snapshot_id")
        or (snapshot_id is not None and result.snapshot_id != snapshot_id)
        or result.request_id != request_id
        or receipt.get("request_id") != request_id
        or receipt.get("base_revision") != record["project_revision"]
        or receipt.get("result_revision") != result.project_revision
    ):
        raise HTTPException(409, "The saved draft is no longer the current correction checkpoint. Reload the project.")
    return project


def _run_demo_stage(project, actor, record, stage, client, request_id):
    is_scenario = stage == "use_cases"
    enabled = approval_scenarios_enabled if is_scenario else approval_test_cases_enabled
    if not enabled():
        raise HTTPException(409, "Project draft refresh requires the bounded demo provider settings.")
    settings = (
        WorkflowSettings(max_iterations=1, timeout_seconds=90, retry_attempts=0)
        if is_scenario
        else WorkflowSettings(max_iterations=2, timeout_seconds=120, retry_attempts=0)
    )
    max_requests, deadline = (7, 240) if is_scenario else (24, 300)
    model = getenv("MODEL_NAME", DEFAULT_MODEL_NAME)
    fingerprint = digest(
        [
            "project-sync-v1",
            stage,
            model,
            record["behaviour_version"],
            settings.model_dump(mode="json"),
            get_generation_settings().model_dump(mode="json"),
            max_requests,
            deadline,
        ]
    )
    claim = claim_draft_attempt(
        client,
        project_id=project.project_id,
        actor_id=actor.sub,
        request_id=request_id,
        operation=f"project.sync.{stage}",
        source_snapshot_ids=_source_ids(project),
        base_revision=project.current_revision,
        max_requests=max_requests,
        deadline_seconds=deadline,
        change_id=record["id"],
        settings_fingerprint=fingerprint,
    )
    if claim.result_snapshot_id:
        return _verify_demo_stage(_project(project.project_id, actor), record, stage, claim.request_id, claim.result_snapshot_id)
    try:
        billing = enforce_billing_access(current_user=actor, billing_key="testcases.generate")
        with bounded_model_requests(claim.reserve), model_scope(model):
            updated = _generate_stage(project, actor, record, stage, demo=_DemoStage(client, claim, settings, fingerprint, billing))
    except Exception as exc:
        try:
            result = claim.read_result()
            if result is None:
                claim.stop()
        except Exception as recovery_error:
            raise HTTPException(
                503,
                {
                    "code": "project_sync_publication_unconfirmed",
                    "message": "Draft publication could not be confirmed. Check the saved update before retrying.",
                },
            ) from recovery_error
        if result:
            return _verify_demo_stage(_project(project.project_id, actor), record, stage, claim.request_id, result)
        if isinstance(exc, (HTTPException, projects.ProjectConflictError)):
            raise
        logging.warning("Project draft refresh failed (%s)", type(exc).__name__)
        raise HTTPException(502, "Refresh failed. Saved drafts and the accepted correction are preserved.") from exc
    return _verify_demo_stage(updated, record, stage, claim.request_id)


def _resume_demo(project, actor, record, client, request_id):
    request_id = request_id or str(uuid4())
    for stage in _DEMO_STAGES:
        _check_current(project, record)
        if project.current_revision != record["project_revision"]:
            raise projects.ProjectConflictError(project.current_revision)
        if record["stages"][stage] == "draft":
            continue
        project = _run_demo_stage(project, actor, record, stage, client, request_id)
        record = project.project_sync
    return project


def _enroll_unstarted_change(project, actor, record, client):
    if record.get("draft_admission_version") == _DRAFT_ADMISSION_VERSION:
        return record
    # Earlier demo acceptances saved pending corrections without dispatching any stage.
    if record.get("draft_admission_version") or record["status"] != "pending" or record.get("attempts") != 0:
        raise HTTPException(409, "This saved correction does not have a resumable demo checkpoint.")
    project_ref = client.collection(projects.QA_PROJECTS_COLLECTION).document(project.project_id)
    change_ref = project_ref.collection("project_changes").document(record["id"])

    @projects.transactional
    def enroll(transaction):
        current = projects._document_to_dict(project_ref.get(transaction=transaction))
        if not current:
            raise projects.ProjectNotFoundError(project.project_id)
        projects._require_owner(current, actor)
        projects._check_revision(current, project.current_revision)
        saved = projects._document_to_dict(change_ref.get(transaction=transaction))
        if current.get("project_sync") != record or saved != record or record["project_revision"] != project.current_revision:
            raise HTTPException(409, "This saved correction changed before enrollment. Reload the project.")
        enrolled = {**record, "draft_admission_version": _DRAFT_ADMISSION_VERSION}
        if record["stages"].get("automation") == "pending":
            enrolled["stages"] = {**record["stages"], "automation": "deferred"}
        transaction.update(change_ref, enrolled)
        transaction.update(project_ref, {"project_sync": enrolled})
        return enrolled

    return enroll(client.transaction())


def resume_sync(project_id, actor, change_id, request_id=None):
    draft_client = draft_admission_client()
    project = _project(project_id, actor)
    record = projects._document_to_dict(_doc(project_id, change_id).get())
    if not record:
        raise HTTPException(404, "Project update not found")
    if record["status"] == "ready_for_review":
        return project
    _check_current(project, record)
    if draft_client is not None:
        return _resume_demo(project, actor, _enroll_unstarted_change(project, actor, record, draft_client), draft_client, request_id)
    if record.get("draft_admission_version"):
        raise draft_refresh_deferred()
    now = projects._utcnow()
    if record["status"] == "running" and record.get("lease_until", "") > now.isoformat():
        return project
    enforce_billing_access(current_user=actor, billing_key="testcases.generate")
    running = {
        **record,
        "status": "running",
        "lease_token": uuid4().hex,
        "lease_until": (now + timedelta(minutes=LEASE_MINUTES)).isoformat(),
        "attempts": record["attempts"] + 1,
        "error": None,
    }
    project = _write(project, actor, running, guard=_guard(project_id, record))
    try:
        for stage in ("use_cases", "test_cases", "automation"):
            record = project.project_sync
            if record["stages"][stage] in ("draft", "not_requested"):
                continue
            project = _generate_stage(project, actor, record, stage)
        record = {**project.project_sync, "status": "ready_for_review", "lease_until": None}
        return _write(project, actor, record, guard=_guard(project_id, record))
    except Exception as exc:
        latest = _project(project_id, actor)
        _check_current(latest, project.project_sync)
        record = {
            **latest.project_sync,
            "status": "partial",
            "lease_until": None,
            "error": exc.detail
            if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
            else "Refresh failed. Saved drafts and previous artifacts are preserved; retry the remaining stages.",
        }
        return _write(latest, actor, record, guard=_guard(project_id, record))
