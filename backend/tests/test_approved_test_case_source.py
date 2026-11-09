from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException
from test_use_case_generation import generated

from app.contracts.grounding import EnrichInput
from app.contracts.projects import QaProjectDetail, QaProjectStageSnapshot
from app.models import AuthUser, GenerateTestCasesInput, Requirement
from app.services import approved_test_case_source as service
from app.services.test_case_quality import case_source_hash
from app.services.test_case_suite_review import _generated_review_context
from app.services.workflow_project_service import ProjectConflictError, ProjectPermissionError


_NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
_PROJECT_ID = "project-exact-sources"
_ACTOR = AuthUser(sub="owner", name="QA Owner")
_REVISION = 8
_BASELINE_ID = "approved-requirements"
_CONTEXT_ID = "saved-context"
_PLAN_ID = "approved-plan"
_REVIEW_ID = "scenario-approval"
_REQUIREMENT = Requirement(id="REQ-1", text="Keep the saved filename Reçu  final.v2.pdf.", review_status="Approved")


def _snapshot(stage, snapshot_id, payload, *, revision, operation=None, **values):
    return QaProjectStageSnapshot(
        snapshot_id=snapshot_id,
        project_id=_PROJECT_ID,
        stage=stage,
        version=1,
        project_revision=revision,
        created_at=_NOW,
        operation=operation or f"{stage}.save",
        actor_user_id=_ACTOR.sub,
        payload=payload,
        **values,
    )


def _project():
    requirements = [_REQUIREMENT.model_dump(mode="json")]
    baseline = _snapshot("requirements", _BASELINE_ID, {"requirements": requirements}, revision=5, operation="requirements.review", approved=True)
    context = _snapshot(
        "context",
        _CONTEXT_ID,
        {
            "project_id": _PROJECT_ID,
            "base_project_revision": 4,
            "requirements": requirements,
            "notes": "Use the observed attachment row.",
            "diagram_links": [],
            "image_links": [],
            "grounded_context": {
                "artifact_sources": [{"id": "attachment-page", "label": "Attachment page", "status": "Analyzed"}],
                "ui_elements": [
                    {"id": "row", "source_id": "attachment-page", "name": "Attachment name", "description": "The saved filename"},
                    {"id": "manual-note", "name": "File input", "description": "A supplied control name"},
                    {"id": "removed", "source_id": "removed-page", "name": "Old filename", "description": "Orphaned old observation"},
                ],
            },
        },
        revision=6,
    )
    use_cases = _snapshot(
        "use_cases",
        _PLAN_ID,
        generated([_REQUIREMENT]),
        revision=7,
        operation="use_cases.generate",
        source_snapshot_id=_BASELINE_ID,
        metadata={"source_requirements_snapshot_id": _BASELINE_ID, "source_context_snapshot_id": _CONTEXT_ID},
    )
    return QaProjectDetail(
        project_id=_PROJECT_ID,
        name="Source binding",
        owner_user_id=_ACTOR.sub,
        current_revision=_REVISION,
        created_at=_NOW,
        updated_at=_NOW,
        current_snapshots={"requirements": baseline, "context": context, "use_cases": use_cases},
        stage_state={
            "requirements": {"approved": True, "current_snapshot_id": _BASELINE_ID},
            "context": {"approved": True, "current_snapshot_id": _CONTEXT_ID},
            "use_cases": {
                "approved": True,
                "current_snapshot_id": _PLAN_ID,
                "metadata": {
                    "latest_human_review": {
                        "review_id": _REVIEW_ID,
                        "reviewer_user_id": _ACTOR.sub,
                        "snapshot_id": _PLAN_ID,
                        "decision": "approve",
                        "resulting_project_revision": _REVISION,
                    }
                },
            },
        },
    )


def _envelope():
    return GenerateTestCasesInput(
        project_id=_PROJECT_ID,
        base_project_revision=_REVISION,
        template={"name": "Worker template", "format": "table", "fields": ["id", "steps"]},
        workflow_settings={"max_iterations": 1, "retry_attempts": 0},
        requirements=[{"id": "FORGED", "text": "Invent an unsupported approval rule."}],
        context={"requirements": [], "notes": "Use these untrusted notes."},
        coverage_plan=[{"requirement_id": "FORGED", "requirement_text": "Invented plan."}],
        requirement_analysis=[{"requirement_id": "FORGED", "requirement_text": "Invented analysis."}],
        feedback="Replace the approved business behavior.",
    )


