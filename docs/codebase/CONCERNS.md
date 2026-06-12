# Codebase Concerns

This document records risks visible from the current tracked source, docs, and
git/history checks. It is not a full bug backlog.

## 1) Top Risks

| Severity | Concern | Evidence | Impact | Suggested action |
|----------|---------|----------|--------|------------------|
| High | Large, high-churn orchestration and contract files remain central to the app | `frontend/src/App.jsx`, `backend/app/agents/test_case_agent.py`, `backend/app/models.py`, recent git history | Higher regression and merge risk for UI workflow, prompt orchestration, and API contracts | Continue behavior-preserving extraction under issue-scoped frontend, agent, and contract refactor tasks |
| High | PostgreSQL persistence adapter, schema, and migration plan are not implemented | `docs/persistence-target-decision.md`, `backend/app/services/firestore_repository.py`, `backend/app/services/billing_repository.py`, `backend/app/services/audit_repository.py` | Audit, billing, and reporting now have repository seams but still use Firestore as the transitional runtime store | Define PostgreSQL schema/migrations and add a PostgreSQL adapter behind the #49 boundaries |
| Medium | Auth compatibility mode must stay out of production deployments | `docs/production-auth-policy-decision.md`, `backend/app/auth/jwt_auth.py`, `backend/app/routers/auth.py`, `scripts/deploy_cloud_run.sh` | Manual deployments using `firebase-or-backend-jwt` would re-enable backend JWT compatibility outside local/E2E workflows | Keep Cloud Run on `AUTH_TOKEN_MODE=firebase-only` and treat compatibility mode as local/test only |
| Medium | Metrics endpoint exposure must stay intentionally scoped | `backend/app/main.py`, `backend/app/observability/metrics.py`, `scripts/deploy_cloud_run.sh` | `/metrics` exposes operational metadata and should not be accidentally public in production | Keep production deployments on `METRICS_ENABLED=false` or require `METRICS_ACCESS_TOKEN` plus an appropriate network perimeter |

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
| Stored integration credentials | A02 Cryptographic Failures | `jira_connection_service.py`, `azure_devops_connection_service.py`, `docs/credential-rotation-runbook.md` | Fernet encryption using dedicated secret or JWT secret fallback; token hints only; rotation runbook documented | Seamless previous-key decryption and re-encryption support remains a follow-up in #77 |
| SSRF through artifact URLs | A10 Server-Side Request Forgery | `artifact_fetcher.py` | Blocks local/private/non-routable hosts, unsafe schemes, and redirect abuse | Continued hardening needed before broad production use with authenticated/internal artifacts |
| Browser token storage | A07 Identification and Authentication Failures | `frontend/src/App.jsx`, `README.md` | Firebase token verification and backend auth checks | `localStorage` token storage remains MVP-level risk |
| Metrics endpoint exposure | A05 Security Misconfiguration | `backend/app/main.py`, `scripts/deploy_cloud_run.sh` | Endpoint is schema-hidden, can be disabled, and can require a bearer token | Network perimeter remains deployment-specific |
| Generated artifacts may contain sensitive content if real data is used | A01 Broken Access Control / data exposure | `.gitignore`, `docs/artifact-retention-policy.md`, `scripts/cleanup_generated_artifacts.py`, `execution_service.py` | Generated directories are ignored; cleanup is dry-run by default; tracked files are skipped | Durable archival policy for approved client deliverables remains out of scope |

## 4) Performance and Scaling Concerns

| Concern | Evidence | Current symptom | Scaling risk | Suggested improvement |
|---------|----------|-----------------|-------------|-----------------------|
| Long-running agent workflows | `adk_client.py`, `test_case_agent.py`, `scripts/e2e_playwright_workflow.py` | E2E script sets a 600 second timeout | Request/response flows can tie up workers under load | Consider background jobs or async workflow state once product usage grows |
| In-process execution subprocesses | `execution_service.py`, `local_runner.py` | Backend shells out to `npx playwright test` | CPU/browser resource contention in multi-user deployments | Add queueing, concurrency limits, and artifact retention policy |
| Firestore reads for reporting/billing | `usage_event_repository.py`, `reporting_service.py`, `billing_repository.py`, `docs/persistence-target-decision.md` | Tests cover repository-boundary fallback/warning behavior | Large usage-event history may become expensive or slow | Add PostgreSQL query/rollup adapters behind the accepted repository boundary |
| Large frontend state tree | `frontend/src/App.jsx` | Many workflow states in one component | Re-renders and maintenance overhead | Move domain state into hooks/reducers and memoize expensive derived views if needed |
| Local artifact growth | `.execution_artifacts/`, `client_submission/`, `/tmp/pw_workflow_out` | Generated outputs are ignored and covered by the dry-run cleanup command | Disk growth if maintainers never run cleanup | Run `python scripts/cleanup_generated_artifacts.py` before applying age-based cleanup |

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

1. [ASK USER] Should any future approved client deliverable archives live outside local ignored directories, and what retention owner should govern them?
2. [ASK USER] Which production monitoring system should receive metrics, and should it scrape through Cloud Run ingress, a private network path, or a token-protected endpoint?

## 7) Evidence

- `docs/firebase-auth-audit-architecture.md`
- `docs/production-auth-policy-decision.md`
- `docs/artifact-retention-policy.md`
- `docs/credential-rotation-runbook.md`
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
