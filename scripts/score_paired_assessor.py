"""Compare pinned assessors against the same adjudicated inputs, offline."""

import argparse
from collections import Counter
from hashlib import sha256
from pathlib import Path
import re

from scripts.prepare_case_adjudication import ARMS, FROZEN_MANIFEST, json_bytes, read_json, require


LAYERS = ("structural", "design")
DESIGN_VERDICTS = {"assessed_complete": "approved", "incomplete": "rejected", "unknown": "unknown"}
UNFINISHED = {"failed", "interrupted", "blocked"}


def _check_input(record, directory, key):
    path = (directory / record["file"]).resolve()
    require(path.is_relative_to(directory), f"{key}: input path escapes the frozen directory")
    input_hash = sha256(path.read_bytes()).hexdigest()
    require(input_hash == record["input_sha256"], f"{key}: input bytes differ from the frozen manifest")
    return input_hash


def _examples(manifest, directory):
    examples = {}
    seen_inputs = set()
    for example in manifest["examples"]:
        key, layer = example["id"], example["layer"]
        require(isinstance(key, str) and key.strip() and key not in examples, "Missing or duplicate example ID")
        require(layer in LAYERS, f"{key}: invalid review layer")
        input_hash = _check_input(example, directory, key)
        require((layer, input_hash) not in seen_inputs, f"{key}: duplicate input for {layer} review")
        seen_inputs.add((layer, input_hash))
        if layer == "design":
            for arm in ARMS:
                _check_input(example["review_inputs"][arm], directory, f"{key} {arm} design projection")
        examples[key] = example
    require(examples, "The manifest has no examples")
    return examples


def _rows(document, identity, examples):
    require(isinstance(document, dict), "Evidence must be a JSON object")
    require(all(document.get(key) == value for key, value in identity.items()), "Evidence identity does not match the pinned manifest")
    rows = {}
    for row in document["rows"]:
        key = row["id"]
        require(key in examples and key not in rows, f"Unexpected or duplicate evidence row {key}")
        require(row["input_sha256"] == examples[key]["input_sha256"], f"{key}: evidence used a different input")
        rows[key] = row
    return rows


def _observed(row, example, arm):
    if row is None:
        return {"status": "missing"}
    status = row["status"]
    if status in UNFINISHED:
        require(isinstance(row.get("reason"), str) and row["reason"].strip(), "Unfinished capture needs a reason")
        require("output" not in row, "Unfinished capture cannot provide a verdict")
        return {"status": status, "reason": row["reason"]}

    require(status == "completed", "Invalid capture status")
    output = row["output"]
    require(isinstance(output, dict), "Completed capture needs its raw output object")
    if example["layer"] == "structural":
        approved = output.get("approved")
        require(isinstance(approved, bool), "Structural output needs its boolean approved field")
        verdict = "approved" if approved else "rejected"
    else:
        expected_hash = example["review_inputs"][arm]["input_sha256"]
        require(output.get("input_hash") == expected_hash, f"{row['id']} {arm}: assessor output used a different design projection")
        design = output.get("design_status")
        require(design in DESIGN_VERDICTS, "Independent output needs its design_status")
        diagnostics = output.get("diagnostics") or {}
        unverified = output.get("model_status", output.get("status")) == "unknown" or diagnostics.get("unverified_group_count")
        verdict = "unknown" if unverified else DESIGN_VERDICTS[design]
        if verdict == "unknown" and isinstance(output.get("reason"), str):
            return {"status": verdict, "reason": output["reason"]}
    return {"status": verdict}


def _label(row):
    if row is None:
        return {"verdict": "missing", "critical": False}
    verdict, critical, reason = row["verdict"], row["critical"], row["reason"]
    require(verdict in {"valid", "defective", "unknown"}, "Invalid reference verdict")
    require(isinstance(critical, bool) and (not critical or verdict == "defective"), "Critical labels require a defective design")
    require(isinstance(reason, str) and reason.strip(), "A reference verdict needs a reason")
    return {"verdict": verdict, "critical": critical, "reason": reason}


