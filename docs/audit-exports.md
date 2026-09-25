# Audit context in test-case exports

JSON keeps `export_format: test_cases_v1`, `total_count` and `test_cases`, with
additive `audit_metadata` schema version 1. Case IDs, titles and lifecycle values
are unchanged. `Ready` does not mean suite approval or execution readiness.
Consumers must tolerate the added envelope field; the browser downloads the
response as a Blob. CSV retains its header and one row per case, appending an
`Audit <field>` column for each metadata field. Objects/lists use JSON cells;
booleans use `true`/`false` and unavailable values use `null`.

XLSX retains Test Cases and adds Audit, Sources and Coverage sheets. User text
beginning with spreadsheet formula characters (including after leading space),
or leading control whitespace, is prefixed with an apostrophe in CSV/XLSX.
JSON preserves the original text. XLSX requires openpyxl; the server no longer
mislabels CSV bytes as an Excel workbook when that dependency is missing.

Project exports require the current project revision. Optional
`source_snapshot_id` identifies the expected current test-case snapshot; older
clients may omit it, but their full case array must still match the current saved
suite. Ownership is checked before serialization. A stale suite, old revision,
mismatched snapshot or altered case array returns 409 before a file is sent.
Reload and re-review/regenerate the current suite rather than bypassing the
conflict. The existing report-write revision check also protects concurrent edits.

Approval comes from the saved suite's stage/review and recorded completion.
Client `approved`/`review` fields remain accepted for request compatibility but
cannot authorize export. Failed, incomplete or unavailable review requires
`draft_override_requested: true` and a nonempty `draft_override_reason` (422
otherwise). Concrete partial drafts remain exportable; unfinished placeholder
cases remain blocked. An unbound standalone/legacy draft has null artifact
identity and unavailable review/provenance, even if the client claims approval.

Metadata includes server export time, project revision, snapshot ID/version,
artifact set ID when known, per-case artifact versions, independent human and
machine review, draft disposition/reason, delivered/task counts, source mapping
and matching execution evidence. Artifact versions belong to individual cases;
`artifact_version_id` at suite level is null, while `snapshot_id` binds the suite.
Human review is unavailable unless its recorded decision matches that snapshot.
No new human approval is synthesized from the machine review.

Coverage reports explicit requirement/scenario references and known gaps.
Behavioral assessment remains null: a linked ID is not proof the behavior was
tested. Recorded missing references/tasks make a suite incomplete; missing task
information leaves completeness unknown. Source mappings require a matching
recorded requirement snapshot; older unbound provenance stays unavailable.
Verified and unverified excerpts retain their explicit flags.

Execution evidence includes only receipts referencing the exported test-case
snapshot, with selected candidate IDs. `not_executed` means no execution receipt
is recorded in the loaded project evidence; previews never qualify. Unmatched
historical runs yield `unknown`. A latest passed selected run does not certify
the whole suite. Project history is bounded to its latest 100 run records.

The report snapshot records the same resolved export evidence and draft reason.
It does not re-read newer project approval to relabel the exported artifact.
No provider diagnostics, credentials or internal artifact paths are included.

When server evidence requires a draft despite the initial browser state, the API
returns `detail.code: draft_override_required` with status 422. The export panel
shows the message and requires a reason before retrying that snapshot/revision.
Changing the project evidence clears that server-derived draft requirement.
