"""Accept source corrections once, then refresh reviewable drafts in dependency order."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
import logging
from fastapi import HTTPException
from ..contracts.project_sync import ProjectDecision
from ..contracts.requirements import Requirement
from ..contracts.grounding import EnrichInput
from ..models import GenerateTestCasesInput, GenerateTestCasesResponse, TestCaseTemplate
from . import workflow_project_service as projects
from .quality_feedback import digest, _project, _check_revision, _snapshot
from .project_inputs import manifest_from_payload, behavioural_version
from .guidance_runtime import prepare_run
from .guidance_service import guidance_scope, public_manifest
from .billing_service import enforce_billing_access, record_billing_consumption
from .audit_service import record_usage_event
from .versioning_service import persist_test_case_versions
from .use_case_generation_service import _validate_output
from ..agents.use_case_agent import generate_use_cases
from ..agents.test_case_agent import generate_test_cases
from ..agents.automation_agent import generate_playwright_pom
from .test_case_quality import placeholder_reason, delivered_coverage
from .scenario_review_state import carry_scenario_reviews

LEASE_MINUTES = 20


def _doc(project_id, change_id):
    return projects._get_project_doc(project_id).collection("project_changes").document(change_id)


def _write(project, actor, record, *, snapshots=(), states=None, updates=None, writes=(), guard=None):
    revision = project.current_revision + 1
    now = projects._utcnow()
    record = {**record, "updated_at": now.isoformat(), "project_revision": revision}
    values = {"current_revision": revision, "updated_at": now, "project_sync": record, **(updates or {})}
    if states is not None:
        values["stage_state"] = states
    pending = [(projects._get_project_doc(project.project_id).collection("snapshots").document(s["snapshot_id"]), s, False) for s in snapshots]
    projects._commit_project_revision(
        project.project_id,
        actor,
        project.current_revision,
        values,
        {
            "event_id": str(uuid4()),
            "project_id": project.project_id,
            "event_type": "project.sync",
            "summary": f"Project update: {record['status']}",
            "project_revision": revision,
            "actor_user_id": actor.sub,
            "metadata": {"change_id": record["id"]},
            "occurred_at": now,
        },
        pending_writes=[*pending, *writes, (_doc(project.project_id, record["id"]), record, False)],
        transaction_guard=guard,
    )
    return projects.get_project(project.project_id, actor=actor)


def accept_finding(project_id, actor, payload, request_id):
    project = _project(project_id, actor)
    change_id = "change_" + digest([actor.sub, project_id, request_id])[:24]
    existing = projects._document_to_dict(_doc(project_id, change_id).get())
    fingerprint = digest(payload.model_dump(mode="json"))
    if existing:
        if existing["fingerprint"] != fingerprint:
            raise HTTPException(409, "Request ID belongs to a different correction")
        return resume_sync(project_id, actor, change_id)
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
    for stage in projects.DOWNSTREAM_STAGES["context"]:
        if stage in states:
            states[stage].update(stale=True, approved=False, stale_reason="Accepted project correction; drafts are refreshing")
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
    enforce_billing_access(current_user=actor, billing_key="testcases.generate")
    _write(project, actor, record, snapshots=snapshots, states=states, updates={"project_decisions": decisions}, writes=[(finding_ref, finding, False)])
    return resume_sync(project_id, actor, change_id)


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
        if not saved or saved.get("lease_token") != record.get("lease_token"):
            raise HTTPException(409, "Refresh attempt was superseded")

    return guard


def _draft(project, actor, record, stage, payload, manifest, *, counts=None, pending_writes=()):
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
    source_ids = {name: snapshot.snapshot_id for name, snapshot in project.current_snapshots.items() if name in ("requirements", "context", "use_cases")}
    source["metadata"].update(previous_snapshot_id=source.get("source_snapshot_id"), source_snapshot_ids=source_ids)
    source["source_snapshot_id"] = project.current_snapshots[upstream].snapshot_id
    source["operation"] = "project.sync.draft"
    states[stage].update(source_snapshot_id=source["source_snapshot_id"], operation=source["operation"])
    if stage == "use_cases":
        old = project.current_snapshots.get(stage)
        reviews = carry_scenario_reviews(
            payload, old.payload if old else {}, project.stage_state[stage].metadata if old else {}, old.snapshot_id if old else "", source["snapshot_id"]
        )
        states[stage]["metadata"]["scenario_reviews"] = reviews
    record = {**record, "stages": {**record["stages"], label: "draft"}, "expected_snapshots": {**record["expected_snapshots"], stage: source["snapshot_id"]}}
    if counts:
        record["review"] = counts
    return _write(project, actor, record, snapshots=[source], states=states, writes=pending_writes, guard=_guard(project.project_id, record))


def _generate_stage(project, actor, record, stage):
    affected = set(record["affected_requirement_ids"])
    reqs = [Requirement.model_validate(r) for r in project.current_snapshots["requirements"].payload["requirements"] if r["id"] in affected]
    context = EnrichInput.model_validate({**project.current_snapshots["context"].payload, "requirements": reqs})
    payload = GenerateTestCasesInput(
        requirements=reqs,
        context=context,
        project_id=project.project_id,
        template=TestCaseTemplate(name="Project update", format="json", fields=["id", "title", "steps", "expected_result"]),
    )
    request_id = f"{record['id']}:{stage}:{record['attempts']}"
    manifest = prepare_run(stage, actor, project.project_id, request_id, requirement_ids=sorted(affected), input_fingerprint=digest(record["input_manifest"]))
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
            return _draft(project, actor, record, "use_cases", merged, manifest)
        if stage == "test_cases":
            plans = project.current_snapshots["use_cases"].payload
            payload = GenerateTestCasesInput.model_validate(
                {**payload.model_dump(), "coverage_plan": [p for p in plans["coverage_plan"] if p["requirement_id"] in affected]}
            )
            output = GenerateTestCasesResponse.model_validate(
                generate_test_cases(payload, actor_user_id=actor.sub, request_id=request_id, operation="impact.update.apply")
            )
            refs = {s.id for p in payload.coverage_plan for s in p.scenarios}
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
            versioned = persist_test_case_versions(
                current_test_cases=preserved + generated,
                previous_test_cases=previous,
                actor=actor,
                request_id=request_id,
                workflow_run_id=None,
                source_event_id=None,
                operation="project.sync",
                approved=False,
                reuse_unchanged_versions=True,
                pending_writes=pending_writes,
            )
            generated_ids = {c["id"] for c in generated}
            combined = [c.model_dump(mode="json") for c in versioned]
            generated = [c for c in combined if c["id"] in generated_ids]
            value = {
                **old_payload,
                "test_cases": combined,
                "approved": False,
                "coverage_plan": plans["coverage_plan"],
                "coverage_metrics": delivered_coverage(combined, plans["coverage_plan"]),
                "generation_tasks": [],
                "substantive_assessment": {"status": "unknown", "reason": "The refreshed suite requires combined assessment and human review."},
                "review": {"approved": False, "score": 0, "summary": "Review project changes before export."},
                "workflow_diagnostics": output.workflow_diagnostics.model_dump(mode="json"),
            }
            counts = {
                "changed_ids": [c["id"] for c in generated],
                "removed_ids": [c["id"] for c in replaced[len(generated) :]],
                "changed": min(len(replaced), len(generated)),
                "added": max(0, len(generated) - len(replaced)),
                "preserved": len(preserved),
            }
            billing_context = enforce_billing_access(current_user=actor, billing_key="testcases.generate")
            event_id = record_usage_event(
                event_type="testcases.generated",
                billing_key="testcases.generate",
                quantity=len(generated),
                unit="test_case",
                actor=actor,
                request_id=request_id,
                workflow_run_id=None,
                status="completed",
                event_id=f"sync-{project.project_id}-{record['id']}",
                pending_writes=pending_writes,
                firestore_client=projects.get_required_firestore_client(unavailable_message="Project storage unavailable"),
            )
            result = _draft(project, actor, record, "test_cases", value, manifest, counts=counts, pending_writes=pending_writes)
            try:
                record_billing_consumption(
                    current_user=actor,
                    billing_context=billing_context,
                    source_event_id=event_id,
                    request_id=request_id,
                    workflow_run_id=None,
                    billing_key="testcases.generate",
                    quantity=len(generated),
                    unit="test_case",
                )
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


def resume_sync(project_id, actor, change_id):
    project = _project(project_id, actor)
    record = projects._document_to_dict(_doc(project_id, change_id).get())
    if not record:
        raise HTTPException(404, "Project update not found")
    if record["status"] == "ready_for_review":
        return project
    _check_current(project, record)
    now = projects._utcnow()
    if record["status"] == "running" and record.get("lease_until", "") > now.isoformat():
        return project
    enforce_billing_access(current_user=actor, billing_key="testcases.generate")
    record = {
        **record,
        "status": "running",
        "lease_token": uuid4().hex,
        "lease_until": (now + timedelta(minutes=LEASE_MINUTES)).isoformat(),
        "attempts": record["attempts"] + 1,
        "error": None,
    }
    project = _write(project, actor, record)
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
