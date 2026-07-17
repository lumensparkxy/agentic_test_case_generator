# Persistence Target Decision

Status: Accepted for Phase 4 planning on 2026-06-12

GitHub issue: [#48](https://github.com/lumensparkxy/agentic_test_case_generator/issues/48)

## Decision

Use a staged persistence approach:

1. Keep the current Firestore-backed behavior for the next compatibility release.
   The current backend already uses Firestore through service modules for audit,
   billing, reporting, versioning, and integration metadata, and the local test
   suite depends on patched Firestore seams.
2. Make PostgreSQL the accepted durable target for compliance-grade audit,
   billing ledger, reporting, and versioned artifacts.
3. Unblock [#49](https://github.com/lumensparkxy/agentic_test_case_generator/issues/49)
   to introduce storage-independent repository boundaries for audit and billing
   before any database migration. The first implementation story should preserve
   current Firestore behavior behind those boundaries and add a Postgres-ready
   contract, not switch production storage in one step.

This means Firestore remains the transitional runtime store, while PostgreSQL is
the target architecture once repository boundaries, schema, migrations, and
operational runbooks exist.

## Implementation Status

[#49](https://github.com/lumensparkxy/agentic_test_case_generator/issues/49)
introduced the first repository boundary slice after this decision. Runtime
behavior still uses Firestore, but audit writes, reporting usage-event reads,
billing repository access, versioning collection lookup, and integration
metadata collection lookup now route through explicit service-layer repository
or adapter seams. PostgreSQL schema, adapter, migration, and runbook work remain
future stories.

## Why

The product needs reporting, audit, billing, and compliance queries that are
join-heavy and ledger-oriented:

- invoice and usage rollups by user, organization, event type, month, pricing
  version, workflow run, and request ID
- append-only usage and audit records with replayable rollups
- versioned requirement/test-case artifacts linked to the source workflow event
- admin reporting that can filter across users, organizations, operations, and
  time windows
- durable dead-letter handling for failed audit writes

Firestore is effective for the current MVP because it is already integrated and
the code degrades gracefully when credentials are unavailable. It is not the
best long-term system of record for the query shape above. Google documents
Firestore as a NoSQL document database whose relationship model differs from
traditional databases, and its Standard edition query model has fixed query
limits such as 30 disjunctions for `or`/`in`/`array-contains-any` queries and
other compound-query restrictions. Firestore aggregation queries also execute
only as direct backend responses, have a 60 second deadline, and scale with
index entries scanned. Firestore transactions are useful for document updates,
but they require reads before writes, may retry the transaction function, and
fail offline.

Cloud SQL for PostgreSQL is a managed relational PostgreSQL service and is a
better fit for the target audit and billing workload because relational schema,
foreign keys, transactional ledger updates, indexed joins, and SQL rollups map
directly to the required queries. Google Cloud SQL for PostgreSQL documents
managed PostgreSQL operations and Cloud Run connectivity, and its FAQ documents
PostgreSQL transaction isolation behavior.

## External Product Documentation Checked

- [Firestore Native mode overview](https://docs.cloud.google.com/firestore/docs?authuser=14)
- [Firestore query limitations](https://firebase.google.com/docs/firestore/query-data/queries#query_limitations)
- [Firestore aggregation query behavior and limitations](https://firebase.google.com/docs/firestore/query-data/aggregation-queries#behavior_and_limitations)
- [Firestore transactions and batched writes](https://firebase.google.com/docs/firestore/manage-data/transactions)
- [Cloud SQL for PostgreSQL documentation](https://docs.cloud.google.com/sql/docs/postgres)
- [Cloud SQL for PostgreSQL FAQ](https://docs.cloud.google.com/sql/docs/postgres/faq)
- [Connect to Cloud SQL for PostgreSQL from Cloud Run](https://docs.cloud.google.com/sql/docs/postgres/connect-run)

## Current Firestore Surface

| Area | Current source | Data involved |
| --- | --- | --- |
| Firebase client access | `backend/app/services/firebase_admin.py`, `backend/app/services/firestore_repository.py` | Firebase Admin app and Firestore collection adapter |
| Workflow audit | `backend/app/services/audit_service.py`, `backend/app/services/audit_repository.py` | `workflow_runs`, `usage_events`, local dead-letter summaries, optional `audit_dead_letters` Firestore sink |
| Billing repository | `backend/app/services/billing_repository.py`, `backend/app/services/firestore_repository.py` | `user_profiles`, `billing_accounts`, `billing_wallet_ledger`, `billing_allocations`, `billing_consumption` |
| Reporting | `backend/app/services/reporting_service.py`, `backend/app/services/usage_event_repository.py` | streamed `usage_events` grouped by user, organization, and event type |
| Artifact versioning | `backend/app/services/versioning_service.py`, `backend/app/services/firestore_repository.py` | `requirements_sets`, `test_case_sets`, item subcollections, version subcollections |
| QA project review decisions | `backend/app/services/use_case_review_service.py`, `backend/app/services/workflow_project_service.py`, `backend/app/services/firestore_repository.py` | immutable Use Cases snapshots, project stage approval metadata, `use_case_reviews` provenance records, and project `timeline` events |
| JIRA connections and mappings | `backend/app/services/jira_connection_service.py`, `backend/app/services/jira_requirements_service.py`, `backend/app/services/jira_sync_service.py`, `backend/app/services/firestore_repository.py` | encrypted connection records and requirement sync mappings |
| Azure DevOps connections and mappings | `backend/app/services/azure_devops_connection_service.py`, `backend/app/services/azure_devops_requirements_service.py`, `backend/app/services/azure_devops_sync_service.py`, `backend/app/services/firestore_repository.py` | encrypted connection records and work item sync mappings |
| API contracts and data models | `backend/app/models.py` | `AuthUser`, billing models, usage report models, requirement/test-case artifact metadata |

Routers should continue to call service functions rather than storage adapters
directly. The repository boundary belongs under `backend/app/services/` so
routers, agents, and frontend contracts can remain stable while storage changes.

### Workspace summary Firestore indexes

`GET /workspace/summary` deliberately uses hard-limited queries and does not
fall back to a collection scan. Deployments must create the following composite
indexes before enabling the Home-first workspace:

| Scope | Fields in query order |
| --- | --- |
| `qa_projects` collection, active-only | `owner_user_id ASC`, `status ASC`, `updated_at DESC`, `project_id ASC` |
| `qa_projects` collection, including archived | `owner_user_id ASC`, `updated_at DESC`, `project_id ASC` |
| `execution_runs` collection group | `actor_user_id ASC`, `project_id ASC`, `created_at DESC`, `run_record_id ASC` |

The project query is bounded by `projects_limit` (maximum 50). For each returned
owned project, at most the seven current stage snapshots are read by document
identity and execution history is queried with the validated `runs_limit`
(maximum 50). This makes the read cost bounded by request limits and prevents
cross-user project or execution records from entering the response. Missing
indexes surface as a safe HTTP 503 so operators see a configuration failure
instead of receiving a partial or misleading workspace.

### Use Cases review transaction

`POST /projects/{project_id}/use-cases/reviews` treats the generated Use Cases
snapshot as immutable evidence. A Firestore transaction reads the owned project,
deterministic review identity, and exact current snapshot before writing the
review record, updated project stage/revision, and deterministic timeline event.
Automated quality approval remains snapshot payload evidence and cannot satisfy
the human stage gate; only a matching current-snapshot human approval does.
Durable stage metadata retains the authorized reviewer's user ID, display name,
and email so the UI can present a human-readable actor while keeping the raw ID
in secondary provenance.
The transaction function is retry-safe: the stable request identity resolves
to the existing equivalent decision, while a changed payload, stale snapshot,
or stale base revision returns HTTP 409 without a partial write. Approval-only
updates do not mark downstream artifacts stale because no generated content
changed. A future PostgreSQL adapter must preserve the same uniqueness,
optimistic-concurrency, atomicity, ownership, and immutable-snapshot guarantees.

## Required Guarantees

| Domain | Required guarantee | Decision impact |
| --- | --- | --- |
| Billing ledger | Append-only consumption records, idempotency by request/workflow/event ID, atomic account/ledger updates, replayable monthly rollups | PostgreSQL target; Firestore adapter may remain transitional but must satisfy the repository contract as far as current behavior allows |
| Workflow audit | Start/complete records and usage events linked by request ID, workflow run ID, actor, trace ID, status, and operation | Repository contract must preserve existing audit payload fields and local dead-letter behavior |
| Reporting | Query by user, organization, event type, status, and time window without streaming all events in process | PostgreSQL target read model or rollups; current Firestore reporting remains compatibility behavior |
| Versioned artifacts | Immutable versions linked to source event and previous version where available | PostgreSQL target schema should model sets, items, versions, source events, and content hashes |
| Human review decisions | Immutable decision provenance keyed by request identity, optimistic project revision checks, and atomic decision/stage/timeline writes without changing reviewed snapshots | PostgreSQL target schema and transitional Firestore implementation must preserve reviewer, snapshot, comment, request, revision, and timeline evidence |
| Integration metadata | Per-user encrypted connection records and provider sync mappings | Can remain Firestore-backed until audit/billing/reporting migration proves the boundary pattern |
| Local/E2E tests | No real Firebase or database dependency for default local validation | Keep fake/patchable repositories and preserve deterministic test behavior |

## Follow-Up Implementation Stories

Existing issue state after this decision and the first implementation slice:

- [#49](https://github.com/lumensparkxy/agentic_test_case_generator/issues/49)
  introduced explicit repository/adapter seams while preserving current
  Firestore behavior.
- [#56](https://github.com/lumensparkxy/agentic_test_case_generator/issues/56)
  added an optional Firestore dead-letter sink behind the audit repository
  boundary while preserving local-only behavior by default.

Issue-ready follow-ups to create before implementation:

1. Define the PostgreSQL schema and migration plan for users, workflow runs,
   usage events, billing accounts, billing ledger entries, allocations,
   consumption records, and monthly rollups.
2. Add a PostgreSQL repository adapter behind the audit/billing boundary,
   including idempotency and transaction tests.
3. Move usage reporting to PostgreSQL repository queries or rollup tables
   instead of streaming all usage events into process memory.
4. Extend the persistence boundary to requirement/test-case versioning after
   audit and billing prove the pattern.
5. Plan Firestore-to-PostgreSQL migration/export tooling and rollback behavior
   for existing deployments.
6. Decide whether integration connection metadata remains in Firestore or moves
   to PostgreSQL after credential rotation operations prove out, including the
   seamless re-encryption support implemented by
   [#77](https://github.com/lumensparkxy/agentic_test_case_generator/issues/77).

## Non-Goals

- Do not implement PostgreSQL, migrations, or storage adapters in this spike.
- Do not remove Firestore-backed code paths in this spike.
- Do not change authentication policy; that is tracked by
  [#50](https://github.com/lumensparkxy/agentic_test_case_generator/issues/50)
  and [#51](https://github.com/lumensparkxy/agentic_test_case_generator/issues/51).

## Validation Plan

- Review this decision record against the current Firestore service modules.
- Verify every referenced source file exists.
- Run `git diff --check`.
