# Codebase Concerns

This document records risks visible from the current tracked source, docs, and
git/history checks. It is not a full bug backlog.

## 1) Top Risks

| Severity | Concern | Evidence | Impact | Suggested action |
|----------|---------|----------|--------|------------------|
| High | Large, high-churn orchestration files remain central to the app | `frontend/src/App.jsx`, `frontend/src/App.css`, `backend/app/agents/test_case_agent.py`, `backend/app/models.py`, recent git history | Higher regression and merge risk for UI workflow and generation behavior | Continue behavior-preserving extraction under issue-scoped frontend and agent refactor tasks |
| High | Persistence target is not fully settled in docs versus code | `docs/firebase-auth-audit-architecture.md`, `backend/app/services/firebase_admin.py`, `backend/app/services/billing_repository.py` | Architecture decisions can drift between Firestore current state and Postgres target recommendations | Decide whether Firestore remains the near-term durable store or whether Postgres migration is active |
| Medium | Dual auth support can blur production policy | `backend/app/auth/jwt_auth.py`, `backend/app/auth/firebase_auth.py`, `backend/app/routers/auth.py`, `frontend/src/App.jsx` | Local/E2E JWT compatibility is useful, but production paths need clear accepted-token policy | Document deployment auth mode and eventually remove or isolate legacy JWT if no longer needed |
| Medium | `.env.example` duplicates execution settings | `.env.example` lines 34-50 | Increases setup confusion and risk of stale values | Deduplicate the repeated execution block in a focused docs/config cleanup issue |
| Medium | Generated artifacts can dominate local scans | `.gitignore`, `.execution_artifacts/`, `client_submission/` ignore rules | Repository analysis can report generated traces/reports instead of source complexity | Keep ignored artifact cleanup in local workflow and tune future scan scripts to exclude ignored directories |
| Medium | No enforced formatter/linter configuration is tracked | `frontend/package.json`, `backend/requirements.txt`, `.github/workflows/ci.yml` | Style drift and avoidable review noise | Add formatter/linter tooling under a dedicated issue |
| Medium | Metrics endpoint exposure depends on deployment perimeter | `backend/app/main.py`, `backend/app/observability/metrics.py` | `/metrics` may expose operational metadata if public deployments do not protect it | Decide deployment access policy for `/metrics` |

## 2) Technical Debt

| Debt item | Why it exists | Where | Risk if ignored | Suggested fix |
|-----------|---------------|-------|-----------------|---------------|
| Monolithic frontend orchestration | `App.jsx` still owns most workflow state and API actions after component extraction | `frontend/src/App.jsx` | Harder UI changes and fragile E2E updates | Extract workflow state into hooks/reducers in small issue-scoped slices |
| Large global stylesheet | Many feature styles still live in one CSS file | `frontend/src/App.css` | Dead styles and visual regressions become harder to audit | Split CSS by foundation and feature ownership |
| Large generation agent | Test-case generation, review, metrics, fallback, normalization, and prompting still share one module | `backend/app/agents/test_case_agent.py` | Agent behavior changes are difficult to isolate | Split orchestration, normalization, coverage metrics, fallback, and prompt helpers gradually |
| Broad model module | Many product domains share one Pydantic file | `backend/app/models.py` | Contract merge conflicts and long review cycles | Split model modules only after router/service ownership boundaries are stable |
| Older roadmap/doc drift | Some docs describe work as future that is partly implemented now | `docs/implementation-plan.md`, `docs/frontend-refactor-github-issues.md` | Contributors may follow stale sequencing | Add "current state" notes or close superseded plan sections during planning updates |
| No generated frontend API types | Frontend manually consumes backend response shapes | `frontend/src/App.jsx`, `scripts/export_openapi.py` | Response-shape drift can escape until runtime/E2E | Generate frontend types from OpenAPI in a dedicated contract issue |

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
| Firestore reads for reporting/billing | `reporting_service.py`, `billing_repository.py` | Tests cover fallback/warning behavior | Large usage-event history may become expensive or slow | Add rollups or a relational analytics store if usage grows |
| Large frontend state tree | `frontend/src/App.jsx` | Many workflow states in one component | Re-renders and maintenance overhead | Move domain state into hooks/reducers and memoize expensive derived views if needed |
| Local artifact growth | `.execution_artifacts/`, `client_submission/` | Generated outputs are ignored but not auto-pruned | Disk growth and noisy local scans | Add cleanup command or retention guidance |

## 5) Fragile/High-Churn Areas

| Area | Why fragile | Churn signal | Safe change strategy |
|------|-------------|--------------|----------------------|
| `frontend/src/App.jsx` | Owns auth, workflow state, API actions, derived metrics, and layout orchestration | Highest recent git churn among tracked source hotspots; 3013 lines | Keep changes focused and cover with frontend build/E2E |
| `frontend/src/App.css` | Large global stylesheet with feature-specific selectors | High churn and 3478 lines | Move styles by feature only with visual checks |
| `backend/app/agents/test_case_agent.py` | Central generation/review/coverage/fallback pipeline | High churn and 3245 lines | Add focused tests for every behavior slice before refactor |
| `backend/app/models.py` | Shared API contracts for many domains | High churn and 1047 lines | Treat changes as contract changes; run OpenAPI export |
| Integration sync routers/services | JIRA and Azure DevOps have parallel flows with provider-specific edge cases | Recent traceability and test isolation work | Change one provider at a time or keep explicit parity tests |
| Execution runtime | Converts natural language cases into runnable browser specs | New next-version E2E capability | Run execution preview and runtime checks for every change |

## 6) `[ASK USER]` Questions

1. [ASK USER] Should Firestore remain the durable store for the next release, or is the Postgres target in `docs/firebase-auth-audit-architecture.md` an active migration requirement?
2. [ASK USER] Should backend-issued JWT support stay as a supported local/E2E compatibility mode after Firebase Auth becomes the default production identity path?
3. [ASK USER] What retention window should apply to `.execution_artifacts/` and `/tmp/pw_workflow_out` outputs when real client data is used locally?
4. [ASK USER] Should `/metrics` be publicly reachable in deployed environments, or should it require network/auth protection?

## 7) Evidence

- `docs/firebase-auth-audit-architecture.md`
- `docs/implementation-plan.md`
- `docs/frontend-refactor-github-issues.md`
- `.env.example`
- `.gitignore`
- `backend/app/auth/jwt_auth.py`
- `backend/app/auth/firebase_auth.py`
- `backend/app/services/artifact_fetcher.py`
- `backend/app/services/execution_service.py`
- `backend/app/services/billing_repository.py`
- `backend/app/services/reporting_service.py`
- `frontend/src/App.jsx`
- `frontend/src/App.css`
- `backend/app/agents/test_case_agent.py`
- `backend/app/models.py`
