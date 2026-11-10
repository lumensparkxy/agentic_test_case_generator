from copy import deepcopy
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from pydantic import ValidationError

from app.agents.substantive_review import RUBRIC
from app.contracts.grounding import EnrichInput
from app.contracts.projects import QaProjectDetail, QaProjectStageSnapshot, QaProjectStageState
from app.contracts.requirements import Requirement, RequirementCoveragePlan
from app.contracts.test_cases import TestCase as SavedCase
from app.services.suite_quality import build_suite_design_proof, resolve_suite_quality, reviewed_case_content_hash
from app.services.test_case_quality import case_source_hash


NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
ASSESSMENT_INPUT = "a" * 64
SAVED_VERSION = {"artifact_set_id": "set-1", "artifact_item_id": "item-1", "artifact_version_id": "version-1", "artifact_version_number": 1}


def snapshot(stage, snapshot_id, *, source=None, operation=None, payload=None, metadata=None):
    return QaProjectStageSnapshot(
        project_id="project-1",
        stage=stage,
        snapshot_id=snapshot_id,
        version=1,
        project_revision=3,
        actor_user_id="owner",
        operation=operation or f"{stage}.save",
        created_at=NOW,
        source_snapshot_id=source,
        payload=payload or {},
        metadata=metadata or {},
    )


