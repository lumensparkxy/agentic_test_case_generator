#!/usr/bin/env python3
"""Run the bounded, synthetic Flash and Argon quality comparison."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import random
import subprocess
import sys

from google.genai import types

import evaluate_guidance as guidance
from app.agents.context_evidence import MAX_CONTEXT_CHARS
from app.agents.evaluation_limits import EvaluationLimits

FLASH = "gemini-3.8-flash"
ARGON = "gemini-4-argon"
DEADLINE_SECONDS = 600
PROFILES = {
    "flash-expanded": (FLASH, 128_000, None),
    "argon-expanded": (ARGON, 128_000, None),
    "flash-current-context": (FLASH, 48_000, None),
    "argon-current-context": (ARGON, 48_000, None),
    "argon-long-output": (ARGON, 128_000, 256_000),
}
CONFLICT_PROFILES = {
    "flash-conflict-pilot": (FLASH, MAX_CONTEXT_CHARS, None),
    "argon-conflict-pilot": (ARGON, MAX_CONTEXT_CHARS, None),
}
ALL_PROFILES = {**PROFILES, **CONFLICT_PROFILES}
RATES = {FLASH: (0.75, 3.75), ARGON: (2.0, 10.0)}

RULES = {
    "R1": ("Only a Supervisor may authorize a Submitted expense.", "The exact authorization button is Authorize expense.", "Authorize expense"),
    "R2": ("Submission requires a receipt strictly above the configured threshold.", "The threshold is 173 EUR, inclusive on the no-receipt side.", "173 EUR"),
    "R3": ("Withdrawing a Submitted expense returns it to the editable state.", "The editable state is Draft reopened.", "Draft reopened"),
    "R4": ("A rejected expense must show a reason to its submitter.", "The reason appears in the field Review explanation.", "Review explanation"),
    "R5": ("Only the submitter can edit a Draft expense.", "An unauthorized edit returns the exact message Owner access required.", "Owner access required"),
    "R6": (
        "Submitting the same draft twice must create only one submitted expense.",
        "The second submission returns the original expense ID and displays Already submitted.",
        "Already submitted",
    ),
    "R7": ("The approval list must show only expenses in the eligible status.", "The eligible status is Submitted for review.", "Submitted for review"),
    "R8": (
        "A Supervisor can return a submitted expense for correction.",
        "The action is Send for correction and the resulting state is Correction requested.",
        "Correction requested",
    ),
}


def fixtures():
    directory = {
        "requirements": [
            {"id": "R1", "text": "Searching the employee directory for Alex Rivera displays the matching employee Alex Rivera."},
            {"id": "R2", "text": "Searching the employee directory for an unknown name displays No employees found."},
        ],
        "context": {
            "notes": "The directory is at https://synthetic.example.invalid/directory and needs no login. "
            'Use the field "Employee name" and the "Search" button. The only seeded name is "Alex Rivera". '
            '"NoMatch Person" is confirmed absent. The exact empty message is "No employees found".',
        },
        "obligations": {
            "R1": "Search for the supplied Alex Rivera and verify the matching name is visible.",
            "R2": "Search for the supplied NoMatch Person and verify the exact No employees found message.",
        },
        "evidence_markers": {"match": "Alex Rivera", "no_match_input": "NoMatch Person", "empty_state": "No employees found"},
    }
    conflict = json.loads((Path(__file__).parent / "quality_fixtures" / "corpus.json").read_text())["fixtures"]["conflicting_context"]
    result = {"directory": directory, "authorization": conflict}
    for name, padding, decoys in (("project_short", 0, 0), ("project_long", 61_250, 100), ("project_over_budget", 61_250, 650)):
        # The same evidence appears in the same order in every variant. Padding is a synthetic archive index.
        facts = [f"{key} source note. {fact}" for key, (_, fact, _) in RULES.items()]
        gap = "Archived navigation entry with no expense behavior. " * (padding // 7 // 52)
        notes = ("\n" + gap + "\n").join(facts)
        context = {"notes": notes}
        if decoys:
            context["grounded_context"] = {
                "ui_elements": [
                    {"id": f"ARCHIVE-{index:04}", "name": f"Archive entry {index}", "description": "Read-only navigation. " * 8} for index in range(decoys)
                ]
            }
        result[name] = {
            "requirements": [{"id": key, "text": rule} for key, (rule, _, _) in RULES.items()],
            "context": context,
            "obligations": {key: f"{rule} {fact}" for key, (rule, fact, _) in RULES.items()},
            "evidence_markers": {key: marker for key, (_, _, marker) in RULES.items()},
        }
    return {name: {**fixture, "workflow_timeout_seconds": DEADLINE_SECONDS} for name, fixture in result.items()}


def conflict_fixtures():
    return {
        "conflicting_authorization": fixtures()["authorization"],
        "conflicting_threshold": {
            "requirements": [{"id": "R1", "text": "A receipt is required only when an expense exceeds 173 EUR."}],
            "context": {
                "notes": "Archive index entry. No expense behavior specified. " * 2_900 + "\nAn archived expense guide says the receipt threshold is 250 EUR."
            },
            "obligations": {
                "authority": "Verify no receipt is required at 173 EUR and a receipt is required above 173 EUR.",
                "conflict": "Explicitly flag the archived 250 EUR rule and verify the current threshold on an amount above 173 EUR and at or below 250 EUR.",
            },
            "evidence_markers": {"current_threshold": "173 EUR", "archived_threshold": "250 EUR"},
            "workflow_timeout_seconds": DEADLINE_SECONDS,
        },
        "conflicting_withdrawal": {
            "requirements": [{"id": "R1", "text": "Withdrawing a Submitted expense returns it to the editable Draft reopened state."}],
            "context": {"notes": "An older workflow guide says withdrawal permanently changes the expense to a read-only Cancelled state."},
            "obligations": {
                "authority": "Withdraw a Submitted expense and verify Draft reopened and that editing is allowed.",
                "conflict": "Explicitly flag the older read-only Cancelled rule and verify the withdrawal does not leave the expense cancelled or read-only.",
            },
            "evidence_markers": {"current_state": "Draft reopened", "archived_state": "Cancelled"},
            "workflow_timeout_seconds": DEADLINE_SECONDS,
        },
    }


def trial_matrix(conflict_pilot=False):
    names = list(conflict_fixtures() if conflict_pilot else fixtures())
    for repeat in range(1, 4):
        for name in names[repeat - 1 :] + names[: repeat - 1]:
            if conflict_pilot:
                profiles = list(CONFLICT_PROFILES)
            else:
                profiles = list(PROFILES) if name in {"project_long", "project_over_budget"} else list(PROFILES)[:2]
            offset = (repeat - 1) % len(profiles)
            for profile in profiles[offset:] + profiles[:offset]:
                yield name, profile, repeat


def sample(name, profile):
    model, context_cap, output_cap = ALL_PROFILES[profile]
    pilot = profile in CONFLICT_PROFILES
    spec = (conflict_fixtures() if pilot else fixtures())[name]
    input_rate, output_rate = RATES[model]
    return guidance.evaluate(
        live=True,
        model=model,
        stages=("test_cases",),
        fixtures={name: spec},
        guidance_arms=("combined",),
        limits=EvaluationLimits() if pilot else EvaluationLimits(context_cap, 512_000, output_cap),
        input_rate=input_rate,
        output_rate=output_rate,
    )


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def run_trial(output, item):
    name, profile, repeat = item
    sample_id = f"{name}--{profile}--{repeat}"
    result_path = output / f"{sample_id}.json"
    command = [sys.executable, __file__, "--sample", name, profile, "--output", str(result_path)]
    try:
        with (output / f"{sample_id}.log").open("w") as log:
            completed = subprocess.run(command, stdout=log, stderr=log, timeout=DEADLINE_SECONDS, check=False)
        if completed.returncode or not result_path.exists():
            write_json(result_path, {"mode": "worker_failed", "exit_code": completed.returncode, "results": []})
    except subprocess.TimeoutExpired:
        write_json(result_path, {"mode": "deadline_exceeded", "deadline_seconds": DEADLINE_SECONDS, "results": []})
    report = json.loads(result_path.read_text())
    rows = report["results"]
    print(sample_id, rows[0]["status"] if rows else report["mode"], flush=True)
    return {"fixture": name, "profile": profile, "repeat": repeat, "file": result_path.name}


def blind_packets(output, index):
    packets, key = [], {}
    specs = {**fixtures(), **conflict_fixtures()}
    for trial in index:
        rows = json.loads((output / trial["file"]).read_text())["results"]
        row = rows[0] if rows else {}
        artifact = row.get("output", {})
        case_fields = (
            "id",
            "title",
            "description",
            "preconditions",
            "steps",
            "expected_result",
            "test_data",
            "execution_prerequisites",
            "linked_requirement_ids",
            "scenario_refs",
            "source_refs",
            "automation_status",
        )
        blind_id = sha256(trial["file"].encode()).hexdigest()[:16]
        key[blind_id] = {**trial, "output_hash": row.get("output_hash"), "run_hash": row.get("run_hash")}
        packets.append(
            {
                "blind_id": blind_id,
                "answer_key": specs[trial["fixture"]]["obligations"],
                "requirements": specs[trial["fixture"]]["requirements"],
                "coverage_plan": artifact.get("coverage_plan", []),
                "cases": [{field: case.get(field) for field in case_fields} for case in artifact.get("test_cases", [])],
            }
        )
    random.Random(360).shuffle(packets)
    write_json(output / "blind-packets.json", packets)
    write_json(output / "blind-key.json", key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 7), default=4)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--conflict-pilot", action="store_true", help="Compare only conflicting requirements using the app's input limits")
    parser.add_argument("--sample", nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.sample:
        write_json(args.output, sample(*args.sample))
        return
    matrix = list(trial_matrix(args.conflict_pilot))
    if not args.live:
        for name, spec in (conflict_fixtures() if args.conflict_pilot else fixtures()).items():
            guidance.payload_for(spec["requirements"], spec)
            print(name, len(json.dumps(spec["context"], ensure_ascii=False)))
        print(f"{len(matrix)} planned samples, no model calls")
        return
    if "automatic_continuation" not in types.GenerateContentConfig.model_fields:
        parser.error("The live trial requires a Google Gen AI SDK with automatic continuation support.")
    args.output.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        index = list(pool.map(lambda item: run_trial(args.output, item), matrix))
    write_json(args.output / "index.json", index)
    blind_packets(args.output, index)


if __name__ == "__main__":
    main()
