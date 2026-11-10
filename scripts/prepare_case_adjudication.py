#!/usr/bin/env python3
"""Prepare blinded human review of the frozen 2.0 saved/exported artifacts offline."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from secrets import token_hex


FROZEN_MANIFEST = Path(__file__).resolve().parents[1] / "docs/test-case-generator-2-corpus-manifest.json"
# Exact test_cases_v1 case projection. Keep saved prerequisites alongside it.
EXPORT_FIELDS = frozenset(
    "id title description priority type status preconditions steps expected_result test_data estimated_time "
    "automation_status component linked_requirement_ids scenario_refs source_refs tags".split()
)
SAVED_VIEW_FIELDS = EXPORT_FIELDS | {"execution_prerequisites"}
STEP_FIELDS = {"step", "action", "expected", "test_data"}
VERSION_FIELDS = ("artifact_item_id", "artifact_version_id", "artifact_version_number")
SOURCES = ("requirements", "context", "use_cases", "test_cases")
CAPTURES = ("project", "hydrated", "exported", "report")
INCOMPLETE_OUTCOMES = {"failed", "partial", "blocked", "interrupted"}
ARMS = ("baseline", "candidate")
FROZEN_PAIRS = 36
MIN_COVERAGE_PERCENT = 95
FACT_KINDS = ("rule", "account", "record", "locator", "outcome", "other")
FACT_VERDICTS = ("supported", "unsupported", "unknown")
CASE_FACT_FIELDS = {"title", "description", "preconditions", "test_data", "expected_result", "execution_prerequisites"}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(value):
    return sha256(json_bytes(value)).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(raw):
    def fields(pairs):
        row = dict(pairs)
        require(len(row) == len(pairs), "Duplicate JSON object field")
        return row

    def invalid_constant(_value):
        raise ValueError("Non-finite JSON value")

    return json.loads(raw, object_pairs_hook=fields, parse_constant=invalid_constant)


def exact(left, right):
    # Unlike Python equality this distinguishes true, 1, and 1.0.
    return json_bytes(left) == json_bytes(right)


def markdown_source(rows):
    return "\n\n".join(f"## {row['id']}\n{row['text']}" for row in rows) + "\n"


def cited_lines(ref, import_id, version, lines):
    if (
        ref.get("source_system") != "file"
        or ref.get("import_id") != import_id
        or ref.get("source_version") != version
        or ref.get("excerpt_verified") is not True
    ):
        return None
    excerpt = ref.get("excerpt")
    span = re.fullmatch(r"Lines ([1-9][0-9]*)-([1-9][0-9]*)", ref.get("source_section") or "")
    if not isinstance(excerpt, str) or not excerpt.strip() or not span:
        return None
    first, last = map(int, span.groups())
    if last <= len(lines) and last - first == excerpt.count("\n") and excerpt in "\n".join(lines[first - 1 : last]):
        return slice(first - 1, last)
    return None


def check_saved_source(project, document, *, previous=None):
    payload = project["current_snapshots"]["requirements"]["payload"]
    import_id = payload["import_id"]
    require(isinstance(import_id, str) and import_id, "Saved requirements lack an import identity")
    version = sha256(document.encode()).hexdigest()
    lines = document.split("\n")
    prior_rows = {}
    if previous:
        prior_project, prior_document = previous
        prior = prior_project["current_snapshots"]["requirements"]["payload"]
        prior_rows = {row["id"]: row for row in prior["requirements"]}
        prior_import = prior["import_id"]
        require(prior_import != import_id, "Revised requirements reuse the original import")
        prior_version = sha256(prior_document.encode()).hexdigest()
        prior_lines = prior_document.split("\n")

    current_count = 0
    for row in payload["requirements"]:
        refs = row.get("sources", [])
        if any(cited_lines(ref, import_id, version, lines) for ref in refs):
            current_count += 1
            continue
        # A W11 reimport can leave unaffected requirements exactly as they were.
        if row["id"] in prior_rows and exact(row, prior_rows[row["id"]]):
            spans = (cited_lines(ref, prior_import, prior_version, prior_lines) for ref in refs)
            if any(span is not None and prior_lines[span] == lines[span] for span in spans):
                continue
        raise ValueError(f"Saved requirement {row['id']} lacks an exact frozen source reference")
    require(current_count > 0, "Saved requirements lack a reference from the current frozen import")


def firestore_fields(value):
    """Match persisted dictionaries that omit nulls during snapshot writes."""
    if isinstance(value, dict):
        return {key: firestore_fields(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [firestore_fields(item) for item in value]
    return value


def check_suite_parent(project, suite, plan_id):
    parent_id = suite["source_snapshot_id"]
    if suite["operation"] != "test_cases.review":
        require(parent_id == plan_id, "Mismatched suite plan")
        return
    parent = max(
        (
            event
            for event in project["timeline"]
            if event["event_type"] == "stage.snapshot_created"
            and event["stage"] == "test_cases"
            and event["project_id"] == project["project_id"]
            and event["project_revision"] < suite["project_revision"]
        ),
        key=lambda event: event["project_revision"],
        default=None,
    )
    require(
        parent_id
        and parent
        and parent["snapshot_id"] == parent_id
        and project["stage_state"]["test_cases"]["source_snapshot_id"] == parent_id
        and suite["metadata"].get("source_test_cases_snapshot_id", parent_id) == parent_id,
        "Reviewed suite does not name its preceding saved Test Cases snapshot",
    )


def checked_delivery(project, hydrated, exported, report):
    """Check the saved suite, exact case reads, export and saved export report."""
    try:
        snapshots = project["current_snapshots"]
        suite = snapshots["test_cases"]
        source_ids = {name: snapshots[name]["snapshot_id"] for name in SOURCES if snapshots.get(name)}
        for stage in source_ids:
            state = project["stage_state"][stage]
            require(state["current_snapshot_id"] == source_ids[stage] and state["stale"] is False, f"Stale or mismatched {stage} state")
            require(snapshots[stage]["project_id"] == project["project_id"], f"Mismatched {stage} project")
        requirements = snapshots["requirements"]["payload"]["requirements"]
        require(any(row.get("review_status") == "Approved" for row in requirements), "Missing approved requirements")
        plan_snapshot = snapshots["use_cases"]
        require(
            plan_snapshot["source_snapshot_id"] == source_ids["requirements"]
            and plan_snapshot["metadata"]["source_requirements_snapshot_id"] == source_ids["requirements"]
            and plan_snapshot["metadata"].get("source_context_snapshot_id") == source_ids.get("context"),
            "Mismatched scenario plan lineage",
        )
        check_suite_parent(project, suite, source_ids["use_cases"])
        plan = project["stage_state"]["use_cases"]
        approval = plan["metadata"]["latest_human_review"]
        require(
            plan["approved"] is True and approval["decision"] == "approve" and approval["review_id"] and approval["snapshot_id"] == source_ids["use_cases"],
            "Missing exact scenario approval",
        )
        suite_sources = suite["metadata"]["source_snapshot_ids"]
        audit = exported["audit_metadata"]
        require(audit["project_id"] == project["project_id"] and exact(audit["project_revision"], project["current_revision"]), "Mismatched export project")
        require(audit["snapshot_id"] == suite["snapshot_id"] and exact(audit["snapshot_version"], suite["version"]), "Mismatched export suite")
        for stage in SOURCES:
            require(audit["source_snapshot_ids"].get(stage) == source_ids.get(stage), f"Mismatched export {stage} source")
            if stage != "test_cases":
                require(suite_sources.get(stage) == source_ids.get(stage), f"Mismatched suite {stage} source")
        stored = suite["payload"]["test_cases"]
        delivered = exported["test_cases"]
        count = len(hydrated)
        require(
            count > 0 and exact(exported["total_count"], count) and exact(audit["delivered_case_count"], count) and len(stored) == len(delivered) == count,
            "Missing or additional saved/exported cases",
        )
        require(exported["export_format"] == "test_cases_v1", "Unexpected JSON export format")
        version_rows = []
        saved_cases = []
        for saved, row, exported_case in zip(stored, hydrated, delivered, strict=True):
            require(SAVED_VIEW_FIELDS <= row.keys(), "Exact case read is missing a design field")
            require(set(exported_case) == EXPORT_FIELDS, "Export fields differ from test_cases_v1")
            require(exact({key: row[key] for key in EXPORT_FIELDS}, exported_case), "Export case differs from exact read")
            saved_fields = SAVED_VIEW_FIELDS.union(VERSION_FIELDS)
            require(
                all(exact(firestore_fields(saved.get(key)), firestore_fields(row.get(key))) for key in saved_fields),
                "Exact case read differs from saved suite",
            )
            require(all(set(step) == STEP_FIELDS for step in row["steps"]), "Exact step fields differ from test_cases_v1")
            steps = [step["step"] for step in row["steps"]]
            require(all(type(step) is int and step > 0 for step in steps) and len(steps) == len(set(steps)), "Invalid or duplicate step number")
            version = {key: row[key] for key in VERSION_FIELDS}
            require(
                all(isinstance(version[key], str) and version[key] for key in VERSION_FIELDS[:2])
                and type(version["artifact_version_number"]) is int
                and version["artifact_version_number"] > 0,
                "Invalid saved case version",
            )
            require(isinstance(row["id"], str) and row["id"], "Invalid case ID")
            version_rows.append({"case_id": row["id"], **version})
            saved_cases.append({key: row[key] for key in SAVED_VIEW_FIELDS})
        for key in ("case_id", "artifact_item_id", "artifact_version_id"):
            require(len({row[key] for row in version_rows}) == count, f"Duplicate {key}")
        require(exact(version_rows, audit["case_versions"]), "Export case-to-version mapping differs from saved suite")
        require(
            report["project_id"] == project["project_id"]
            and report["stage"] == "reports"
            and report["operation"] == "export.json"
            and report["request_id"]
            and report["source_snapshot_id"] == suite["snapshot_id"]
            and report["payload"]["source"] == "export"
            and report["payload"]["format"] == "json"
            and exact(report["payload"]["test_case_count"], count),
            "Mismatched saved file report",
        )
        # Match the router's export_audit_fingerprint over the full JSON audit.
        audit_sha = sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest()
        require(
            report["metadata"]["export_audit_fingerprint"] == audit_sha
            and exact(firestore_fields(report["payload"]["audit_metadata"]), firestore_fields(audit)),
            "Saved file report audit differs from JSON export",
        )
        return {"saved_test_cases": saved_cases, "exported_test_cases": delivered}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Missing or malformed exact delivery evidence") from exc


def frozen_inputs(root):
    manifest = read_json(FROZEN_MANIFEST.read_bytes())
    frozen = {}
    for name, checksum in manifest["content_hashes"].items():
        raw = (root / f"{name.replace('_', '-')}.json").read_bytes()
        require(sha256(raw).hexdigest() == checksum, f"Changed frozen {name}")
        frozen[name] = read_json(raw)
    inputs = frozen["calibration_inputs"]["workflows"] | frozen["held_out_inputs"]["workflows"]
    pairs = frozen["pair_order"]["pairs"]
    schedule = [(pair["workflow"], pair["repeat"], arm) for pair in pairs for arm in pair["order"]]
    check_schedule(schedule, manifest)
    return manifest, inputs, frozen["adjudication_inputs"]["workflows"], schedule


def check_schedule(schedule, manifest):
    expected = {(workflow, repeat, arm) for workflow in manifest["workflow_index"] for repeat in (1, 2, 3) for arm in ARMS}
    require(
        len(schedule) == 2 * FROZEN_PAIRS and all(type(repeat) is int for _, repeat, _ in schedule) and set(schedule) == expected, "Unexpected frozen schedule"
    )


def captured_files(root, refs):
    """Read only explicitly hashed, local evidence paths and keep the raw hashes."""
    rows = {}
    for kind, ref in refs.items():
        path = (root / ref["path"]).resolve()
        require(path.is_relative_to(root) and not Path(ref["path"]).is_absolute(), "Capture path escapes private evidence directory")
        raw = path.read_bytes()
        require(sha256(raw).hexdigest() == ref["sha256"], f"Changed {kind} capture")
        rows[kind] = read_json(raw)
    return rows


def prepare(frozen_root, intake_path, output):
    manifest, inputs, keys, schedule = frozen_inputs(frozen_root)
    intake = read_json(intake_path.read_bytes())
    cohort = intake["cohort"]
    require(cohort in {"rehearsal", "live"}, "Use a separate rehearsal or live cohort")
    candidate = intake["candidate_commit"]
    require(re.fullmatch(r"[0-9a-f]{40}", candidate) and candidate != manifest["baseline"], "Invalid candidate commit")
    records = {}
    for row in intake["records"]:
        identity = (row["workflow"], row["repeat"], row["arm"])
        require(type(row["repeat"]) is int and identity in schedule and identity not in records, "Unknown or duplicate scheduled slot")
        records[identity] = row
    ledger = []
    packets = {}
    used = {"project": set(), "report": set()}
    root = intake_path.resolve().parent
    for workflow, repeat, arm in schedule:
        entry = {"workflow": workflow, "repeat": repeat, "arm": arm, "status": "unrun"}
        ledger.append(entry)
        record = records.get((workflow, repeat, arm))
        if record is None:
            continue
        entry["capture"] = record
        try:
            state = record["status"]
            require(state == "delivered" or state in INCOMPLETE_OUTCOMES, "Unknown run outcome")
            require(record["cohort"] == cohort, "Run belongs to a different evidence cohort")
            commit = manifest["baseline"] if arm == "baseline" else candidate
            source = inputs[workflow]["generation_input"]
            require(record["commit"] == commit and record["input_sha256"] == digest(source), "Changed commit or frozen input")
            captures = captured_files(root, record.get("files", {}))
            require(cohort != "live" or "physical_mock_requests" not in captures.get("requests", {}), "Mock requests cannot enter the live cohort")
            if state != "delivered":
                require(isinstance(record.get("reason"), str) and record["reason"].strip(), "Incomplete run needs a recorded reason")
                entry.update(status=state, reason=record["reason"])
                continue
            evidence = checked_delivery(*(captures[kind] for kind in CAPTURES))
            project_id = captures["project"]["project_id"]
            run = captures["run"]
            require(
                run["workflow"] == workflow
                and run["arm"] == arm
                and exact(run.get("repeat", 1), repeat)
                and run["input_sha256"] == record["input_sha256"]
                and run["suite_snapshot_id"] == captures["report"]["source_snapshot_id"]
                and exact(run["case_count"], len(evidence["saved_test_cases"])),
                "Runner identity differs from scheduled slot or saved suite",
            )
            if cohort == "live":
                require(
                    run["cohort"] == cohort and run["commit"] == commit and run["project_id"] == project_id and exact(run["repeat"], repeat),
                    "Live runner identity lacks the exact cohort, commit, project or repeat",
                )
            request_id = captures["report"]["request_id"]
            require(project_id not in used["project"] and request_id not in used["report"], "Delivery reuses another slot's project or export request")
            document = source.get("source_document", markdown_source(source["requirements"]))
            if "revised_source_document" in source:
                before = captured_files(root, record["before_files"])
                previous, current = before["project"], captures["project"]
                require(
                    previous["project_id"] == project_id
                    and previous["current_revision"] < current["current_revision"]
                    and previous["current_snapshots"]["requirements"]["snapshot_id"] != current["current_snapshots"]["requirements"]["snapshot_id"]
                    and before["report"]["source_snapshot_id"] != captures["report"]["source_snapshot_id"],
                    "Expected both exact source-change checkpoints in one project",
                )
                evidence["before"] = checked_delivery(*(before[kind] for kind in CAPTURES))
                check_saved_source(before["project"], document)
                check_saved_source(current, source["revised_source_document"], previous=(previous, document))
            else:
                check_saved_source(captures["project"], document)
            used["project"].add(project_id)
            used["report"].add(request_id)
            key = keys[workflow]
            sample = token_hex(16)
            packet = {
                "sample_id": sample,
                "input": source,
                "obligations": key["obligations"],
                "approved_scope": key["approved_scope"],
                "deferred_obligation_ids": (key["approved_scope"] or {}).get("deferred_scope_checks", []),
                **evidence,
            }
            seal = digest(packet)
            packets[sample] = {"packet_sha256": seal, "packet": packet}
            entry.update(status="delivered", sample_id=sample, packet_sha256=seal)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            entry.update(status="invalid_evidence", reason=str(exc))
    output.mkdir(mode=0o700)
    reviewer = output / "reviewer"
    reviewer.mkdir(mode=0o700)
    for sample in sorted(packets):
        (reviewer / f"{sample}.json").write_bytes(json_bytes(packets[sample]))
    private = {
        "protocol": manifest["protocol"],
        "cohort": cohort,
        "baseline_commit": manifest["baseline"],
        "candidate_commit": candidate,
        "schedule_sha256": manifest["content_hashes"]["pair_order"],
        "slots": ledger,
    }
    (output / "private-ledger.json").write_bytes(json_bytes(private))
    return Counter(row["status"] for row in ledger)


def contains_quote(value, quote):
    if isinstance(value, str):
        return quote in value
    if isinstance(value, dict):
        return any(contains_quote(item, quote) for item in value.values())
    if isinstance(value, list):
        return any(contains_quote(item, quote) for item in value)
    return False


def validate_fact_review(checkpoints, facts):
    require(isinstance(facts, dict) and type(facts.get("complete")) is bool, "Fact review needs an explicit completeness decision")
    claims = facts.get("claims")
    reason = facts.get("reason")
    require(isinstance(claims, list) and isinstance(reason, str) and reason.strip(), "Fact review needs claims and a reason")
    cases = {(checkpoint, case["id"]): case for checkpoint, evidence in checkpoints.items() for case in evidence["saved_test_cases"]}
    cited = set()
    seen_claims = set()
    counts = dict.fromkeys(FACT_VERDICTS, 0)
    critical_unsupported = 0
    for claim in claims:
        require(isinstance(claim, dict), "Invalid fact judgment")
        kind, verdict, critical, reason = (claim.get(key) for key in ("kind", "verdict", "critical", "reason"))
        require(
            kind in FACT_KINDS and verdict in FACT_VERDICTS and type(critical) is bool and isinstance(reason, str) and reason.strip(),
            "Invalid fact judgment",
        )
        citation = claim.get("citation")
        require(isinstance(citation, dict), "Fact judgment needs an exact artifact citation")
        checkpoint, case_id = citation.get("checkpoint", "after"), citation.get("case_id")
        field, quote = citation.get("field"), citation.get("quote")
        require(
            isinstance(checkpoint, str) and isinstance(case_id, str) and isinstance(field, str) and isinstance(quote, str) and quote.strip(),
            "Invalid fact citation",
        )
        case = cases.get((checkpoint, case_id))
        require(case is not None, "Fact citation names an unknown case or checkpoint")
        step = citation.get("step")
        if "step" in citation:
            require(type(step) is int and step > 0 and field in STEP_FIELDS - {"step"}, "Invalid fact step citation")
            value = next((row[field] for row in case["steps"] if row["step"] == step), None)
        else:
            require(field in CASE_FACT_FIELDS, "Invalid fact case field")
            value = case.get(field)
        location = (checkpoint, case_id, step, field, quote)
        require(location not in cited and contains_quote(value, quote), "Duplicate fact citation or quote differs from the saved case")
        claim_key = (checkpoint, kind, quote, reason.strip())
        require(claim_key not in seen_claims, "Repeated fact judgment within one checkpoint")
        cited.add(location)
        seen_claims.add(claim_key)
        counts[verdict] += 1
        critical_unsupported += verdict == "unsupported" and critical
    return {
        "complete": facts["complete"],
        "reason": facts["reason"],
        "claims": claims,
        **counts,
        "critical_unsupported": critical_unsupported,
    }


def validate_labels(packet, seal, labels):
    require(
        digest(packet) == seal == labels.get("packet_sha256") and packet["sample_id"] == labels.get("sample_id"),
        "Labels must bind to the sealed reviewer packet",
    )
    obligations = packet["obligations"]
    judgments = labels.get("judgments")
    require(isinstance(judgments, dict) and judgments.keys() == obligations.keys(), "Every frozen obligation needs one judgment")
    deferred = set(packet["deferred_obligation_ids"])
    checkpoints = {"after": packet}
    if "before" in packet:
        checkpoints["before"] = packet["before"]
    valid_steps = {
        (checkpoint, case["id"], step["step"])
        for checkpoint, evidence in checkpoints.items()
        for case in evidence["saved_test_cases"]
        for step in case["steps"]
    }
    for obligation, judgment in judgments.items():
        status, reason, citations = (judgment.get(key) for key in ("status", "reason", "citations"))
        require(status in {"covered", "unmet", "unknown"} and isinstance(reason, str) and reason.strip(), f"Invalid judgment for {obligation}")
        require(judgment.get("source_id") == obligations[obligation]["source_id"], f"Source citation differs for {obligation}")
        require(isinstance(citations, list), f"Invalid citations for {obligation}")
        require(status != "covered" or citations or obligation in deferred, f"Covered {obligation} needs an exact step citation")
        cited = set()
        for item in citations:
            step = (item.get("checkpoint", "after"), item.get("case_id"), item.get("step"))
            require(type(step[2]) is int and step in valid_steps and step not in cited, f"Invalid or duplicate step citation for {obligation}")
            cited.add(step)
    in_scope = {key: value for key, value in judgments.items() if key not in deferred}
    result = {
        "in_scope": len(in_scope),
        "in_scope_status": {key: row["status"] for key, row in in_scope.items()},
        "covered": sum(row["status"] == "covered" for row in in_scope.values()),
        "unknown": sum(row["status"] == "unknown" for row in in_scope.values()),
        "missed_critical": [key for key, row in in_scope.items() if obligations[key]["critical"] and row["status"] != "covered"],
        "deferred": {key: judgments[key]["status"] for key in sorted(deferred)},
    }
    if "fact_review" in labels:
        result["fact_review"] = validate_fact_review(checkpoints, labels["fact_review"])
    return result


def coverage_totals(pairs):
    matched = [pair["coverage"] for pair in pairs if pair["coverage"] is not None]
    arms = {}
    for arm in ARMS:
        reviews = [pair[arm] for pair in matched]
        counts = {key: sum(review[key] for review in reviews) for key in ("in_scope", "covered", "unknown")}
        arms[arm] = {
            **counts,
            "missed_critical": sum(len(review["missed_critical"]) for review in reviews),
            "coverage_percent": round(100 * counts["covered"] / counts["in_scope"], 2) if counts["in_scope"] else None,
        }
    return {
        "scheduled_pairs": len(pairs),
        "matched_pairs": len(matched),
        "covered_delta": arms["candidate"]["covered"] - arms["baseline"]["covered"] if matched else None,
        "arms": arms,
    }


def paired_slots(slots):
    grouped = {}
    for slot in slots:
        identity = (slot["workflow"], slot["repeat"])
        grouped.setdefault(identity, {})[slot["arm"]] = slot
    return grouped


def paired_coverage(slots, cohort):
    pairs = []
    workflows = {}
    for (workflow, repeat), arms in paired_slots(slots).items():
        pair = {
            "workflow": workflow,
            "repeat": repeat,
            "arms": {arm: {"status": arms[arm]["status"], "labeled": arms[arm]["review"] is not None} for arm in ARMS},
            "coverage": None,
        }
        baseline, candidate = (arms[arm]["review"] for arm in ARMS)
        if baseline is not None and candidate is not None:
            baseline_status, candidate_status = baseline["in_scope_status"], candidate["in_scope_status"]
            require(
                baseline_status.keys() == candidate_status.keys() and baseline["deferred"].keys() == candidate["deferred"].keys(),
                "Paired obligation scopes differ",
            )
            baseline_covered = {key for key, status in baseline_status.items() if status == "covered"}
            candidate_covered = {key for key, status in candidate_status.items() if status == "covered"}
            pair.update(
                coverage={"baseline": baseline, "candidate": candidate},
                covered_delta=candidate["covered"] - baseline["covered"],
                baseline_only=sorted(baseline_covered - candidate_covered),
                candidate_only=sorted(candidate_covered - baseline_covered),
            )
        pairs.append(pair)
        workflows.setdefault(workflow, []).append(pair)

    totals = coverage_totals(pairs)
    candidate = totals["arms"]["candidate"]
    checks = None
    if cohort == "rehearsal":
        status = "rehearsal"
    elif totals["matched_pairs"] != FROZEN_PAIRS or not candidate["in_scope"]:
        status = "incomplete"
    else:
        checks = {
            "minimum_candidate_coverage": candidate["covered"] * 100 >= candidate["in_scope"] * MIN_COVERAGE_PERCENT,
            "no_missed_critical": candidate["missed_critical"] == 0,
            "no_pooled_regression": totals["covered_delta"] >= 0,
        }
        status = "pass" if all(checks.values()) else "fail"
    return {
        "gate": {"status": status, "minimum_percent": MIN_COVERAGE_PERCENT, "checks": checks},
        "pooled": totals,
        "workflows": {workflow: coverage_totals(rows) for workflow, rows in workflows.items()},
        "pairs": pairs,
    }


def fact_totals(pairs):
    matched = [pair for pair in pairs if pair["matched"]]
    arms = {}
    for arm in ARMS:
        counts = {key: sum(pair["arms"][arm]["facts"][key] for pair in matched) for key in (*FACT_VERDICTS, "critical_unsupported")}
        adjudicated = counts["supported"] + counts["unsupported"]
        arms[arm] = {
            **counts,
            "adjudicated": adjudicated,
            "unsupported_percent": round(100 * counts["unsupported"] / adjudicated, 2) if adjudicated else None,
        }
    return {"scheduled_pairs": len(pairs), "matched_pairs": len(matched), "arms": arms}


def paired_unsupported_facts(slots, cohort):
    pairs = []
    workflows = {}
    for (workflow, repeat), arms in paired_slots(slots).items():
        evidence = {}
        for arm in ARMS:
            slot = arms[arm]
            review = slot.get("fact_review")
            facts = {key: review[key] for key in ("complete", *FACT_VERDICTS, "critical_unsupported")} if review is not None else None
            evidence[arm] = {"status": slot["status"], "facts": facts}
        matched = all(
            row["status"] == "delivered" and row["facts"] is not None and row["facts"]["complete"] and not row["facts"]["unknown"] for row in evidence.values()
        )
        pair = {"workflow": workflow, "repeat": repeat, "arms": evidence, "matched": matched}
        pairs.append(pair)
        workflows.setdefault(workflow, []).append(pair)
    totals = fact_totals(pairs)
    baseline, candidate = (totals["arms"][arm] for arm in ARMS)
    checks = None
    if cohort == "rehearsal":
        status = "rehearsal"
    elif totals["matched_pairs"] != FROZEN_PAIRS or not (baseline["adjudicated"] and candidate["adjudicated"]):
        status = "incomplete"
    else:
        checks = {
            "no_candidate_critical_unsupported": candidate["critical_unsupported"] == 0,
            "no_pooled_rate_increase": candidate["unsupported"] * baseline["adjudicated"] <= baseline["unsupported"] * candidate["adjudicated"],
        }
        status = "pass" if all(checks.values()) else "fail"
    return {
        "gate": {"status": status, "checks": checks},
        "pooled": totals,
        "workflows": {workflow: fact_totals(rows) for workflow, rows in workflows.items()},
        "pairs": pairs,
    }


def summarize(package, label_dir):
    private = read_json((package / "private-ledger.json").read_bytes())
    manifest = read_json(FROZEN_MANIFEST.read_bytes())
    require(
        private["protocol"] == manifest["protocol"] and private["schedule_sha256"] == manifest["content_hashes"]["pair_order"],
        "Private ledger does not match the frozen protocol",
    )
    require(private["cohort"] in {"rehearsal", "live"}, "Use a separate rehearsal or live cohort")
    require(
        private["baseline_commit"] == manifest["baseline"]
        and re.fullmatch(r"[0-9a-f]{40}", private["candidate_commit"])
        and private["candidate_commit"] != private["baseline_commit"],
        "Private ledger does not match the frozen baseline and candidate pins",
    )
    check_schedule([(slot["workflow"], slot["repeat"], slot["arm"]) for slot in private["slots"]], manifest)
    require(label_dir.is_dir(), "Labels directory is missing")
    labels = {}
    for path in label_dir.glob("*.json"):
        label = read_json(path.read_bytes())
        sample = label["sample_id"]
        require(sample not in labels, "Duplicate review for one sample. Resolve disagreements before unblinding")
        labels[sample] = label
    results = []
    seen = set()
    for slot in private["slots"]:
        require("fact_review" not in slot, "Private intake slot contains unverified fact review")
        if slot["status"] != "delivered":
            require(
                slot["status"] in INCOMPLETE_OUTCOMES | {"unrun", "invalid_evidence"} and not {"sample_id", "packet_sha256"} & slot.keys(),
                "Invalid outcome for slot without a reviewer packet",
            )
            results.append({**slot, "review": None})
            continue
        sample = slot["sample_id"]
        require(re.fullmatch(r"[0-9a-f]{32}", sample) and sample not in seen, "Invalid or duplicate sample ID")
        seen.add(sample)
        envelope = read_json((package / "reviewer" / f"{sample}.json").read_bytes())
        packet = envelope["packet"]
        require(sample == packet["sample_id"] and digest(packet) == envelope["packet_sha256"] == slot["packet_sha256"], "Changed reviewer envelope")
        capture = slot["capture"]
        require(
            capture["cohort"] == private["cohort"]
            and capture["commit"] == private[f"{slot['arm']}_commit"]
            and capture["input_sha256"] == digest(packet["input"])
            and capture["status"] == "delivered"
            and all(exact(capture[key], slot[key]) for key in ("workflow", "repeat", "arm")),
            "Delivered capture differs from the ledger cohort, pins or scheduled slot",
        )
        if sample not in labels:
            results.append({**slot, "review": None})
            continue
        label = labels.pop(sample)
        result = validate_labels(packet, slot["packet_sha256"], label)
        fact_review = result.pop("fact_review", None)
        reviewed = {**slot, "review_sha256": digest(label), "review": result}
        if fact_review is not None:
            reviewed["fact_review"] = fact_review
        results.append(reviewed)
    require({path.stem for path in (package / "reviewer").glob("*.json")} == seen, "Reviewer files differ from the private slot ledger")
    require(not labels, "Review references an unknown sample")
    return {
        **private,
        "slots": results,
        "business_coverage": paired_coverage(results, private["cohort"]),
        "unsupported_facts": paired_unsupported_facts(results, private["cohort"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("prepare", help="Validate private captures and compile blinded review packets")
    pack.add_argument("--frozen", type=Path, required=True)
    pack.add_argument("--intake", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    score = commands.add_parser("summarize", help="Check finalized labels and record matched coverage and unsupported facts privately")
    score.add_argument("--package", type=Path, required=True)
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(json_bytes(dict(prepare(args.frozen, args.intake, args.output))).decode(), end="")
        else:
            result = summarize(args.package, args.labels)
            with args.output.open("xb") as output:
                output.write(json_bytes(result))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f"Adjudication preparation failed: {exc}\n")


if __name__ == "__main__":
    main()
