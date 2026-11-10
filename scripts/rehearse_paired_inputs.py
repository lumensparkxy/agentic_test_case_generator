#!/usr/bin/env python3
"""Rehearse the frozen 2.0 source and scope routes without a model or worker."""

import argparse
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import re
from unittest.mock import patch
from uuid import uuid4

from prepare_case_adjudication import FROZEN_MANIFEST, digest, exact, firestore_fields, json_bytes, markdown_source, read_json, require


DEMO_PROJECTS = {"baseline": "demo-tcg-workbench", "candidate": "demo-tcg-v2-continuation"}
FROZEN_FIELDS = {"requirements", "context", "template", "healthy_target_fixture", "source_document", "revised_source_document", "revised_context"}
CONTEXT_FIELDS = {"requirements", "notes", "execution_prerequisites", "grounded_context"}


def generation_inputs(directory):
    """Read the two generation manifests only. Adjudication stays in a separate process."""
    manifest = read_json(FROZEN_MANIFEST.read_bytes())
    workflows = {}
    for split in ("calibration_inputs", "held_out_inputs"):
        raw = (directory / f"{split.replace('_', '-')}.json").read_bytes()
        require(sha256(raw).hexdigest() == manifest["content_hashes"][split], f"Changed frozen {split}")
        part = read_json(raw)
        require(part["baseline"] == manifest["baseline"] and not workflows.keys() & part["workflows"].keys(), "Changed frozen corpus identity")
        workflows.update(part["workflows"])
    require(workflows.keys() == manifest["workflow_index"].keys(), "Missing or additional workflows")
    for name, item in workflows.items():
        source = item["generation_input"]
        require(set(source) <= FROZEN_FIELDS and set(source["context"]) <= CONTEXT_FIELDS, "Unexpected generation input field")
        require(digest(source) == manifest["workflow_index"][name]["generation_input_sha256"], "Changed frozen workflow")
    return manifest, workflows


def revised_rows(document, original):
    # The frozen Room Booking source has one labeled Markdown section per rule.
    sections = re.split(r"(?m)^## (ROOM-\d+) — [^\n]+\n", document)[1:]
    rows = [{"id": identity, "text": body.strip()} for identity, body in zip(sections[::2], sections[1::2], strict=True)]
    require([r["id"] for r in rows] == [r["id"] for r in original], "Revised source changed the frozen requirement IDs")
    return rows


def exact_saved_rows(project, frozen_rows, document, name):
    requirements = project["current_snapshots"]["requirements"]["payload"]["requirements"]
    require(len(requirements) == len(frozen_rows), "Saved requirements dropped or added a row")
    require(len({row["requirement_uid"] for row in requirements}) == len(requirements), "Saved requirement UIDs are ambiguous")
    version = sha256(document.encode()).hexdigest()
    mappings = {}
    for row, source in zip(requirements, frozen_rows, strict=True):
        require(row["text"] == source["text"], f"Saved requirement differs for {source['id']}")
        quotes = [ref for ref in row["sources"] if ref["label"] == name and ref["source_version"] == version]
        require(len(quotes) == 1 and quotes[0]["excerpt_verified"], f"Unverified source for {source['id']}")
        quote = quotes[0]
        excerpt = source["text"].strip()
        start = document.index(excerpt)
        first = document.count("\n", 0, start) + 1
        last = first + excerpt.count("\n")
        lines = f"Lines {first}-{last}"
        require(quote["excerpt"] == excerpt and quote["source_section"] == lines, f"Saved quotation differs for {source['id']}")
        mappings[source["id"]] = {key: row.get(key) for key in ("id", "requirement_uid", "content_version", "review_status", "clarification_status")}
        mappings[source["id"]]["source"] = quote
    return mappings


def context_payload(context, frozen_rows, saved_rows):
    selected = deepcopy(context)
    if "requirements" in selected:
        require(exact(selected.pop("requirements"), frozen_rows), "Frozen Context refers to different requirements")
    if "execution_prerequisites" in selected:
        identities = {row["id"]: saved["id"] for row, saved in zip(frozen_rows, saved_rows, strict=True)}
        for item in selected["execution_prerequisites"]:
            item["requirement_ids"] = [identities[identity] for identity in item["requirement_ids"]]
    return selected


