# Codebase Concerns

This document records risks visible from the current tracked source, docs, and
git/history checks. It is not a full bug backlog.

## 1) Top Risks

| Severity | Concern | Evidence | Impact | Suggested action |
|----------|---------|----------|--------|------------------|
| High | Large, high-churn orchestration and contract files remain central to the app | `frontend/src/App.jsx`, `backend/app/agents/test_case_agent.py`, `backend/app/models.py`, recent git history | Higher regression and merge risk for UI workflow, prompt orchestration, and API contracts | Continue behavior-preserving extraction under issue-scoped frontend, agent, and contract refactor tasks |
| High | PostgreSQL persistence adapter, schema, and migration plan are not implemented | `docs/persistence-target-decision.md`, `backend/app/services/firestore_repository.py`, `backend/app/services/billing_repository.py`, `backend/app/services/audit_repository.py` | Audit, billing, and reporting now have repository seams but still use Firestore as the transitional runtime store | Define PostgreSQL schema/migrations and add a PostgreSQL adapter behind the #49 boundaries |
| Medium | Dual auth support can blur production policy | `backend/app/auth/jwt_auth.py`, `backend/app/auth/firebase_auth.py`, `backend/app/routers/auth.py`, `frontend/src/App.jsx` | Local/E2E JWT compatibility is useful, but production paths need clear accepted-token policy | Document deployment auth mode and eventually remove or isolate legacy JWT if no longer needed |
| Medium | Metrics endpoint exposure depends on deployment perimeter | `backend/app/main.py`, `backend/app/observability/metrics.py` | `/metrics` may expose operational metadata if public deployments do not protect it | Decide deployment access policy for `/metrics` |

## 2) Technical Debt

| Debt item | Why it exists | Where | Risk if ignored | Suggested fix |
|-----------|---------------|-------|-----------------|---------------|
| Monolithic frontend orchestration | `App.jsx` still owns most workflow state and API actions after component extraction | `frontend/src/App.jsx` | Harder UI changes and fragile E2E updates | Extract workflow state into hooks/reducers in small issue-scoped slices |
| Shared frontend style cascade | Feature styles are split into owned modules but still share one imported cascade | `frontend/src/styles/index.css`, `frontend/src/styles/*.css` | Cross-feature selectors and import-order changes can still cause visual regressions | Keep new selectors in the closest feature style file and cover visual workflow changes with browser checks |
| Test-case agent orchestration | Coverage metrics, review scoring, fallback generation, and hydration are split, but prompt builders and workflow orchestration still share one module | `backend/app/agents/test_case_agent.py`, `backend/app/agents/test_case_coverage.py`, `backend/app/agents/test_case_review.py`, `backend/app/agents/test_case_fallback.py`, `backend/app/agents/test_case_hydration.py` | Prompt or workflow changes still require careful regression checks across generation behavior | Keep future prompt/orchestration changes focused and rely on helper-level tests plus offline benchmarks |
| Broad model module | Many product domains share one Pydantic file | `backend/app/models.py` | Contract merge conflicts and long review cycles | Split model modules only after router/service ownership boundaries are stable |
| Partial generated frontend API types | High-traffic workflow contracts are generated from OpenAPI, but broader integrations still consume some response shapes manually | `frontend/src/api/generated/api-contracts.d.ts`, `frontend/src/App.jsx`, `scripts/generate_frontend_api_types.py` | Untyped lower-traffic calls can still drift until runtime/E2E | Extend generated contract coverage as additional API areas change |

## 3) Security Concerns

| Risk | OWASP category | Evidence | Current mitigation | Gap |
|------|----------------|----------|--------------------|-----|
| Stored integration credentials | A02 Cryptographic Failures | `jira_connection_service.py`, `azure_devops_connection_service.py` | Fernet encryption using dedicated secret or JWT secret fallback; token hints only | Rotation and secret lifecycle are not documented |
| SSRF through artifact URLs | A10 Server-Side Request Forgery | `artifact_fetcher.py` | Blocks local/private/non-routable hosts, unsafe schemes, and redirect abuse | Continued hardening needed before broad production use with authenticated/internal artifacts |
| Browser token storage | A07 Identification and Authentication Failures | `frontend/src/App.jsx`, `README.md` | Firebase token verification and backend auth checks | `localStorage` token storage remains MVP-level risk |
| Metrics endpoint exposure | A05 Security Misconfiguration | `backend/app/main.py` | Endpoint is schema-hidden and contains operational metrics rather than secrets | Deployment access control is not documented |
| Generated artifacts may contain sensitive content if real data is used | A01 Broken Access Control / data exposure | `.gitignore`, `docs/client-submission-workflow.md`, `execution_service.py` | Generated directories are ignored; docs warn against committing client data | Local retention and cleanup policy is not formalized |