def _measure(rows, arm):
    observed = Counter()
    errors = Counter()
    for row in rows:
        verdict = row["reference"]["verdict"]
        status = row[arm]["status"]
        observed[status] += 1
        if status == "approved" and verdict == "defective":
            errors["false_approvals"] += 1
            if row["reference"]["critical"]:
                errors["critical_false_approvals"] += 1
        if status == "rejected" and verdict == "valid":
            errors["false_rejections"] += 1
    return {
        "observed": {key: observed[key] for key in sorted({"approved", "rejected", "unknown", "missing"} | UNFINISHED)},
        "false_approvals": errors["false_approvals"],
        "critical_false_approvals": errors["critical_false_approvals"],
        "false_rejections": errors["false_rejections"],
    }


def _compare_layer(rows):
    summary = {arm: _measure(rows, arm) for arm in ARMS}
    references = Counter(row["reference"]["verdict"] for row in rows)
    critical = sum(row["reference"]["critical"] for row in rows)
    known_pairs = sum(
        row["reference"]["verdict"] in {"valid", "defective"} and all(row[arm]["status"] in {"approved", "rejected"} for arm in ARMS) for row in rows
    )
    if not references["valid"] or not critical or known_pairs != len(rows):
        status = "incomplete"
    elif summary["candidate"]["critical_false_approvals"] or summary["candidate"]["false_rejections"] > summary["baseline"]["false_rejections"]:
        status = "fail"
    else:
        status = "pass"
    return {
        "status": status,
        "examples": len(rows),
        "known_pairs": known_pairs,
        "valid_labels": references["valid"],
        "defective_labels": references["defective"],
        "critical_labels": critical,
        "unknown_labels": references["unknown"],
        "missing_labels": references["missing"],
        **summary,
    }


def compare(manifest_path, manifest_hash, baseline_path, candidate_path, labels_path=None):
    manifest_bytes = manifest_path.read_bytes()
    require(sha256(manifest_bytes).hexdigest() == manifest_hash, "Assessor manifest SHA-256 differs from its precommitted digest")
    manifest = read_json(manifest_bytes)
    cohort = manifest["cohort"]
    require(cohort in {"rehearsal", "independent"}, "Invalid comparison cohort")
    baseline = read_json(FROZEN_MANIFEST.read_bytes())["baseline"]
    candidate = manifest["candidate_commit"]
    require(manifest["baseline_commit"] == baseline, "Baseline differs from the frozen 2.0 pin")
    require(isinstance(candidate, str) and re.fullmatch("[0-9a-f]{40}", candidate) and candidate != baseline, "Invalid candidate pin")
    examples = _examples(manifest, manifest_path.resolve().parent)
    identity = {"cohort": cohort, "manifest_sha256": manifest_hash}
    captures = {}
    evidence_hashes = {}
    for arm, pin, path in zip(ARMS, (baseline, candidate), (baseline_path, candidate_path), strict=True):
        raw = path.read_bytes()
        captures[arm] = _rows(read_json(raw), {**identity, "arm": arm, "commit": pin}, examples)
        evidence_hashes[arm] = sha256(raw).hexdigest()
    labels = {}
    if labels_path is not None:
        raw = labels_path.read_bytes()
        labels = _rows(read_json(raw), identity, examples)
        evidence_hashes["labels"] = sha256(raw).hexdigest()

    rows = []
    for key, example in examples.items():
        layer = example["layer"]
        rows.append(
            {
                "id": key,
                "layer": layer,
                "input_sha256": example["input_sha256"],
                "reference": _label(labels.get(key)),
                **{arm: _observed(captures[arm].get(key), example, arm) for arm in ARMS},
            }
        )
    layers = {layer: _compare_layer([row for row in rows if row["layer"] == layer]) for layer in LAYERS}
    layer_status = {summary["status"] for summary in layers.values()}
    if cohort == "rehearsal":
        gate = "rehearsal"
    elif "fail" in layer_status:
        gate = "fail"
    elif "incomplete" in layer_status:
        gate = "incomplete"
    else:
        gate = "pass"
    return {
        **identity,
        "baseline_commit": baseline,
        "candidate_commit": candidate,
        "evidence_sha256": evidence_hashes,
        "paired_human_thresholds": gate,
        "layers": layers,
        "examples": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = compare(args.manifest, args.manifest_sha256, args.baseline, args.candidate, args.labels)
        with args.output.open("xb") as output:
            output.write(json_bytes(report))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"Assessor comparison rejected: {exc}\n")


if __name__ == "__main__":
    main()
