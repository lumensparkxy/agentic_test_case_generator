"""Focused offline checks for exact case delivery and blinded adjudication labels."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

from app.agents.export_agent import export_to_json
from app.contracts.test_cases import TestCase
from scripts import prepare_case_adjudication as adjudication
from scripts.prepare_case_adjudication import checked_delivery, digest, validate_labels


REQUIREMENT_ROWS = [
    {"id": "R1", "text": "Supervisor approval is allowed."},
    {"id": "R2", "text": "The configured threshold is unspecified."},
]


def _storage(value):
    """Model the documented Firestore omission of null dictionary fields."""
    if isinstance(value, dict):
        return {key: _storage(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_storage(item) for item in value]
    return value


def _delivery(*, revision_offset=0):
    suffix = f"-{revision_offset}" if revision_offset else ""
    requirements_id, context_id, plan_id, suite_id = (f"requirements-2{suffix}", f"context-1{suffix}", f"scenarios-1{suffix}", f"suite-1{suffix}")
    sources = {"requirements": requirements_id, "context": context_id, "use_cases": plan_id, "test_cases": suite_id}
    plan_sources = {"source_requirements_snapshot_id": requirements_id, "source_context_snapshot_id": context_id}
    requirements = [
        {
            "id": "REQ-001",
            "requirement_uid": "stable-req-1",
            "text": "Supervisor approval is allowed.",
            "review_status": "Approved",
            "lifecycle_status": "active",
        },
        {
            "id": "REQ-002",
            "requirement_uid": "stable-req-2",
            "text": "The configured threshold is unspecified.",
            "review_status": "Needs Review",
            "clarification_status": "deferred",
            "lifecycle_status": "active",
        },
    ]
    source_version = sha256(adjudication.markdown_source(REQUIREMENT_ROWS).encode()).hexdigest()
    for requirement, line in zip(requirements, (2, 5), strict=True):
        requirement["sources"] = [
            {
                "source_system": "file",
                "import_id": "import-original",
                "source_version": source_version,
                "excerpt_verified": True,
                "source_section": f"Lines {line}-{line}",
                "excerpt": requirement["text"],
            }
        ]
    plan = [{"requirement_id": "REQ-001", "scenarios": [{"id": "REQ-001-SCN-01", "requirement_id": "REQ-001"}]}]
    cases = [
        TestCase(
            id=f"TC-{index:03}",
            title=title,
            test_data=literal,
            steps=[
                {"step": 1, "action": "Enter the reference", "expected": "The exact reference is visible.", "test_data": literal},
                {"step": 2, "action": "Submit the request", "expected": "The expected result is recorded.", "test_data": None},
            ],
            linked_requirement_ids=["REQ-001"],
            scenario_refs=["REQ-001-SCN-01"],
            execution_prerequisites=[
                {
                    "name": "supervisor",
                    "kind": "credential",
                    "description": "Provisioned Supervisor account",
                    "requirement_ids": ["REQ-001"],
                    "status": "missing",
                }
            ],
            artifact_set_id="case-set-1",
            artifact_item_id=f"item-{index}",
            artifact_version_id=f"case-version-{index}",
            artifact_version_number=index,
            generation_source="model",
            generation_pass_id=f"private-generation-pass-{index}",
        )
        for index, (title, literal) in enumerate([("Accept the literal reference", "  Zürich–01\n第二行  "), ("Reject a blank reference", "")], start=1)
    ]
    hydrated = [case.model_dump(mode="json") for case in cases]
    versions = [
        {
            "case_id": case.id,
            "artifact_item_id": case.artifact_item_id,
            "artifact_version_id": case.artifact_version_id,
            "artifact_version_number": case.artifact_version_number,
        }
        for case in cases
    ]

    def snapshot(stage, snapshot_id, revision, operation, payload, *, metadata=None, source=None, approved=False):
        return {
            "project_id": "project-1",
            "stage": stage,
            "snapshot_id": snapshot_id,
            "version": 1,
            "project_revision": revision + revision_offset,
            "operation": operation,
            "approved": approved,
            "source_snapshot_id": source,
            "request_id": f"request-{stage}{suffix}",
            "payload": payload,
            "metadata": metadata or {},
        }

    current = {
        "requirements": snapshot("requirements", requirements_id, 5, "requirements.review", {"requirements": requirements, "import_id": "import-original"}),
        "context": snapshot("context", context_id, 6, "requirements.enrich", {"grounded_context": {"notes": "Supervisor setup is absent."}}, approved=True),
        "use_cases": snapshot("use_cases", plan_id, 7, "use_cases.generate", {"coverage_plan": plan}, metadata=plan_sources, source=requirements_id),
        "test_cases": snapshot(
            "test_cases",
            suite_id,
            9,
            "testcases.generate",
            {"test_cases": _storage(hydrated), "coverage_plan": deepcopy(plan)},
            source=plan_id,
            metadata={
                **plan_sources,
                "source_use_case_snapshot_id": plan_id,
                "source_snapshot_ids": {stage: identity for stage, identity in sources.items() if stage != "test_cases"},
            },
        ),
    }
    stage_state = {stage: {"current_snapshot_id": value["snapshot_id"], "version": value["version"], "stale": False} for stage, value in current.items()}
    stage_state["use_cases"]["approved"] = True
    stage_state["use_cases"]["metadata"] = {
        "latest_human_review": {
            "snapshot_id": plan_id,
            "decision": "approve",
            "review_id": f"scenario-approval-1{suffix}",
            "resulting_project_revision": 8 + revision_offset,
        }
    }
    project = {"project_id": "project-1", "status": "active", "current_revision": 9 + revision_offset, "current_snapshots": current, "stage_state": stage_state}
    audit = {
        "schema_version": 1,
        "exported_at": "2026-11-09T23:00:00Z",
        "project_id": project["project_id"],
        "project_revision": project["current_revision"],
        "snapshot_id": suite_id,
        "snapshot_version": 1,
        "artifact_set_id": "case-set-1",
        "case_versions": versions,
        "is_draft": True,
        "draft_override_used": True,
        "draft_override_reason": "Supervisor setup is absent.",
        "delivered_case_count": len(cases),
        "source_snapshot_ids": sources,
        "execution_run_ids": [],
    }
    exported = json.loads(export_to_json(cases, audit_metadata=audit))
    report = snapshot(
        "reports",
        f"report-1{suffix}",
        10,
        "export.json",
        {
            "source": "export",
            "format": "json",
            "test_case_count": len(cases),
            "approved": False,
            "audit_metadata": _storage(audit),
            "evidence": {"source_snapshot_ids": deepcopy(sources), "execution_run_ids": []},
        },
        metadata={
            "source_snapshot_ids": deepcopy(sources),
            "test_case_count": len(cases),
            "format": "json",
            "execution_run_ids": [],
            "export_audit_fingerprint": sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest(),
        },
        source=suite_id,
    )
    return project, hydrated, exported, report


def _reviewed_suite(project):
    suite = project["current_snapshots"]["test_cases"]
    parent = "saved-impact-draft"
    suite.update(operation="test_cases.review", source_snapshot_id=parent)
    project["stage_state"]["test_cases"]["source_snapshot_id"] = parent
    project["timeline"] = [
        {
            "project_id": project["project_id"],
            "event_type": "stage.snapshot_created",
            "stage": "test_cases",
            "project_revision": suite["project_revision"] - 1,
            "snapshot_id": parent,
        }
    ]
    return suite


def _capture_files(folder, evidence):
    folder.mkdir()
    captures = {}
    for kind, value in evidence.items():
        path = folder / f"{kind}.json"
        raw = adjudication.json_bytes(value)
        path.write_bytes(raw)
        captures[kind] = {"path": f"{folder.name}/{path.name}", "sha256": sha256(raw).hexdigest()}
    return captures


def _packet_and_labels():
    _, hydrated, exported, _ = _delivery()
    packet = {
        "sample_id": "RANDOM-SAMPLE",
        "input": {"requirements": REQUIREMENT_ROWS, "context": {"notes": "Supervisor setup is absent."}},
        "obligations": {
            "permission": {"source_id": "R1", "critical": True, "required_observation": "Supervisor approval is allowed."},
            "durability": {"source_id": "R1", "critical": True, "required_observation": "Persist the changed record."},
            "threshold": {"source_id": "R2", "critical": False, "required_observation": "Retain the explicit deferral."},
        },
        "deferred_obligation_ids": ["threshold"],
        "saved_test_cases": [
            {**case, "execution_prerequisites": saved["execution_prerequisites"]} for case, saved in zip(exported["test_cases"], hydrated, strict=True)
        ],
        "exported_test_cases": deepcopy(exported["test_cases"]),
    }
    seal = digest(packet)
    labels = {
        "sample_id": packet["sample_id"],
        "packet_sha256": seal,
        "judgments": {
            "permission": {
                "status": "covered",
                "source_id": "R1",
                "reason": "The case observes the recorded outcome.",
                "citations": [{"case_id": "TC-001", "step": 2}],
            },
            "durability": {"status": "unknown", "source_id": "R1", "reason": "The saved evidence does not show a readback after reopening.", "citations": []},
            "threshold": {"status": "covered", "source_id": "R2", "reason": "R2 was explicitly deferred in the source scope.", "citations": []},
        },
    }
    return packet, seal, labels


class CheckedDeliveryTests(unittest.TestCase):
    def test_frozen_source_checks_exact_version_quote_and_line_range_for_each_requirement(self):
        project, *_ = _delivery()
        original = adjudication.markdown_source(REQUIREMENT_ROWS)
        revised = original.replace("unspecified", "173 EUR")
        adjudication.check_saved_source(project, original)
        with self.assertRaisesRegex(ValueError, "REQ-001 lacks an exact frozen source reference"):
            adjudication.check_saved_source(project, revised)

        requirement = project["current_snapshots"]["requirements"]["payload"]["requirements"][1]
        ref = requirement["sources"][0]
        for changes in (
            {"import_id": "superseded-import"},
            {"excerpt_verified": False},
            {"excerpt": "The configured threshold is 173 EUR."},
            {"source_section": "Lines 2-2"},
            {"source_section": "Lines 4-5"},
        ):
            with self.subTest(changes=changes):
                requirement["sources"] = [{**ref, **changes}]
                with self.assertRaisesRegex(ValueError, "REQ-002 lacks an exact frozen source reference"):
                    adjudication.check_saved_source(project, original)

    def test_preserves_exact_projection_and_saved_prerequisites(self):
        project, hydrated, exported, report = _delivery()

        cases = checked_delivery(project, hydrated, exported, report)

        self.assertEqual([row["id"] for row in cases["saved_test_cases"]], ["TC-001", "TC-002"])
        self.assertEqual(cases["exported_test_cases"], exported["test_cases"])
        self.assertEqual(cases["saved_test_cases"][0]["steps"], hydrated[0]["steps"])
        self.assertEqual(cases["saved_test_cases"][0]["execution_prerequisites"], hydrated[0]["execution_prerequisites"])
        self.assertNotIn("generation_pass_id", cases["saved_test_cases"][0])
        self.assertNotIn("artifact_version_id", cases["saved_test_cases"][0])
        self.assertEqual(cases["exported_test_cases"][0]["steps"][0]["test_data"], "  Zürich–01\n第二行  ")
        self.assertEqual(cases["exported_test_cases"][1]["steps"][0]["test_data"], "")
        self.assertIsNone(cases["exported_test_cases"][0]["steps"][1]["test_data"])

    def test_reviewed_suite_keeps_current_plan_and_names_preceding_saved_suite(self):
        project, hydrated, exported, report = _delivery()
        suite = _reviewed_suite(project)
        checked_delivery(project, hydrated, exported, report)  # Baseline review has no parent metadata field.
        suite["metadata"]["source_test_cases_snapshot_id"] = suite["source_snapshot_id"]
        checked_delivery(project, hydrated, exported, report)
        suite["metadata"]["source_test_cases_snapshot_id"] = "another-suite"
        with self.assertRaisesRegex(ValueError, "preceding saved Test Cases snapshot"):
            checked_delivery(project, hydrated, exported, report)
        suite["metadata"]["source_test_cases_snapshot_id"] = suite["source_snapshot_id"]
        parent_event = project["timeline"][0]
        parent_event["snapshot_id"] = "another-suite"
        with self.assertRaisesRegex(ValueError, "preceding saved Test Cases snapshot"):
            checked_delivery(project, hydrated, exported, report)
        parent_event["snapshot_id"] = suite["source_snapshot_id"]
        suite["metadata"]["source_snapshot_ids"]["use_cases"] = "stale-plan"
        with self.assertRaisesRegex(ValueError, "Mismatched suite use_cases source"):
            checked_delivery(project, hydrated, exported, report)

    def test_rejects_dropped_reordered_or_changed_export_values(self):
        def reorder_steps(rows):
            rows[0]["steps"].reverse()

        def trim_literal(rows):
            rows[0]["steps"][0]["test_data"] = rows[0]["steps"][0]["test_data"].strip()

        def change_unicode(rows):
            rows[0]["steps"][0]["test_data"] = rows[0]["steps"][0]["test_data"].replace("ü", "u\u0308")

        def drop_null(rows):
            del rows[0]["steps"][1]["test_data"]

        def replace_null(rows):
            rows[0]["steps"][1]["test_data"] = ""

        def replace_step_type(rows):
            rows[0]["steps"][0]["step"] = True

        for label, change in {
            "reordered steps": reorder_steps,
            "trimmed literal": trim_literal,
            "normalized Unicode": change_unicode,
            "missing null field": drop_null,
            "empty instead of null": replace_null,
            "boolean step number": replace_step_type,
            "reordered cases": lambda rows: rows.reverse(),
            "missing case": lambda rows: rows.pop(),
            "duplicate case": lambda rows: rows.append(deepcopy(rows[0])),
        }.items():
            with self.subTest(change=label):
                project, hydrated, exported, report = _delivery()
                change(exported["test_cases"])
                with self.assertRaises(ValueError):
                    checked_delivery(project, hydrated, exported, report)

    def test_rejects_misattributed_case_versions_even_when_all_ids_exist(self):
        for change in ("swap", "number_type"):
            with self.subTest(change=change):
                project, hydrated, exported, report = _delivery()
                rows = exported["audit_metadata"]["case_versions"]
                if change == "swap":
                    rows[0]["artifact_version_id"], rows[1]["artifact_version_id"] = rows[1]["artifact_version_id"], rows[0]["artifact_version_id"]
                else:
                    rows[0]["artifact_version_number"] = True
                report["payload"]["audit_metadata"] = deepcopy(exported["audit_metadata"])
                report["metadata"]["export_audit_fingerprint"] = sha256(json.dumps(exported["audit_metadata"], sort_keys=True).encode()).hexdigest()
                with self.assertRaises(ValueError):
                    checked_delivery(project, hydrated, exported, report)

    def test_rejects_matching_but_incomplete_exact_step_reads(self):
        project, hydrated, exported, report = _delivery()
        del hydrated[0]["steps"][1]["test_data"]
        del exported["test_cases"][0]["steps"][1]["test_data"]
        with self.assertRaisesRegex(ValueError, "Exact step fields"):
            checked_delivery(project, hydrated, exported, report)

    def test_rejects_a_noncurrent_or_unapproved_source_binding(self):
        for mismatch in (
            "saved case",
            "saved prerequisite",
            "report source",
            "report audit",
            "report audit fingerprint",
            "suite source",
            "plan source",
            "scenario decision",
            "requirement approval",
            "stale",
        ):
            with self.subTest(mismatch=mismatch):
                project, hydrated, exported, report = _delivery()
                snapshots = project["current_snapshots"]
                if mismatch == "saved case":
                    snapshots["test_cases"]["payload"]["test_cases"][0]["steps"][0]["expected"] = "Different stored assertion."
                elif mismatch == "saved prerequisite":
                    snapshots["test_cases"]["payload"]["test_cases"][0]["execution_prerequisites"][0]["reference"] = ""
                elif mismatch == "report source":
                    report["source_snapshot_id"] = "other-suite"
                elif mismatch == "report audit":
                    report["payload"]["audit_metadata"]["draft_override_reason"] = "Unrelated evidence"
                elif mismatch == "report audit fingerprint":
                    report["metadata"]["export_audit_fingerprint"] = "0" * 64
                elif mismatch == "suite source":
                    snapshots["test_cases"]["source_snapshot_id"] = "other-plan"
                elif mismatch == "plan source":
                    snapshots["use_cases"]["metadata"]["source_context_snapshot_id"] = "other-context"
                elif mismatch == "scenario decision":
                    project["stage_state"]["use_cases"]["metadata"]["latest_human_review"]["snapshot_id"] = "other-plan"
                elif mismatch == "requirement approval":
                    snapshots["requirements"]["payload"]["requirements"][0]["review_status"] = "Needs Review"
                else:
                    project["stage_state"]["test_cases"]["stale"] = True
                with self.assertRaises(ValueError):
                    checked_delivery(project, hydrated, exported, report)


class ValidateLabelsTests(unittest.TestCase):
    def test_keeps_unknown_in_scope_and_counts_deferral_separately(self):
        packet, seal, labels = _packet_and_labels()

        result = validate_labels(packet, seal, labels)

        self.assertEqual(result["in_scope"], 2)
        self.assertEqual(result["in_scope_status"], {"permission": "covered", "durability": "unknown"})
        self.assertEqual(result["covered"], 1)
        self.assertEqual(result["unknown"], 1)
        self.assertEqual(result["missed_critical"], ["durability"])
        self.assertEqual(result["deferred"], {"threshold": "covered"})

    def test_rejects_a_changed_packet_or_label_seal(self):
        packet, seal, labels = _packet_and_labels()
        changed = deepcopy(packet)
        changed["exported_test_cases"][0]["steps"][0]["test_data"] = "changed literal"
        with self.assertRaises(ValueError):
            validate_labels(changed, seal, labels)
        labels["sample_id"] = "OTHER-SAMPLE"
        with self.assertRaises(ValueError):
            validate_labels(packet, seal, labels)
        labels["sample_id"] = packet["sample_id"]
        labels["packet_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_labels(packet, seal, labels)

    def test_rejects_a_missing_judgment_including_a_deferred_obligation(self):
        for omitted in ("durability", "threshold"):
            with self.subTest(omitted=omitted):
                packet, seal, labels = _packet_and_labels()
                del labels["judgments"][omitted]
                with self.assertRaises(ValueError):
                    validate_labels(packet, seal, labels)

    def test_requires_actual_distinct_step_citations_for_covered_design(self):
        for citations in (
            [],
            [{"case_id": "TC-001", "step": 99}],
            [{"case_id": "OTHER-CASE", "step": 2}],
            [{"case_id": "TC-001", "step": True}],
            [{"case_id": "TC-001", "step": 2}, {"case_id": "TC-001", "step": 2}],
        ):
            with self.subTest(citations=citations):
                packet, seal, labels = _packet_and_labels()
                labels["judgments"]["permission"]["citations"] = citations
                with self.assertRaises(ValueError):
                    validate_labels(packet, seal, labels)

    def test_rejects_a_citation_to_a_different_requirement(self):
        packet, seal, labels = _packet_and_labels()
        labels["judgments"]["permission"]["source_id"] = "R2"
        with self.assertRaisesRegex(ValueError, "Source citation differs"):
            validate_labels(packet, seal, labels)


class PairedCoverageTests(unittest.TestCase):
    def slots(self, baseline_covered, candidate_covered, *, critical_miss=False):
        def review(covered, critical=False):
            statuses = {f"rule-{index}": "covered" if index < covered else "unknown" for index in range(20)}
            return {
                "in_scope": len(statuses),
                "in_scope_status": statuses,
                "covered": covered,
                "unknown": len(statuses) - covered,
                "missed_critical": ["rule-19"] if critical else [],
                "deferred": {"deferred-rule": "covered"},
            }

        manifest = json.loads(adjudication.FROZEN_MANIFEST.read_bytes())
        reviews = {"baseline": review(baseline_covered), "candidate": review(candidate_covered, critical_miss)}
        return [
            {"workflow": workflow, "repeat": repeat, "arm": arm, "status": "delivered", "review": deepcopy(reviews[arm])}
            for workflow in manifest["workflow_index"]
            for repeat in (1, 2, 3)
            for arm in adjudication.ARMS
        ]

    def test_pools_exact_paired_counts_at_frozen_threshold(self):
        slots = self.slots(18, 19)
        coverage = adjudication.paired_coverage(slots, "live")
        self.assertEqual(coverage["gate"]["status"], "pass")
        self.assertEqual(coverage["pooled"]["matched_pairs"], 36)
        self.assertEqual(coverage["pooled"]["covered_delta"], 36)
        self.assertEqual(
            coverage["pooled"]["arms"]["candidate"], {"in_scope": 720, "covered": 684, "unknown": 36, "missed_critical": 0, "coverage_percent": 95.0}
        )
        self.assertEqual(coverage["pairs"][0]["candidate_only"], ["rule-18"])
        self.assertTrue(all(workflow["covered_delta"] == 3 for workflow in coverage["workflows"].values()))

    def test_a_complete_pair_set_must_meet_each_numeric_gate(self):
        for check, slots in (
            ("minimum_candidate_coverage", self.slots(18, 18)),
            ("no_missed_critical", self.slots(19, 19, critical_miss=True)),
            ("no_pooled_regression", self.slots(20, 19)),
        ):
            with self.subTest(check=check):
                gate = adjudication.paired_coverage(slots, "live")["gate"]
                self.assertEqual(gate["status"], "fail")
                self.assertFalse(gate["checks"][check])
                self.assertTrue(all(passed for name, passed in gate["checks"].items() if name != check))

    def test_preserves_unpaired_runs_and_never_gates_rehearsal(self):
        slots = self.slots(20, 20)
        self.assertEqual(adjudication.paired_coverage(slots, "rehearsal")["gate"]["status"], "rehearsal")
        slots[0].update(review=None, status="failed")
        slots[3].update(review=None)
        coverage = adjudication.paired_coverage(slots, "live")
        self.assertEqual(coverage["gate"]["status"], "incomplete")
        self.assertIsNone(coverage["gate"]["checks"])
        self.assertEqual(len(coverage["pairs"]), 36)
        self.assertEqual(coverage["pooled"]["matched_pairs"], 34)
        self.assertEqual(coverage["pooled"]["arms"]["candidate"]["in_scope"], 680)
        self.assertEqual(coverage["pairs"][0]["arms"]["baseline"], {"status": "failed", "labeled": False})
        self.assertEqual(coverage["pairs"][1]["arms"]["candidate"], {"status": "delivered", "labeled": False})
        self.assertIsNone(coverage["pairs"][0]["coverage"])

    def test_rejects_matched_labels_for_different_obligation_sets(self):
        slots = self.slots(20, 20)
        status = slots[1]["review"]["in_scope_status"]
        status["another-rule"] = status.pop("rule-0")
        with self.assertRaisesRegex(ValueError, "Paired obligation scopes differ"):
            adjudication.paired_coverage(slots, "live")


class PairedPacketTests(unittest.TestCase):
    def test_prepares_both_source_change_checkpoints_with_reviewed_successor(self):
        manifest = json.loads(adjudication.FROZEN_MANIFEST.read_bytes())
        workflow = "W11-source-update"
        schedule = [(name, repeat, arm) for name in manifest["workflow_index"] for repeat in (1, 2, 3) for arm in ("baseline", "candidate")]
        original = adjudication.markdown_source(REQUIREMENT_ROWS)
        revised = original.replace("unspecified", "173 EUR")
        source = {"requirements": REQUIREMENT_ROWS, "source_document": original, "revised_source_document": revised}
        key = {"obligations": {}, "approved_scope": None}
        before = _delivery()
        after = _delivery(revision_offset=10)
        project = after[0]
        suite = _reviewed_suite(project)
        payload = project["current_snapshots"]["requirements"]["payload"]
        payload["import_id"] = "import-revised"
        changed = payload["requirements"][1]
        changed["text"] = changed["text"].replace("unspecified", "173 EUR")
        changed["sources"].append(
            {
                **changed["sources"][0],
                "source_version": sha256(revised.encode()).hexdigest(),
                "import_id": "import-revised",
                "excerpt": changed["text"],
            }
        )
        commit = "1" * 40
        run = {
            "workflow": workflow,
            "repeat": 1,
            "arm": "candidate",
            "input_sha256": digest(source),
            "suite_snapshot_id": suite["snapshot_id"],
            "case_count": 2,
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            before_files = _capture_files(root / "before", dict(zip(adjudication.CAPTURES, before, strict=True)))
            after_files = _capture_files(root / "after", {**dict(zip(adjudication.CAPTURES, after, strict=True)), "run": run})
            intake = root / "intake.json"
            record = {**run, "commit": commit, "cohort": "rehearsal", "status": "delivered", "before_files": before_files, "files": after_files}
            intake.write_bytes(adjudication.json_bytes({"cohort": "rehearsal", "candidate_commit": commit, "records": [record]}))
            frozen = (manifest, {workflow: {"generation_input": source}}, {workflow: key}, schedule)
            with patch.object(adjudication, "frozen_inputs", return_value=frozen):
                output = root / "package"
                self.assertEqual(adjudication.prepare(root, intake, output), {"delivered": 1, "unrun": 71})
            (reviewer_file,) = (output / "reviewer").glob("*.json")
            packet = json.loads(reviewer_file.read_bytes())["packet"]
            self.assertEqual(len(packet["before"]["saved_test_cases"]), 2)
            self.assertEqual(len(packet["saved_test_cases"]), 2)

        with self.assertRaisesRegex(ValueError, "REQ-002 lacks an exact frozen source reference"):
            adjudication.check_saved_source(project, original, previous=(before[0], original))
        payload["requirements"][1] = before[0]["current_snapshots"]["requirements"]["payload"]["requirements"][1]
        with self.assertRaisesRegex(ValueError, "REQ-002 lacks an exact frozen source reference"):
            adjudication.check_saved_source(project, revised, previous=(before[0], original))

    def test_keeps_all_scheduled_slots_and_separates_private_from_reviewer_evidence(self):
        manifest = json.loads(adjudication.FROZEN_MANIFEST.read_bytes())
        workflow = "W05-missing-prerequisites"
        packet, _, _ = _packet_and_labels()
        key = {"obligations": packet["obligations"], "approved_scope": {"deferred_scope_checks": ["threshold"]}}
        schedule = [(name, repeat, arm) for name in manifest["workflow_index"] for repeat in (1, 2, 3) for arm in ("baseline", "candidate")]
        pins = {"baseline": manifest["baseline"], "candidate": "1" * 40}
        with TemporaryDirectory() as folder:
            root = Path(folder)
            captures = {}
            identity = {"workflow": workflow, "arm": "baseline", "input_sha256": digest(packet["input"]), "suite_snapshot_id": "suite-1", "case_count": 2}
            for kind, evidence in zip((*adjudication.CAPTURES, "run"), (*_delivery(), identity), strict=True):
                path = root / f"{kind}.json"
                raw = adjudication.json_bytes(evidence)
                path.write_bytes(raw)
                captures[kind] = {"path": path.name, "sha256": sha256(raw).hexdigest()}
            common = {"cohort": "rehearsal", "workflow": workflow, "input_sha256": digest(packet["input"])}
            intake = root / "intake.json"
            intake.write_bytes(
                adjudication.json_bytes(
                    {
                        "cohort": "rehearsal",
                        "candidate_commit": pins["candidate"],
                        "records": [
                            {**common, "arm": "baseline", "repeat": 1, "commit": pins["baseline"], "status": "delivered", "files": captures},
                            {**common, "arm": "candidate", "repeat": 1, "commit": pins["candidate"], "status": "partial", "reason": "Export timed out"},
                            {**common, "arm": "baseline", "repeat": 2, "commit": pins["baseline"], "status": "delivered", "files": captures},
                            {**common, "arm": "candidate", "repeat": 2, "commit": pins["candidate"], "status": "delivered", "files": {}},
                        ],
                    }
                )
            )
            output = root / "package"
            frozen = (manifest, {workflow: {"generation_input": packet["input"]}}, {workflow: key}, schedule)
            with patch.object(adjudication, "frozen_inputs", return_value=frozen):
                self.assertEqual(adjudication.prepare(root, intake, output), {"delivered": 1, "partial": 1, "invalid_evidence": 2, "unrun": 68})

            (reviewer_file,) = (output / "reviewer").glob("*.json")
            envelope = json.loads(reviewer_file.read_bytes())
            sample = envelope["packet"]["sample_id"]
            self.assertEqual(reviewer_file.stem, sample)
            self.assertEqual(envelope["packet_sha256"], digest(envelope["packet"]))
            for identifying_text in (pins["baseline"], "private-generation-pass", "project-1", "case-version-1", "export_audit_fingerprint"):
                self.assertNotIn(identifying_text, reviewer_file.read_text())
            labels = root / "labels"
            labels.mkdir()
            private = adjudication.summarize(output, labels)
            self.assertEqual(len(private["slots"]), 72)
            self.assertTrue(all(slot["review"] is None for slot in private["slots"]))
            self.assertEqual(private["business_coverage"]["gate"]["status"], "rehearsal")
            self.assertEqual(private["business_coverage"]["pooled"]["matched_pairs"], 0)
            private["cohort"] = "live"
            (output / "private-ledger.json").write_bytes(adjudication.json_bytes(private))
            with self.assertRaisesRegex(ValueError, "Delivered capture differs from the ledger cohort"):
                adjudication.summarize(output, labels)
            private["cohort"] = "rehearsal"
            swapped = next(slot for slot in private["slots"] if slot["workflow"] == workflow and slot["arm"] == "baseline" and slot["repeat"] == 2)
            self.assertIn("Runner identity differs", swapped["reason"])

            project_path = root / captures["project"]["path"]
            project = json.loads(project_path.read_bytes())
            for requirement in project["current_snapshots"]["requirements"]["payload"]["requirements"]:
                requirement["sources"][0]["source_version"] = "0" * 64
            raw = adjudication.json_bytes(project)
            project_path.write_bytes(raw)
            wrong_source = json.loads(intake.read_bytes())
            wrong_source["records"][0]["files"]["project"]["sha256"] = sha256(raw).hexdigest()
            intake.write_bytes(adjudication.json_bytes(wrong_source))
            with patch.object(adjudication, "frozen_inputs", return_value=frozen):
                rejected = root / "rejected"
                self.assertEqual(adjudication.prepare(root, intake, rejected), {"partial": 1, "invalid_evidence": 3, "unrun": 68})
            self.assertEqual(list((rejected / "reviewer").iterdir()), [])
            invalid = json.loads((rejected / "private-ledger.json").read_bytes())
            mismatched = next(slot for slot in invalid["slots"] if slot["workflow"] == workflow and slot["arm"] == "baseline" and slot["repeat"] == 1)
            self.assertIn("lacks an exact frozen source reference", mismatched["reason"])

            delivered = next(slot for slot in private["slots"] if slot["status"] == "delivered")
            delivered["status"] = "delivred"
            (output / "private-ledger.json").write_bytes(adjudication.json_bytes(private))
            with self.assertRaisesRegex(ValueError, "Invalid outcome"):
                adjudication.summarize(output, labels)
            delivered["status"] = "delivered"
            private["slots"].pop()
            (output / "private-ledger.json").write_bytes(adjudication.json_bytes(private))
            with self.assertRaisesRegex(ValueError, "frozen schedule"):
                adjudication.summarize(output, labels)
