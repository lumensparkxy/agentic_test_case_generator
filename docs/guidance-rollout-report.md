# Guidance rollout report — 2026-09-11

Epic #269; delivery #270–#274; prerequisites PR #268. Implementation is available for review; production enablement is blocked pending live evaluation.

## Results

| Check | Result |
|---|---|
| Backend unit/API/ownership/repository/workflow/evaluation tests | 414 passed |
| Backend Ruff lint and formatting | Passed |
| Requirements and Test Cases offline strict benchmarks | Both fixture sets passed |
| Orchestrator offline strict benchmark | Passed |
| OpenAPI export and generated API contract check | Passed |
| Frontend build, lint and formatting | Passed; existing bundle-size advisory remains |
| Required frontend workflow suite, including knowledge | 174 passed |
| Full frontend suite | 186 passed; existing legacy live-generation test failed waiting for an upload on Home (#251); redundant retry stopped |
| Four-arm input/manifest evaluation | 32 matched contract samples: two fixtures × four stages × four arms; no model calls or quality claims |
| Live Gemini preflight | Blocked: 403 SERVICE_DISABLED for generativelanguage.googleapis.com in the configured key's project |
| Human-adjudicated quality, latency, and cost comparison | Not available; no live generation runs completed |

Live read-only inspection confirmed Context → Project knowledge and Settings → Personal knowledge load successfully against the updated local backend. The existing project still has zero memories, and its existing Use Cases show Guidance not recorded. No knowledge, artifact review, personal selection or cloud configuration was mutated during that inspection. Deterministic browser fixtures cover approvals, draft preservation, idempotent retries, explicit bypass, historical/deleted versions, narrow layouts and dialog accessibility; desktop/mobile dialog captures are local test artifacts and are not committed.

Additional boundary tests caught and fixed the keyword-only project ownership call. The correction and Python formatting are propagated to the prerequisite branches, not left as a final-branch-only repair. Knowledge operations remain behind authenticated ownership and the Firestore repository boundary. Request-scope cleanup prevents completed writes/reads from repainting another project. Settings remounts its personal library on account changes.

## Stage decisions

| Stage | Skills | Memory | Reason |
|---|---|---|---|
| Requirements | Disabled | Disabled | Paired quality evidence unavailable |
| Use Cases | Disabled | Disabled | Paired quality evidence unavailable |
| Test Cases | Disabled | Disabled | Paired quality evidence unavailable |
| Automation generation | Disabled | Disabled | Paired quality evidence unavailable |

The configured model is gemini-3.8-flash. Google returned SERVICE_DISABLED for project number 796861486315 when checking model availability. No API enablement, billing change, model migration or deployment was attempted. Restore access to the configured Gemini API, verify that the model is available, then execute the documented per-stage matched evaluation and independent review. Keep #274 and the epic open until that evidence supports staged enablement.

The offline score of 100 in the existing deterministic benchmarks is a regression check, not measured improvement from skills or memory. Unknown assumptions, correction counts, latency and cost are not reported as zero. There is no automatic rollout based solely on passing tests.
