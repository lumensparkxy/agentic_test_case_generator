# Requirement import review (#294)

A project contains one combined active requirement baseline. Uploads, JIRA and
Azure DevOps are input adapters, not mutually exclusive project modes.

## Workflow

1. Import or refine requirements. Extraction saves a pending comparison only.
2. Compare current and incoming text. Exact matches preserve identity and review;
   semantic and wording matches are suggestions. Correct a match, add independently,
   or skip. Resolve ambiguous matches before applying.
3. Select a replacement scope only when the incoming content replaces an existing
   subset. Unmatched requirements inside that scope appear in **Retiring** and
   require confirmation. Requirements outside it remain active.
4. Apply the reviewed changes. The complete baseline, immutable snapshot, revision,
   timeline and retry receipt commit in one transaction. Other project revision writers
   also validate and commit atomically so an overlapping generation cannot restore
   an older requirement baseline. Historical requirements
   and downstream artifacts are retained; downstream stages become stale.

Changed/new content needs human review. Requirement review decisions persist, so
reopening a project or importing another source preserves them. Retired requirements
are available below the active workbench and are excluded from generation.

## Contracts and persistence

Project requests to `/requirements/parse`, `/integrations/jira/import`, and
`/integrations/azure-devops/import` must include `import_mode=review` and
`base_project_revision`. File/refinement requests use multipart form fields;
integration requests use JSON. They return `RequirementImportPreview` with temporary
candidate IDs, the captured project revision, existing requirements, suggestions,
counts, warnings and an import ID. Standalone extraction without a project keeps
its previous response. Older project clients receive HTTP 409 `import_review_required`
before extraction; release API and UI together.

Project routes:

- `GET /projects/{project_id}/requirement-imports`: pending previews, newest first.
- `GET /projects/{project_id}/requirement-imports/{import_id}`: saved preview.
- `POST /projects/{project_id}/requirement-imports/{import_id}/compare`: create a fresh
  comparison from the saved extraction and current project, without re-extraction.
- `POST /projects/{project_id}/requirement-imports/{import_id}/apply`: reviewed decisions,
  update scope, retirement confirmation, captured revision and idempotency key;
  returns `QaProjectDetail`.
- `DELETE /projects/{project_id}/requirement-imports/{import_id}`: cancel a pending preview.
- `PATCH /projects/{project_id}/requirements/reviews`: durable status/flag decisions.
- `GET /projects/{project_id}/requirements/{requirement_uid}/history`: historical snapshots.
- `POST /projects/{project_id}/requirement-imports/recovery`: explicit historical baseline
  plus the current replacement snapshot, staged for review.

`requirement_uid` is stable internal identity; `id` remains the project-wide display
ID. `content_version` increases for changed text. `sources` retain source/version
references and excerpts without silently changing existing sync targets. Existing
source and artifact fields remain compatible. Model-extracted IDs, approval and
version fields are not trusted as canonical identity. Files with the same name and
issues with the same key are not sufficient evidence of requirement identity.

Legacy snapshots are initialized lazily and never rewritten. Only reviewed apply
creates schema version 2, with active `requirements` and `retired_requirements`.
Snapshot history is the immutable version record. Staged comparisons and applied
snapshots retain the extraction guidance manifest. No historical snapshots are
silently merged into ordinary project loads. Comparison payloads are bounded below
Firestore's document limit; oversize batches fail without changing the baseline.

Matching compares every incoming/current chunk using the existing configured model;
responses are validated against supplied IDs. Model errors are surfaced and uncertain
candidates require manual decisions. Exact text matches remain usable without a model.
No matcher can approve, retire, publish or push requirements to an external system.

A failed apply retains the preview. Retry the same decisions with the same key.
Changed project revisions require a fresh comparison. A key reused for different
decisions fails with HTTP 409. Applying one preview makes other older previews stale.

## Recovery operation

Use the repo-local virtual environment and existing administrative Firestore credentials:

```bash
.venv/bin/python scripts/prepare_requirement_recovery.py \
  --project-id PROJECT_ID \
  --baseline-snapshot-id ORIGINAL_REQUIREMENTS_SNAPSHOT \
  --incoming-snapshot-id CURRENT_REPLACEMENT_SNAPSHOT \
  --expected-revision CURRENT_REVISION
```

The default is read-only. Add `--prepare` to save a pending recovery comparison and
obtain semantic suggestions; this never applies the recovery. Open Requirements,
choose **Review import**, inspect the full proposed result, and use **Apply reviewed
changes**. Only the selected current replacement is eligible; a changed revision
requires fresh inspection. Original baseline IDs/approvals are retained and new IDs
are allocated for unrelated incoming requirements. Historical snapshots remain intact.

## Validation

`backend/tests/test_requirement_imports.py` covers reconciliation, rollback, revisions,
retries, permissions, history, review persistence and recovery. Browser coverage lives
in `frontend/e2e/requirement-import-review.spec.js`, including a mobile accessibility
check. Run those checks, the full backend suite, both strict offline benchmarks,
OpenAPI/type validation and frontend build/lint/format before releasing both sides.