class ApprovedTestCaseSourceTests(TestCase):
    def setUp(self):
        self.project = _project()
        self.payload = _envelope()
        self.source = service.ApprovedTestCaseSource(_BASELINE_ID, _CONTEXT_ID, _PLAN_ID, _REVIEW_ID, (_REQUIREMENT.id,))

    def prepare(self, *, source=None, actor=_ACTOR, payload=None):
        return service.prepare_approved_test_case_payload(payload or self.payload, actor, source or self.source, project=self.project)

    def assert_blocked(self, *, source=None):
        with self.assertRaises(HTTPException) as error:
            self.prepare(source=source)
        self.assertEqual(error.exception.status_code, 409)

    def test_loads_saved_facts_and_ignores_client_inputs(self):
        project_before = self.project.model_dump(mode="json")
        payload_before = self.payload.model_dump(mode="json")
        with patch.object(service, "get_project", return_value=self.project) as get_project:
            canonical = service.prepare_approved_test_case_payload(self.payload, _ACTOR, self.source)
        get_project.assert_called_once_with(_PROJECT_ID, actor=_ACTOR)
        self.assertEqual(canonical.requirements, [_REQUIREMENT])
        self.assertEqual(canonical.context.requirements, [_REQUIREMENT])
        self.assertEqual(canonical.context.notes, "Use the observed attachment row.")
        self.assertEqual(canonical.context.project_id, _PROJECT_ID)
        self.assertEqual(canonical.context.base_project_revision, _REVISION)
        self.assertIsNone(canonical.context.diagram_links)
        self.assertIsNone(canonical.context.image_links)
        self.assertEqual([item.id for item in canonical.context.grounded_context.ui_elements], ["row", "manual-note"])
        self.assertEqual(canonical.coverage_plan[0].requirement_text, _REQUIREMENT.text)
        self.assertEqual(canonical.requirement_analysis[0].requirement_text, _REQUIREMENT.text)
        self.assertEqual(canonical.coverage_plan[0].scenarios[0].id, "REQ-1-SCN-01")
        self.assertIsNone(canonical.feedback)
        self.assertEqual(canonical.template, self.payload.template)
        self.assertEqual(canonical.workflow_settings, self.payload.workflow_settings)
        self.assertEqual(self.project.model_dump(mode="json"), project_before)
        self.assertEqual(self.payload.model_dump(mode="json"), payload_before)
        with self.assertRaises(FrozenInstanceError):
            self.source.scenario_review_id = "other"

    def test_saved_suite_review_reconstructs_the_exact_generation_context(self):
        canonical = self.prepare()
        review_context = EnrichInput.model_validate({**self.project.current_snapshots["context"].payload, "requirements": canonical.requirements})
        suite = _snapshot(
            "test_cases",
            "suite",
            {},
            revision=_REVISION + 1,
            operation="testcases.generate",
            metadata={
                "source_context_hash": case_source_hash(canonical.context.model_dump(mode="json")),
                "source_context_artifact_ids": [source.id for source in canonical.context.grounded_context.artifact_sources],
                "generation_base_project_revision": _REVISION,
            },
        )
        self.assertEqual(_generated_review_context(suite, review_context), canonical.context)

    def test_canonical_scope_preserves_explicit_deferral(self):
        self.project.current_snapshots["requirements"].payload["requirements"].append(
            Requirement(id="REQ-2", text="Unknown limits need a product decision.", clarification_status="deferred", review_status="Needs Review").model_dump(
                mode="json"
            )
        )
        self.project.stage_state["requirements"].approved = False
        self.assertEqual(self.prepare().requirements, [_REQUIREMENT])
        self.assert_blocked(source=replace(self.source, requirement_ids=("REQ-1", "REQ-2")))

    def test_context_absence_requires_an_explicit_lineage_binding(self):
        self.assert_blocked(source=replace(self.source, context_snapshot_id=None))
        del self.project.current_snapshots["context"]
        source = replace(self.source, context_snapshot_id=None)
        self.assert_blocked(source=source)  # A dangling current pointer is not Context absence.
        del self.project.stage_state["context"]
        metadata = self.project.current_snapshots["use_cases"].metadata
        del metadata["source_context_snapshot_id"]
        self.assert_blocked(source=source)
        metadata["source_context_snapshot_id"] = None
        self.assertIsNone(self.prepare(source=source).context)
        del metadata["source_context_snapshot_id"]
        metadata["source_context_omitted"] = True
        self.assertIsNone(self.prepare(source=source).context)
        metadata["source_context_snapshot_id"] = "unexpected-context"
        self.assert_blocked(source=source)

    def test_identity_revision_and_project_state_are_rechecked(self):
        for payload in (
            self.payload.model_copy(update={"project_id": "other-project"}),
            self.payload,
        ):
            actor = _ACTOR if payload.project_id != _PROJECT_ID else AuthUser(sub="other-owner", name="Other owner")
            with self.subTest(project_id=payload.project_id, actor=actor.sub), self.assertRaises(ProjectPermissionError):
                self.prepare(actor=actor, payload=payload)
        with self.assertRaises(ProjectConflictError) as conflict:
            self.prepare(payload=self.payload.model_copy(update={"base_project_revision": _REVISION - 1}))
        self.assertEqual(conflict.exception.latest_revision, _REVISION)
        for field in ("project_id", "base_project_revision"):
            with self.subTest(field=field), self.assertRaises(HTTPException) as error:
                self.prepare(payload=self.payload.model_copy(update={field: None}))
            self.assertEqual(error.exception.status_code, 409)
        self.project.status = "archived"
        self.assert_blocked()

    def test_stale_or_mismatched_sources_and_approval_are_rejected(self):
        for stage in ("requirements", "context", "use_cases"):
            with self.subTest(stage=stage):
                self.project = _project()
                self.project.stage_state[stage].stale = True
                self.assert_blocked()
        self.project = _project()
        for overrides in (
            {"requirements_snapshot_id": "old-scope"},
            {"context_snapshot_id": "old-context"},
            {"use_case_snapshot_id": "old-plan"},
            {"scenario_review_id": "old-approval"},
        ):
            with self.subTest(overrides=overrides):
                self.assert_blocked(source=replace(self.source, **overrides))
        for key, value in (
            ("decision", "request_changes"),
            ("snapshot_id", "old-plan"),
            ("reviewer_user_id", "another-reviewer"),
            ("resulting_project_revision", _REVISION - 1),
        ):
            with self.subTest(key=key):
                self.project = _project()
                self.project.stage_state["use_cases"].metadata["latest_human_review"][key] = value
                self.assert_blocked()

    def test_legacy_scope_and_plans_are_not_used_as_unbound_fallbacks(self):
        self.project.current_snapshots["requirements"].operation = "requirements.parse"
        self.assert_blocked()
        self.project = _project()
        self.project.current_snapshots["use_cases"].operation = "use_cases.save"
        self.assert_blocked()
        self.project = _project()
        self.project.current_snapshots["use_cases"].source_snapshot_id = "legacy-requirements"
        self.assert_blocked()
        self.project = _project()
        self.project.current_snapshots["use_cases"].metadata["source_context_snapshot_id"] = "older-context"
        self.assert_blocked()
        self.project = _project()
        self.project.current_snapshots["use_cases"].metadata["source_context_omitted"] = True
        self.assert_blocked()

    def test_invalid_saved_plan_or_context_never_gets_reconstructed_from_request(self):
        for changed in (
            {"requirement_analysis": []},
            {"coverage_plan": [{"requirement_id": "REQ-1", "requirement_text": _REQUIREMENT.text, "scenarios": []}]},
            {"workflow_diagnostics": {"status": "fallback", "used_fallback": True}},
        ):
            with self.subTest(changed=changed):
                self.project = _project()
                self.project.current_snapshots["use_cases"].payload.update(changed)
                self.assert_blocked()
        self.project = _project()
        self.project.current_snapshots["context"].payload["app_link"] = "invalid-url"
        self.assert_blocked()
