# Scenario review persistence (#261)

The authenticated `POST /projects/{project_id}/use-cases/reviews` endpoint accepts a third decision, `review_scenarios`, alongside `approve` and `request_changes`.

```json
{
  "snapshot_id": "current-snapshot-id",
  "base_project_revision": 12,
  "decision": "review_scenarios",
  "scenario_reviews": [
    {
      "requirement_id": "REQ-101",
      "scenario_id": "REQ-101-SCN-01",
      "status": "approved",
      "quality_flags": ["Ambiguous"]
    }
  ]
}
```

Send `X-Request-ID`; retry the unchanged request with the same ID. Row updates are a nonempty patch of at most 2000 distinct `(requirement_id, scenario_id)` pairs. Allowed statuses are `needs_review`, `approved`, `request_changes`. Flags use the fixed UI vocabulary. Unknown scenarios, malformed identities, duplicate updates and invalid flag/status values are rejected. Stale artifacts cannot receive an approval, either individually or in bulk. Ownership, current snapshot and project revision are checked inside the transaction before writes.

The transaction writes an immutable review record (including changed rows and reviewer provenance), a timeline event, and the new project revision atomically. Current row state lives in `stage_state.use_cases.metadata.scenario_reviews`, with `snapshot_id` and an `items` map keyed by a JSON-encoded pair. Stored entries include content fingerprints, status, flags, reviewer and review time. Generated snapshot payloads remain immutable.

`review_scenarios` always clears whole-version approval. `approve` is the explicit approve-all operation: it sets all current rows approved and records a matching human decision atomically. `request_changes` requires a comment and marks all current rows changes-requested. Flags are informational reviewer annotations and survive bulk status changes. Existing approval-only request fingerprints remain compatible. Empty or ambiguously identified scenario sets cannot be approved.

On regeneration, exact scenario and source-group content fingerprints permit review carry-forward, with the previous snapshot ID retained for provenance. Changed/new scenarios reset to Needs review. The new artifact does not inherit the prior whole-version decision. Legacy approved artifacts without row metadata are interpreted consistently as all rows approved; the first row save creates explicit row metadata and invalidates the old whole decision.

The response retains the existing review/project revision/stage state/orchestrator shape. Generated frontend API contracts expose the new request and record fields. No database migration or deployment of new Firestore collections is required; existing owner-authorized project transactions and review audit collections are reused.