def fixture(*, missing_setup=False, with_context=False):
    required = Requirement(id="REQ-1", text="Checkout shows the exact confirmation number.", review_status="Approved")
    deferred = Requirement(id="REQ-2", text="An unresolved refund policy.", review_status="Needs Review", clarification_status="deferred")
    excluded = Requirement(id="REQ-3", text="A different checkout environment.", review_status="Needs Review", clarification_status="excluded")
    group = RequirementCoveragePlan.model_validate(
        {
            "requirement_id": required.id,
            "requirement_text": required.text,
            "scenarios": [
                {"id": "SCN-1", "requirement_id": required.id, "title": "Inspect checkout", "objective": required.text},
            ],
        }
    )
    plan = [group.model_dump(mode="json")]
    reqs = snapshot(
        "requirements",
        "requirements-v1",
        operation="requirements.review",
        payload={"requirements": [required.model_dump(), deferred.model_dump(), excluded.model_dump()]},
    )
    scenario = snapshot(
        "use_cases",
        "plan-v1",
        source=reqs.snapshot_id,
        operation="use_cases.generate",
        payload={"coverage_plan": plan},
        metadata={
            "source_requirements_snapshot_id": reqs.snapshot_id,
            **({"source_context_snapshot_id": "context-v1"} if with_context else {"source_context_omitted": True}),
        },
    )
    context = snapshot(
        "context",
        "context-v1",
        payload={
            "notes": "A saved confirmation page.",
            "grounded_context": {
                "artifact_sources": [{"id": "a-1", "label": "Saved selector"}, {"id": "a-2", "label": "Optional selector"}],
                "ui_elements": [
                    {"id": "ui-1", "source_id": "a-1", "name": "Confirmation", "description": "Confirmation number"},
                    {"id": "ui-2", "source_id": "a-2", "name": "Extra", "description": "Extra content"},
                ],
            },
        },
    )
    states = {
        "requirements": QaProjectStageState(current_snapshot_id=reqs.snapshot_id, approved=False),
        "use_cases": QaProjectStageState(
            current_snapshot_id=scenario.snapshot_id,
            approved=True,
            metadata={
                "latest_human_review": {
                    "snapshot_id": scenario.snapshot_id,
                    "review_id": "human-review",
                    "reviewer_user_id": "owner",
                    "decision": "approve",
                }
            },
        ),
    }
    snapshots = {"requirements": reqs, "use_cases": scenario}
    if with_context:
        states["context"] = QaProjectStageState(current_snapshot_id=context.snapshot_id, approved=True)
        snapshots["context"] = context
    project = QaProjectDetail(
        project_id="project-1",
        name="Checkout QA",
        owner_user_id="owner",
        current_revision=4,
        created_at=NOW,
        updated_at=NOW,
        stage_state=states,
        current_snapshots=snapshots,
    )
    step = {
        "step": 1,
        "action": "Submit checkout and inspect the confirmation number.",
        "expected": "Checkout shows confirmation number β-01.",
        "test_data": "  β-01  ",
    }
    case = SavedCase.model_validate(
        {
            "id": "TC-1",
            "title": "Verify the checkout confirmation",
            "steps": [step],
            "linked_requirement_ids": [required.id],
            "scenario_refs": ["SCN-1"],
            "execution_prerequisites": [
                {"name": "checkout_role", "kind": "role", "description": "Checkout operator role", "reference": "fixture:checkout", "status": "missing"}
            ]
            if missing_setup
            else [],
            **SAVED_VERSION,
        }
    ).model_dump(mode="json")
    reviewed_hash = reviewed_case_content_hash([case])
    payload = {
        "test_cases": [case],
        "coverage_plan": deepcopy(plan),
        "generation_tasks": [],
        "workflow_diagnostics": {"status": "partial" if missing_setup else "completed"},
        "generation_evidence": {"design_precheck": {"passed": True, "case_content_hash": reviewed_hash, "assessment_input_hash": ASSESSMENT_INPUT}},
        "substantive_assessment": {
            "rubric": RUBRIC,
            "input_hash": ASSESSMENT_INPUT,
            "reviewed_case_content_hash": reviewed_hash,
            "scope": "whole_requirements",
            "status": "incomplete" if missing_setup else "assessed_complete",
            "model_status": "incomplete" if missing_setup else "assessed_complete",
            "design_status": "assessed_complete",
            "execution_status": "blocked" if missing_setup else "not_assessed",
            "case_grounding": [{"test_case_id": "TC-1", "reason": "Case uses recorded source facts.", "prerequisites": []}],
            "obligations": [
                {
                    "requirement_id": required.id,
                    "source_quote": required.text,
                    "obligation": "Show the exact confirmation number.",
                    "status": "covered",
                    "reason": "An observable confirmation number is asserted.",
                    "evidence": [{"test_case_id": "TC-1", "step": 1, "action": step["action"], "expected": step["expected"]}],
                }
            ],
        },
    }
    metadata = {
        "source_snapshot_ids": {
            "requirements": reqs.snapshot_id,
            "use_cases": scenario.snapshot_id,
            **({"context": context.snapshot_id} if with_context else {}),
        },
        "source_requirements_snapshot_id": reqs.snapshot_id,
        "source_use_case_snapshot_id": scenario.snapshot_id,
        "source_requirement_hashes": {required.id: case_source_hash(required.model_dump(mode="json"))},
        "source_use_case_hashes": {"SCN-1": case_source_hash(group.scenarios[0].model_dump(mode="json"))},
        "generation_base_project_revision": 4,
    }
    if with_context:
        applied = EnrichInput.model_validate(
            {
                **context.payload,
                "project_id": project.project_id,
                "base_project_revision": project.current_revision,
                "requirements": [required],
                "grounded_context": {key: value[:1] for key, value in context.payload["grounded_context"].items()},
            }
        )
        metadata.update(
            source_context_snapshot_id=context.snapshot_id,
            source_context_artifact_ids=["a-1"],
            source_context_hash=case_source_hash(applied.model_dump(mode="json")),
        )
    else:
        metadata["source_context_omitted"] = True
    payload["substantive_assessment"]["reviewed_source_binding"] = {
        "requirement_hashes": deepcopy(metadata["source_requirement_hashes"]),
        "context": "applied" if with_context else "absent",
        **({"context_hash": metadata["source_context_hash"]} if with_context else {}),
    }
    return project, payload, metadata