def matches_supplied(expected, actual):
    """Compare supplied fields without mistaking typed response defaults for source data."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and matches_supplied(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(matches_supplied(left, right) for left, right in zip(expected, actual, strict=True))
        )
    return exact(expected, actual)


class InputRehearsal:
    def __init__(self, client, requirements_router, arm, workflow):
        self.client = client
        self.requirements_router = requirements_router
        self.arm = arm
        self.routes = []
        self.name = f"{workflow}.md"
        project = self.call("POST", "/projects", json={"name": f"Frozen input rehearsal {workflow}"})
        self.prefix = f"/projects/{project['project_id']}"

    def request(self, method, path, *, request=None, **kwargs):
        request = request or uuid4().hex
        response = self.client.request(method, path, headers={"X-Request-ID": request}, **kwargs)
        self.routes.append({"method": method, "path": path, "status": response.status_code, "request_id": request})
        return response

    def call(self, method, path, **kwargs):
        response = self.request(method, path, **kwargs)
        require(response.is_success, f"{method} {path} failed: {response.status_code} {response.text[:600]}")
        return response.json()

    def read(self):
        return self.call("GET", self.prefix)

    def apply_source(self, project, rows, document, prior=None):
        raw = f"--- SOURCE: {self.name} ---\n{document}"

        def extracted(raw_text, *_args, **_kwargs):
            require(raw_text == raw, "App extraction received different upload bytes")
            return {
                "requirements": [{**row, "source_excerpt": row["text"], "source_path": self.name, "review_status": "Needs Review"} for row in rows],
                "approved": False,
                "review": {"approved": False, "summary": "Frozen offline extraction fixture"},
                "iteration_history": [],
                "coverage_metrics": {},
            }

        with patch.object(self.requirements_router, "extract_requirements", side_effect=extracted) as extraction:
            preview = self.call(
                "POST",
                "/requirements/parse",
                data={"project_id": project["project_id"], "base_project_revision": project["current_revision"], "import_mode": "review"},
                files={"file": (self.name, document.encode(), "text/markdown")},
            )
            require(extraction.call_count == 1, "Expected one deterministic extraction")
        path = f"{self.prefix}/requirement-imports/{preview['import_id']}"
        loaded = self.call("GET", path)
        require(exact(preview, loaded) and loaded["status"] == "pending" and not loaded["source_evidence_findings"], "Saved import preview differs")
        candidates = loaded["candidates"]
        require([item["requirement"]["text"] for item in candidates] == [row["text"] for row in rows], "Comparison does not retain the extracted rows")
        decisions = []
        for item, row in zip(candidates, rows, strict=True):
            decision = {"candidate_id": item["candidate_id"], "action": "add"}
            if prior:
                previous = prior[row["id"]]
                target = previous["requirement_uid"]
                changed = row["text"] != previous["source"]["excerpt"]
                if changed:
                    require(target in {suggestion["requirement_uid"] for suggestion in item["suggestions"]}, "Source revision lost its verified comparison")
                else:
                    require(item["classification"] == "unchanged" and item["target_requirement_uid"] == target, "Unchanged source lost its comparison")
                decision.update(action="update" if changed else "keep", target_requirement_uid=target)
            else:
                require(item["classification"] == "new", "First import unexpectedly changes a requirement")
            decisions.append(decision)
        applied = self.call(
            "POST", f"{path}/apply", json={"base_project_revision": loaded["base_project_revision"], "idempotency_key": uuid4().hex, "decisions": decisions}
        )
        require(applied["current_revision"] == project["current_revision"] + 1, "Import revision differs")
        return applied, exact_saved_rows(applied, rows, document, self.name)

    def defer_ambiguous_scope(self, project, requirement_id):
        finding = self.call(
            "POST",
            f"{self.prefix}/quality-findings",
            json={
                "base_project_revision": project["current_revision"],
                "source_snapshot_id": project["current_snapshots"]["requirements"]["snapshot_id"],
                "category": "ambiguous_requirement",
                "summary": "The configured expense threshold is unspecified",
                "expected_behavior": "Use an authoritative threshold before testing the unresolved rule",
                "requirement_ids": [requirement_id],
            },
        )
        self.call(
            "POST",
            f"{self.prefix}/quality-findings/{finding['finding']['id']}",
            json={"base_project_revision": finding["project_revision"], "action": "defer", "reason": "The frozen source leaves the amount unspecified."},
        )
        return self.read()

    def context(self, project, frozen_rows, context):
        rows = project["current_snapshots"]["requirements"]["payload"]["requirements"]
        payload = context_payload(context, frozen_rows, rows)
        response = self.call(
            "POST",
            "/requirements/enrich",
            json={**payload, "project_id": project["project_id"], "base_project_revision": project["current_revision"], "requirements": rows},
        )
        project = self.read()
        saved = project["current_snapshots"]["context"]["payload"]
        require(not project["stage_state"]["context"]["stale"], "Fresh Context is stale")
        for key in ("notes", "execution_prerequisites", "grounded_context"):
            require(exact(firestore_fields(saved.get(key)), firestore_fields(response.get(key))), f"Saved {key} differs from the response")
        require(saved.get("notes") == payload.get("notes"), "Frozen Context notes changed")
        for expected, actual in zip(payload.get("execution_prerequisites", []), saved["execution_prerequisites"], strict=True):
            require(all(exact(value, actual.get(key)) for key, value in expected.items()), "Frozen prerequisite changed")
        if "grounded_context" in payload:
            require(matches_supplied(payload["grounded_context"], response["grounded_context"]), "Frozen grounded Context changed")
        return project

    def approve(self, project):
        rows = project["current_snapshots"]["requirements"]["payload"]["requirements"]
        reviews = [
            {"requirement_uid": row["requirement_uid"], "review_status": "Approved"}
            for row in rows
            if row["review_status"] != "Approved" and row.get("clarification_status") != "deferred"
        ]
        key = uuid4().hex
        payload = {"base_project_revision": project["current_revision"], "idempotency_key": key, "reviews": reviews}
        if self.arm == "baseline":
            return self.call("PATCH", f"{self.prefix}/requirements/reviews", json=payload, request=key), None
        decision = self.call("POST", f"{self.prefix}/requirements/approve-and-prepare-scenarios", json=payload, request=key)
        run = decision["continuation"]
        require(run["state"] == "queued", "Exact candidate approval did not queue preparation")
        receipt = self.call("GET", f"{self.prefix}/approval-continuations", params={"request_id": key, "stage": "prepare_scenarios"})["receipt"]
        require(receipt["run_id"] == run["run_id"], "Candidate approval receipt changed")
        stopped = self.call("POST", f"{self.prefix}/approval-continuations/{run['run_id']}/cancel")
        require(stopped["state"] == "cancelled", "Offline continuation was not cancelled")
        recovered = self.call("GET", f"{self.prefix}/approval-continuations", params={"request_id": key, "stage": "prepare_scenarios"})
        saved_run = next((item for item in recovered["runs"] if item["run_id"] == run["run_id"]), None)
        require(
            recovered["receipt"]["run_id"] == run["run_id"] and saved_run and saved_run["state"] == "cancelled" and not saved_run.get("result_snapshot_id"),
            "Offline cancellation was not recoverable from its exact receipt",
        )
        return self.read(), {"request_id": key, "run_id": run["run_id"], "states": ["queued", "cancelled"]}

    def checkpoint(self, project, rows, document, context, deferred):
        if self.arm == "candidate":
            project = self.context(project, rows, context)
        project, continuation = self.approve(project)
        if self.arm == "baseline":
            project = self.context(project, rows, context)
        return {
            "project": project,
            "source_sha256": sha256(document.encode()).hexdigest(),
            "frozen_to_saved": exact_saved_rows(project, rows, document, self.name),
            "continuation": continuation,
            "deferred_source_ids": deferred,
        }


def verify_scope(checkpoint, rows, scope, project_model, context_model, prepare_context_evidence):
    project = project_model.model_validate(checkpoint["project"])
    approved = scope(project)
    expected = [row["text"] for row in rows if row["id"] not in checkpoint["deferred_source_ids"]]
    require([row.text for row in approved] == expected, "Approved generation scope differs from frozen source")
    for identity in checkpoint["deferred_source_ids"]:
        require(checkpoint["frozen_to_saved"][identity]["clarification_status"] == "deferred", "Missing explicit scope deferral")
    saved = project.current_snapshots["context"].payload
    evidence = prepare_context_evidence(approved, context_model.model_validate(saved), budget=256_000)
    require(len(evidence) <= 256_000, "Context selector exceeded the frozen budget")
    require(not project.current_snapshots.get("use_cases") and not project.current_snapshots.get("test_cases"), "Generation ran during input rehearsal")
    checkpoint["approved_source_ids"] = [row["id"] for row in rows if row["id"] not in checkpoint["deferred_source_ids"]]
    checkpoint["selected_context"] = read_json(evidence)


def run_workflow(client, requirements_router, arm, workflow, source):
    rehearsal = InputRehearsal(client, requirements_router, arm, workflow)
    rows = source["requirements"]
    document = source.get("source_document", markdown_source(rows))
    project, imported = rehearsal.apply_source(rehearsal.read(), rows, document)
    require(all(row["review_status"] != "Approved" for row in imported.values()), "Import approved requirements without a decision")
    # W05 explicitly defers the ambiguous amount. No adjudication keys are read.
    deferred = ["R2"] if workflow == "W05-missing-prerequisites" else []
    for identity in deferred:
        project = rehearsal.defer_ambiguous_scope(project, imported[identity]["id"])
    before = rehearsal.checkpoint(project, rows, document, source["context"], deferred)
    checkpoints = [(before, rows)]
    if "revised_source_document" in source:
        revised = source["revised_source_document"]
        current = revised_rows(revised, rows)
        previous = before["frozen_to_saved"]
        changed, mapping = rehearsal.apply_source(before["project"], current, revised, previous)
        require(changed["stage_state"]["context"]["stale"], "Source change left old Context current")
        histories = {}
        for row, initial in zip(current, rows, strict=True):
            identity = row["id"]
            prior, successor = previous[identity], mapping[identity]
            updated = row["text"] != initial["text"]
            require(prior["requirement_uid"] == successor["requirement_uid"], "Source revision changed requirement identity")
            require(successor["content_version"] == prior["content_version"] + int(updated), "Source revision changed an unrelated content version")
            require((successor["review_status"] == "Approved") == (not updated), "Source revision kept a stale decision or lost an unaffected decision")
            if updated:
                history = rehearsal.call("GET", f"{rehearsal.prefix}/requirements/{prior['requirement_uid']}/history")
                require(
                    any(
                        entry["requirement"]["content_version"] == prior["content_version"] and entry["requirement"]["text"] == initial["text"]
                        for entry in history
                    ),
                    "Revised requirement lost its original saved history",
                )
                histories[identity] = history
        after = rehearsal.checkpoint(changed, current, revised, source["revised_context"], deferred)
        after["preapproval_frozen_to_saved"] = mapping
        after["changed_requirement_history"] = histories
        checkpoints.append((after, current))
    return checkpoints, rehearsal.routes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", required=True, type=Path)
    parser.add_argument("--arm", required=True, choices=DEMO_PROJECTS)
    parser.add_argument("--app-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest, workflows = generation_inputs(args.frozen)
    pin = args.app_commit
    require(re.fullmatch(r"[a-f0-9]{40}", pin), "An exact app commit is required")
    require((pin == manifest["baseline"]) is (args.arm == "baseline"), "App pin and arm disagree")
    emulator = re.fullmatch(r"127\.0\.0\.1:([1-9][0-9]{0,4})", os.environ.get("FIRESTORE_EMULATOR_HOST", ""))
    require(emulator and int(emulator[1]) <= 65535, "A loopback Firestore emulator is required")
    require(os.environ.get("FIREBASE_PROJECT_ID") == DEMO_PROJECTS[args.arm], "Wrong disposable demo project")
    require(os.environ.get("TCG_APPROVAL_SCENARIOS_ENABLED") == ("true" if args.arm == "candidate" else "false"), "Wrong continuation policy for arm")
    isolated = {
        "AUTH_TOKEN_MODE": "firebase-or-backend-jwt",
        "ADK_SKILLS_ENABLED": "false",
        "ADK_MEMORY_ENABLED": "false",
        "BILLING_SHADOW_MODE": "true",
        "PYTHON_DOTENV_DISABLED": "1",
    }
    require(all(os.environ.get(key) == value for key, value in isolated.items()), "Start with the provider-free rehearsal environment")
    provider_keys = ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_API_KEY", "GEMINI_API_KEY")
    require(not any(os.environ.get(name) for name in provider_keys), "Provider credentials are outside this rehearsal")

    import firebase_admin
    from fastapi.testclient import TestClient
    from google.auth.credentials import AnonymousCredentials
    from google.genai._api_client import BaseApiClient

    class EmulatorCredential(firebase_admin.credentials.Base):
        def get_credential(self):
            return AnonymousCredentials()

    firebase_admin.initialize_app(EmulatorCredential(), {"projectId": DEMO_PROJECTS[args.arm]})
    with patch.object(BaseApiClient, "__init__", side_effect=AssertionError("Provider construction is forbidden during input rehearsal")) as provider:
        from app.agents.context_evidence import prepare_context_evidence
        from app.auth.jwt_auth import create_access_token
        from app.contracts.grounding import EnrichInput
        from app.main import app
        from app.models import AuthUser, QaProjectDetail
        from app.routers import requirements
        from app.services.requirement_scope import approved_generation_requirements

        output = {}
        for workflow, item in workflows.items():
            actor = AuthUser(sub=f"paired-input-{uuid4().hex}", name="Frozen input rehearsal", email="paired-input@example.test")
            token, _ = create_access_token(actor)
            with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
                checkpoints, routes = run_workflow(client, requirements, args.arm, workflow, item["generation_input"])
            for checkpoint, rows in checkpoints:
                verify_scope(checkpoint, rows, approved_generation_requirements, QaProjectDetail, EnrichInput, prepare_context_evidence)
            output[workflow] = {"input_sha256": item["generation_input_sha256"], "checkpoints": [item[0] for item in checkpoints], "routes": routes}
            print(f"PASS {args.arm} {workflow} ({len(item['generation_input']['requirements'])} frozen requirements)", flush=True)
        require(provider.call_count == 0, "Provider construction was attempted")
    with args.output.open("xb") as evidence:
        evidence.write(json_bytes({"cohort": "input_rehearsal", "arm": args.arm, "commit": pin, "provider_attempts": 0, "workflows": output}))


if __name__ == "__main__":
    main()
