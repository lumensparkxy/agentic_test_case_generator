
# Firebase Auth & Audit Architecture for 310_tcAgent

## Recommendation: Firebase Auth as Identity Provider

Status note as of 2026-06-12: the persistence recommendation is clarified by
`docs/persistence-target-decision.md`. PostgreSQL is the accepted target for
compliance-grade audit, billing, reporting, and versioned artifacts, while the
current Firestore-backed behavior remains the transitional runtime. The #49
persistence boundary introduces Firestore adapter/repository seams; PostgreSQL
schema, adapter, migration, and runbook stories remain future work.

Auth policy note as of 2026-06-12: `docs/production-auth-policy-decision.md`
accepts Firebase ID tokens as the production-only protected endpoint token type.
Backend-issued JWTs and `/auth/google/login` are local/test compatibility paths
isolated behind `AUTH_TOKEN_MODE=firebase-or-backend-jwt`.

For this repository, **Firebase Auth** should be the canonical identity provider. The backend must verify Firebase ID tokens directly using the Firebase Admin SDK. Backend-issued JWTs should not be the default; session cookies are only an optional browser optimization, not a primary auth mechanism.

**Postgres** is the recommended durable store for audit, billing, and versioned artifacts. Firestore is a poor fit for this app’s invoice aggregation and audit joins due to its lack of relational joins, transactional consistency, and efficient rollup support.

## Verified Current State (June 2026)

- `backend/app/routers/auth.py` exposes `/auth/google/login`; protected endpoints use `AuthUser` at the FastAPI layer.
- `backend/app/auth/firebase_auth.py` verifies Firebase ID tokens, while `backend/app/auth/jwt_auth.py` keeps legacy/local JWT compatibility.
- Firestore-backed service paths exist for audit, billing, reporting, versioning, JIRA/Azure DevOps connection metadata, and sync mappings through `backend/app/services/firebase_admin.py`, `backend/app/services/firestore_repository.py`, and domain service modules.
- Local tests patch `app.services.firestore_repository.get_firestore_client`, swap repository hooks, or use fakes so default validation does not require real Firebase credentials.
- `frontend/src/App.jsx` and `frontend/src/main.jsx` are wired to Firebase/Google login flows and store the auth token in `localStorage`.

## Target Authentication Flow

**Web and Mobile Clients:**
1. User signs in via Firebase Auth (Google, Microsoft, Apple, etc.)
2. Frontend obtains a Firebase ID token
3. Backend verifies the Firebase ID token with Firebase Admin SDK
4. Backend upserts user in Postgres (by `firebase_uid`)
5. All workflow/API calls use the Firebase UID as the canonical user key
6. All durable events, workflow runs, and artifacts are linked to the Firebase UID

## Relational Schema (Postgres)

### users
- id (PK, UUID)
- firebase_uid (unique, indexed)
- email
- display_name
- created_at
- last_login_at

### auth_identities
- id (PK)
- user_id (FK to users)
- provider (e.g., google, microsoft, apple, password)
- provider_uid
- email
- created_at

### login_events
- id (PK)
- user_id (FK)
- login_time
- ip_address
- user_agent
- firebase_token_id (optional)

### workflow_runs
- id (PK)
- user_id (FK)
- run_type (e.g., requirements_parse, test_case_generate)
- started_at
- completed_at
- status
- request_id (correlation ID)
- provider_metadata (JSON)

### usage_events (append-only, immutable)
- id (PK)
- event_type (e.g., parse_requirements, generate_test_cases, export_csv)
- billing_key (stable string for invoice grouping)
- quantity
- unit (e.g., doc, case, step)
- occurred_at
- actor_user_id (FK)
- workflow_run_id (FK)
- request_id (correlation ID)
- status
- metadata (JSON)

### requirements_sets
- id (PK)
- user_id (FK)
- uploaded_at
- filename
- content_hash
- current_version_id (FK)

### requirement_items
- id (PK)
- requirements_set_id (FK)
- current_version_id (FK)

### requirement_item_versions
- id (PK)
- requirement_item_id (FK)
- version_number
- text
- created_at
- created_by_user_id (FK)
- previous_version_id (nullable, FK)
- source_event_id (FK to usage_events)
- content_hash

### test_case_sets
- id (PK)
- requirements_set_id (FK)
- user_id (FK)
- created_at

### test_case_items
- id (PK)
- test_case_set_id (FK)
- current_version_id (FK)