def attach_suite(project, payload, metadata, *, proof=None, suite_id="suite-v1", source="plan-v1", operation="testcases.generate"):
    suite = snapshot("test_cases", suite_id, source=source, operation=operation, payload=payload, metadata=metadata)
    if proof is not None:
        suite.metadata["suite_design_proof"] = {**proof, "suite_snapshot_id": suite.snapshot_id}
    project.current_snapshots["test_cases"] = suite
    project.stage_state["test_cases"] = QaProjectStageState(current_snapshot_id=suite.snapshot_id, approved=False)
    return suite


class SuiteQualityTests(TestCase):
    def proof(self, project, payload, metadata):
        return build_suite_design_proof(project, payload, metadata, plan_snapshot_id="plan-v1")

    def test_complete_design_and_declared_setup_are_independent(self):
        project, payload, metadata = fixture(missing_setup=True)
        proof = self.proof(project, payload, metadata)
        self.assertEqual(proof["design_status"], "complete")
        self.assertTrue(proof["context_omitted"])
        suite = attach_suite(project, payload, metadata, proof=proof)
        state = resolve_suite_quality(project, raw_suite=suite)
        self.assertEqual(state.source_freshness, "current")
        self.assertEqual(state.design_status, "complete")
        self.assertEqual(state.scenario_decision, "approved")
        self.assertEqual(state.scenario_review_id, "human-review")
        self.assertEqual(state.human_acceptance, "unavailable")
        self.assertEqual(state.declared_setup, "blocked")
        self.assertEqual(state.required_requirement_ids, ["REQ-1"])
        self.assertEqual(state.deferred_requirement_ids, ["REQ-2"])
        self.assertEqual(state.excluded_requirement_ids, ["REQ-3"])
        self.assertEqual(state.active_case_count, 1)
        self.assertFalse(project.stage_state["test_cases"].approved)
        self.assertFalse(suite.approved)
        project.current_revision += 1
        self.assertEqual(resolve_suite_quality(project).source_freshness, "current")

    def test_context_selector_must_reconstruct_the_applied_context(self):
        project, payload, metadata = fixture(with_context=True)
        proof = self.proof(project, payload, metadata)
        self.assertEqual(proof["context_artifact_ids"], ["a-1"])
        self.assertEqual(proof["applied_context_hash"], metadata["source_context_hash"])
        self.assertEqual(proof["design_status"], "complete")
        for changed in (
            {"source_context_artifact_ids": ["a-2"]},
            {"source_context_artifact_ids": ["a-1", "forged"]},
            {"source_context_hash": "b" * 64},
            {"source_context_omitted": True},
            {"generation_base_project_revision": 3},
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(self.proof(project, payload, {**metadata, **changed}))
        suite = attach_suite(project, payload, metadata, proof=proof)
        self.assertEqual(resolve_suite_quality(project).source_freshness, "current")
        project.stage_state["context"].stale = True
        state = resolve_suite_quality(project, raw_suite=suite)
        self.assertEqual((state.source_freshness, state.design_status), ("stale", "unknown"))

    def test_legacy_malformed_and_copied_evidence_never_promotes_approval(self):
        project, payload, metadata = fixture()
        suite = attach_suite(project, payload, metadata)
        suite.approved = True
        project.stage_state["test_cases"].approved = True
        self.assertEqual(resolve_suite_quality(project).design_status, "unknown")
        self.assertEqual(resolve_suite_quality(project).human_acceptance, "unavailable")
        suite.metadata["suite_design_proof"] = {"schema_version": 1, "design_status": "complete"}
        self.assertIn("design_proof_unverified", resolve_suite_quality(project).reason_codes)
        suite.metadata["suite_design_proof"] = {**self.proof(project, payload, metadata), "suite_snapshot_id": "different-suite"}
        self.assertEqual(resolve_suite_quality(project).design_status, "unknown")
        suite.metadata["suite_design_proof"]["suite_snapshot_id"] = suite.snapshot_id
        self.assertEqual(resolve_suite_quality(project).design_status, "complete")
        suite.operation = "project.sync.draft"
        self.assertEqual(resolve_suite_quality(project).design_status, "unknown")
        suite.operation = "testcases.generate"
        suite.source_snapshot_id = "different-plan"
        self.assertEqual(resolve_suite_quality(project).design_status, "unknown")

    def test_scope_scenario_and_assessment_binding_fail_closed(self):
        project, payload, metadata = fixture()
        for altered_metadata in (
            {"source_requirement_hashes": {}},
            {"source_use_case_hashes": {}},
            {"source_context_omitted": False},
            {"source_snapshot_ids": {"requirements": "wrong", "use_cases": "plan-v1"}},
            {"source_use_case_snapshot_id": "another-plan"},
        ):
            with self.subTest(altered_metadata=altered_metadata):
                self.assertIsNone(self.proof(project, payload, {**metadata, **altered_metadata}))
        for altered_assessment in (
            {"scope": "selected_scenarios"},
            {"input_hash": "b" * 64},
            {"reviewed_case_content_hash": "c" * 64},
            {"reviewed_source_binding": {"requirement_hashes": {"REQ-1": "b" * 64}, "context": "absent"}},
        ):
            with self.subTest(altered_assessment=altered_assessment):
                altered = deepcopy(payload)
                altered["substantive_assessment"].update(altered_assessment)
                self.assertIsNone(self.proof(project, altered, metadata))
        project.stage_state["use_cases"].metadata["latest_human_review"]["decision"] = "request_changes"
        self.assertIsNone(self.proof(project, payload, metadata))
        attach_suite(project, payload, metadata)
        self.assertEqual(resolve_suite_quality(project).scenario_decision, "changes_requested")

    def test_same_cases_assessed_against_different_context_do_not_get_proof(self):
        project, payload, metadata = fixture(with_context=True)
        binding = payload["substantive_assessment"]["reviewed_source_binding"]
        binding["context_hash"] = "f" * 64
        self.assertIsNone(self.proof(project, payload, metadata))

    def test_complete_claim_revalidates_model_evidence_and_partial_groups(self):
        project, payload, metadata = fixture()
        for altered_assessment in (
            {"case_grounding": []},
            {"obligations": []},
            {"execution_status": "blocked"},
            {"diagnostics": {"unverified_group_count": 1, "failure_reason": "provider_unavailable"}},
            {"diagnostics": {"groups": [{"design_status": "unknown"}]}},
            {"diagnostics": {"groups": [{"design_status": "assessed_complete", "status": "unknown"}], "unverified_group_count": 0}},
        ):
            with self.subTest(altered_assessment=altered_assessment):
                altered = deepcopy(payload)
                altered["substantive_assessment"].update(altered_assessment)
                self.assertEqual(self.proof(project, altered, metadata)["design_status"], "unknown")
        altered = deepcopy(payload)
        altered["substantive_assessment"]["obligations"][0]["evidence"][0]["expected"] = "A fabricated result."
        self.assertEqual(self.proof(project, altered, metadata)["design_status"], "unknown")
        altered["substantive_assessment"]["design_status"] = "incomplete"
        self.assertEqual(self.proof(project, altered, metadata)["design_status"], "incomplete")

    def test_complete_grouped_proof_survives_storage_omitting_null_metadata(self):
        from app.services.workflow_project_service import _serialize_value

        project, payload, metadata = fixture()
        payload["substantive_assessment"]["diagnostics"] = {
            "unverified_group_count": 0,
            "failure_reason": None,
            "groups": [{"design_status": "assessed_complete", "status": "assessed_complete", "failure_reason": None}],
        }
        proof = self.proof(project, payload, metadata)
        self.assertEqual(proof["design_status"], "complete")
        attach_suite(project, _serialize_value(payload), _serialize_value(metadata), proof=_serialize_value(proof))
        state = resolve_suite_quality(project)
        self.assertEqual((state.source_freshness, state.design_status), ("current", "complete"))

    def test_reassessment_requires_the_original_exact_complete_proof(self):
        for altered in (None, "proof_hash", "source_stale", "archived", "different_parent"):
            with self.subTest(altered=altered):
                project, payload, metadata = fixture()
                proof = self.proof(project, payload, metadata)
                suite = attach_suite(project, payload, metadata, proof=proof)
                if altered == "proof_hash":
                    suite.metadata["suite_design_proof"]["case_content_hash"] = "0" * 64
                elif altered == "source_stale":
                    project.stage_state["use_cases"].stale = True
                elif altered == "archived":
                    project.status = "archived"
                elif altered == "different_parent":
                    suite.source_snapshot_id = "different-plan"
                original = suite.model_dump(mode="json")
                with patch("app.services.suite_quality._complete_assessment", return_value=False):
                    result = resolve_suite_quality(project, raw_suite=suite)
                self.assertEqual(result.design_status, "unknown")
                self.assertEqual("design_reassessment_required" in result.reason_codes, altered is None)
                self.assertEqual(result.source_freshness == "current", altered is None)
                self.assertEqual(suite.model_dump(mode="json"), original)

    def test_structural_failure_and_placeholder_cannot_be_verified_complete(self):
        project, payload, metadata = fixture()
        payload["generation_evidence"]["design_precheck"]["passed"] = False
        self.assertEqual(self.proof(project, payload, metadata)["design_status"], "incomplete")
        payload["generation_evidence"]["design_precheck"]["passed"] = True
        payload["test_cases"][0]["steps"][0]["expected"] = "The changed behavior works without regressing preserved coverage."
        changed_hash = reviewed_case_content_hash(payload["test_cases"])
        payload["generation_evidence"]["design_precheck"]["case_content_hash"] = changed_hash
        payload["substantive_assessment"]["reviewed_case_content_hash"] = changed_hash
        self.assertEqual(self.proof(project, payload, metadata)["design_status"], "incomplete")

    def test_literal_case_content_and_saved_versions_are_both_bound(self):
        project, payload, metadata = fixture()
        proof = self.proof(project, payload, metadata)
        suite = attach_suite(project, payload, metadata, proof=proof)
        original = suite.payload["test_cases"][0]
        versioned = {**original, "artifact_version_id": "next-version"}
        self.assertEqual(reviewed_case_content_hash([original]), reviewed_case_content_hash([versioned]))
        suite.payload["test_cases"][0] = versioned
        self.assertEqual(resolve_suite_quality(project).design_status, "unknown")
        altered = deepcopy(original)
        altered["steps"][0]["test_data"] = "β-01"
        self.assertNotEqual(reviewed_case_content_hash([original]), reviewed_case_content_hash([altered]))
        self.assertNotEqual(reviewed_case_content_hash([original, altered]), reviewed_case_content_hash([altered, original]))
        with self.assertRaises(ValidationError):
            reviewed_case_content_hash([{**original, "unrecognized_fact": "extra content"}])

    def test_reassessment_binds_old_suite_and_preserves_deprecated_versions(self):
        project, payload, metadata = fixture()
        original = payload["test_cases"][0]
        payload["test_cases"].append({**original, "id": "TC-OLD", "status": "Deprecated", "artifact_item_id": "old", "artifact_version_id": "old-version"})
        metadata["source_test_cases_snapshot_id"] = "original-suite"
        proof = self.proof(project, payload, metadata)
        self.assertEqual(proof["design_status"], "complete")
        attach_suite(project, payload, metadata, proof=proof, source="original-suite", operation="test_cases.review")
        state = resolve_suite_quality(project)
        self.assertEqual(state.design_status, "complete")
        self.assertEqual((state.active_case_count, state.deprecated_case_count), (1, 1))
        self.assertEqual(state.declared_setup, "not_assessed")
        project.current_snapshots["test_cases"].source_snapshot_id = "wrong-suite"
        self.assertEqual(resolve_suite_quality(project).design_status, "unknown")