## 4) Performance and Scaling Concerns

| Concern | Evidence | Current symptom | Scaling risk | Suggested improvement |
|---------|----------|-----------------|-------------|-----------------------|
| Long-running agent workflows | `adk_client.py`, `test_case_agent.py`, `scripts/e2e_playwright_workflow.py` | E2E script sets a 600 second timeout | Request/response flows can tie up workers under load | Consider background jobs or async workflow state once product usage grows |
| In-process execution subprocesses | `execution_service.py`, `local_runner.py` | Backend shells out to `npx playwright test` | CPU/browser resource contention in multi-user deployments | Add queueing, concurrency limits, and artifact retention policy |
| Firestore reads for reporting/billing | `usage_event_repository.py`, `reporting_service.py`, `billing_repository.py`, `docs/persistence-target-decision.md` | Tests cover repository-boundary fallback/warning behavior | Large usage-event history may become expensive or slow | Add PostgreSQL query/rollup adapters behind the accepted repository boundary |
| Large frontend state tree | `frontend/src/App.jsx` | Many workflow states in one component | Re-renders and maintenance overhead | Move domain state into hooks/reducers and memoize expensive derived views if needed |
| Local artifact growth | `.execution_artifacts/`, `client_submission/` | Generated outputs are ignored but not auto-pruned | Disk growth and noisy local scans | Add cleanup command or retention guidance |

## 5) Fragile/High-Churn Areas

| Area | Why fragile | Churn signal | Safe change strategy |
|------|-------------|--------------|----------------------|
| `frontend/src/App.jsx` | Owns auth, workflow state, API actions, derived metrics, and layout orchestration | Highest recent git churn among tracked source hotspots; 3013 lines | Keep changes focused and cover with frontend build/E2E |
| `frontend/src/styles/` | Feature-owned stylesheet modules imported through a shared cascade | Former `App.css` styles now span feature files and still depend on import order | Keep selector moves scoped and verify primary workflow screens visually |
| `backend/app/agents/test_case_agent.py` and focused test-case helper modules | Central generation orchestration plus split coverage, review, fallback, and hydration helpers | High churn; orchestrator reduced to about 1400 lines with helpers in `test_case_*.py` modules | Add focused tests for every behavior slice before changing prompt or workflow logic |
| `backend/app/models.py` | Shared API contracts for many domains | High churn and 1047 lines | Treat changes as contract changes; run OpenAPI export |
| Integration sync routers/services | JIRA and Azure DevOps have parallel flows with provider-specific edge cases | Recent traceability and test isolation work | Change one provider at a time or keep explicit parity tests |
| Execution runtime | Converts natural language cases into runnable browser specs | New next-version E2E capability | Run execution preview and runtime checks for every change |

## 6) `[ASK USER]` Questions

1. [ASK USER] Should backend-issued JWT support stay as a supported local/E2E compatibility mode after Firebase Auth becomes the default production identity path?
2. [ASK USER] What retention window should apply to `.execution_artifacts/` and `/tmp/pw_workflow_out` outputs when real client data is used locally?
3. [ASK USER] Should `/metrics` be publicly reachable in deployed environments, or should it require network/auth protection?

## 7) Evidence

- `docs/firebase-auth-audit-architecture.md`
- `docs/persistence-target-decision.md`
- `docs/implementation-plan.md`
- `docs/frontend-refactor-github-issues.md`
- `.env.example`
- `.gitignore`
- `backend/app/auth/jwt_auth.py`
- `backend/app/auth/firebase_auth.py`
- `backend/app/services/artifact_fetcher.py`
- `backend/app/services/execution_service.py`
- `backend/app/services/firestore_repository.py`
- `backend/app/services/audit_repository.py`
- `backend/app/services/billing_repository.py`
- `backend/app/services/reporting_service.py`
- `frontend/src/App.jsx`
- `frontend/src/api/generated/api-contracts.d.ts`
- `frontend/src/api/generated/api-contracts.js`
- `frontend/src/styles/`
- `pyproject.toml`
- `backend/requirements-dev.txt`
- `frontend/eslint.config.js`
- `frontend/.prettierrc.json`
- `backend/app/agents/test_case_agent.py`
- `backend/app/agents/test_case_coverage.py`
- `backend/app/agents/test_case_review.py`
- `backend/app/agents/test_case_fallback.py`
- `backend/app/agents/test_case_hydration.py`
- `backend/app/models.py`