### test_case_item_versions
- id (PK)
- test_case_item_id (FK)
- version_number
- title
- description
- steps (JSON)
- expected_result
- created_at
- created_by_user_id (FK)
- previous_version_id (nullable, FK)
- source_event_id (FK to usage_events)
- content_hash

### monthly_usage_rollups
- id (PK)
- user_id (FK)
- month
- total_events
- total_quantity
- generated_at

### invoice_line_items
- id (PK)
- user_id (FK)
- month
- event_type
- quantity
- unit
- amount
- occurred_at
- workflow_run_id (FK)

## Invoice- and Audit-Friendly Data Design

Every usage event must include:
- event_type
- billing_key
- quantity
- unit
- occurred_at
- actor_user_id
- workflow_run_id
- request_id/correlation_id
- status
- metadata (JSON)

Version tables must link `previous_version_id` and `source_event_id` for full auditability.
All events are immutable (append-only). All versioned artifacts are linked to the event and user.

## Event Taxonomy (Mapped to Current Endpoints)

- requirements_parsed (POST /requirements/parse)
- requirements_refined (POST /requirements/parse with feedback)
- requirements_enriched (POST /requirements/enrich)
- test_cases_generated (POST /testcases/generate)
- test_cases_validated (validation loop)
- export_csv (POST /export/csv)
- export_excel (POST /export/excel)
- export_json (POST /export/json)
- export_jira (POST /export/jira)
- automation_generated (POST /automation/playwright)
- login (auth event)
- logout (auth event)

**Counts guidance:**
- requirements_generated_count: number of requirements parsed/generated
- requirements_modified_count: number of requirements changed/refined
- test_cases_generated_count: number of test cases generated
- test_cases_modified_count: number of test cases refined/validated

## What Must Change First in Code

- `backend/app/config.py`: `AUTH_TOKEN_MODE` has `firebase-only` as the safe default and `firebase-or-backend-jwt` as the explicit local/test compatibility mode.
- `backend/app/routers/auth.py`: `/auth/google/login` disables backend JWT minting in `firebase-only` and preserves it only in compatibility mode.
- `backend/app/auth/jwt_auth.py`: In `firebase-only`, protected endpoints verify Firebase ID tokens directly and reject backend JWTs; in compatibility mode, they continue accepting backend JWTs for local/E2E workflows.
- `backend/app/auth/firebase_auth.py`: Continue verifying Firebase ID tokens with Firebase Admin SDK and revocation checks; use `firebase_uid` as canonical user key.
- `backend/app/adk_client.py`: Accept and propagate user identity; use for all workflow and session records.
- `backend/app/agents/requirements_agent.py` & `backend/app/agents/test_case_agent.py`: Accept and persist user identity; log workflow_runs and usage_events.
- `frontend/src/App.jsx`: Keep Firebase Auth provider sign-in as the production flow; retain stored local JWT restoration only as compatibility behavior.
- `backend/requirements.txt`: Add `firebase-admin`, `psycopg2` or `asyncpg`.
- `frontend/package.json`: Add `firebase`.

## Migration Plan

1. **Introduce persistence and request correlation**: Add Postgres, implement user and event tables, propagate request IDs.
2. **Enforce accepted auth mode**: Require Firebase ID tokens in production, isolate backend JWT support behind `AUTH_TOKEN_MODE=firebase-or-backend-jwt`, use `firebase_uid` as canonical key, and upsert users on login.
3. **Propagate identity into workflow runners**: Pass user identity into all agent and workflow calls, log workflow_runs and usage_events.
4. **Add versioned requirement/test-case artifacts**: Implement version tables, link to usage events and users.
5. **Enable extra providers and mobile clients**: Expand Firebase Auth providers, add mobile support, extend audit/billing as needed.

## Privacy and Retention

- **Metadata-only audit**: Retain usage_events, workflow_runs, and login_events indefinitely for compliance.
- **Version snapshots**: Retain requirement/test-case versions for audit and rollback; support redaction of sensitive fields.
- **Full-content retention**: Store raw document/test content only as long as required; retain hashes and metadata for long-term audit.
- **User privacy**: Support redaction/deletion requests by removing content but retaining audit linkage and event metadata.

---
_This document is specific to the 310_tcAgent repository. See `backend/app/main.py`, `backend/app/auth/jwt_auth.py`, `backend/app/adk_client.py`, `backend/app/agents/requirements_agent.py`, `backend/app/agents/test_case_agent.py`, `frontend/src/App.jsx`, and `frontend/src/main.jsx` for current logic and migration points._
