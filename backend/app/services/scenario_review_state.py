"""Snapshot-bound scenario identities and conservative review carry-forward."""

import hashlib
import json
from typing import Any


def scenario_key(requirement_id: str, scenario_id: str) -> str:
    return json.dumps([requirement_id, scenario_id], separators=(",", ":"), ensure_ascii=False)


def scenario_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for group in payload.get("coverage_plan") or []:
        requirement = {key: value for key, value in group.items() if key != "scenarios"}
        for scenario in group.get("scenarios") or []:
            requirement_id = str(group.get("requirement_id") or "").strip()
            scenario_id = str(scenario.get("id") or "").strip()
            key = scenario_key(requirement_id, scenario_id)
            if not requirement_id or not scenario_id or key in result:
                raise ValueError("Scenarios must have unique IDs within an identified source requirement. Regenerate this artifact.")
            content = json.dumps(
                {
                    "requirement": requirement,
                    "scenario": scenario,
                    "analysis": [item for item in (payload.get("requirement_analysis") or []) if item.get("requirement_id") == requirement_id],
                },
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
            result[key] = {
                "requirement_id": requirement_id,
                "scenario_id": scenario_id,
                "content_fingerprint": hashlib.sha256(content.encode()).hexdigest(),
            }
    return result


def current_scenario_reviews(payload: dict, metadata: dict, snapshot_id: str) -> dict:
    index = scenario_index(payload)
    stored = metadata.get("scenario_reviews") or {}
    items = stored.get("items", {}) if stored.get("snapshot_id") == snapshot_id else {}
    legacy = metadata.get("latest_human_review") or {}
    legacy_approved = not stored and legacy.get("snapshot_id") == snapshot_id and legacy.get("decision") == "approve"
    return {
        key: {
            **identity,
            "status": "approved" if legacy_approved else "needs_review",
            "quality_flags": [],
            **({key: legacy.get(key) for key in ("reviewer_user_id", "reviewer_name", "reviewed_at")} if legacy_approved else {}),
            **(items[key] if key in items and items[key].get("content_fingerprint") == identity["content_fingerprint"] else {}),
        }
        for key, identity in index.items()
    }


def carry_scenario_reviews(payload: dict, previous_payload: dict, previous_metadata: dict, previous_snapshot_id: str, snapshot_id: str) -> dict:
    try:
        old = current_scenario_reviews(previous_payload, previous_metadata, previous_snapshot_id)
        current = current_scenario_reviews(payload, {}, snapshot_id)
    except ValueError:
        return {"snapshot_id": snapshot_id, "items": {}}
    for key, value in current.items():
        if key in old and old[key]["content_fingerprint"] == value["content_fingerprint"]:
            current[key] = {**old[key], "carried_from_snapshot_id": previous_snapshot_id}
    return {"snapshot_id": snapshot_id, "items": current}
