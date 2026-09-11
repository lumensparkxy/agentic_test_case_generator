# Guidance rollout report — 2026-09-11

Epic #269; stories #270–#274; stacked PRs #275–#279 after prerequisite #268. Gemini access is restored. Requirements and Use Cases now have a limited local pilot with both flags enabled. Test Cases and Automation remain held on #280 and #281. This is not a production deployment or full-application rollout acceptance.

## Method

96 live samples used `gemini-3.8-flash`: two synthetic projects (authentication/search and expenses), four stages, four configurations and three repeats. Each arm used identical source inputs and workflow limits; arm order rotated between repeats. Skill packages stayed at v1.0.0. Memory arms used one synthetic approved reviewer lesson. Four stage processes ran concurrently, so latency is observed elapsed time rather than a controlled throughput benchmark.

Requirements/Use Cases/Test Cases used one iteration, zero retries and a 90-second limit. Test Cases used a four-field template (`id`, `title`, `steps`, `expected_result`). Automation had no selector evidence. These small fixtures are a smoke benchmark, not proof for other domains, templates or grounded browser environments.

Codex reviewed Requirements wording and Use Case objectives against the source, independently of Gemini's self-ratings. Review was not blinded to configuration. Coverage counts stated source behaviors (three in auth, four in expenses). The assumptions metric counts artifact items containing at least one assertion requiring source confirmation, not every repeated assertion. Corrections additionally count changed terminology for Requirements and vague objectives for Use Cases. Test data choices alone are not invented rules. Auxiliary model analysis is outside these primary-artifact scores.

Examples requiring confirmation include receipt enforcement timing, dual-role precedence, self-approval prohibitions, draft edit/queue behavior, undisclosed UI/error policies and rolling-window/reset details. These are conservative reviewer judgments, not statistically significant population estimates. Normal human review remains required.

## Matched results

Each row averages six samples. Cost is estimated per sample, including thinking tokens.

| Stage | Configuration | Coverage | Items needing source confirmation | Corrections | Seconds | USD |
|---|---|---:|---:|---:|---:|---:|
| Requirements | Baseline | 100% | 0.333 | 0.500 | 9.412 | 0.00895 |
| Requirements | Skills only | 100% | 0.333 | 0.500 | 11.905 | 0.00945 |
| Requirements | Memory only | 100% | 0.000 | 0.333 | 12.141 | 0.00884 |
| Requirements | Combined | 100% | 0.000 | 0.167 | 9.151 | 0.00993 |
| Use Cases | Baseline | 100% | 6.000 | 8.167 | 20.354 | 0.02519 |
| Use Cases | Skills only | 100% | 4.833 | 7.167 | 20.972 | 0.02772 |
| Use Cases | Memory only | 100% | 5.667 | 7.500 | 18.770 | 0.02378 |
| Use Cases | Combined | 100% | 4.833 | 6.667 | 18.244 | 0.02389 |

Combined Requirements reduced total corrections from 3 to 1 across six runs, at roughly 11% higher estimated cost. Skills alone showed no quality improvement there. Combined Use Cases reduced source-confirmation items from 36 to 29 and corrections from 49 to 40, at roughly 5% lower estimated cost. Its expense fixture had unchanged aggregate source-confirmation counts, and the third expense repeat regressed by adding an unsupported self-approval rule. These results support a monitored local pilot, not a claim that every run improves.

Google's introductory rates are $0.75 per million input tokens and $3.75 per million output tokens through December 31, 2026 ([official documentation](https://ai.google.dev/gemini-api/docs/latest-model)). Recorded usage: 264 successful calls, 986,747 input tokens, 587,559 output/thinking tokens, no missing usage records. Total estimated cost: **$2.943402**. Actual billing may differ due to credits, caching or other adjustments.

## Failed correctness gates

| Stage | Configuration | Contract failures / 6 | Seconds | USD per sample |
|---|---|---:|---:|---:|
| Test Cases | Baseline | 2 | 43.542 | 0.07152 |
| Test Cases | Skills only | 1 | 43.670 | 0.07125 |
| Test Cases | Memory only | 3 | 43.901 | 0.06866 |
| Test Cases | Combined | 5 | 38.309 | 0.06701 |
| Automation | Baseline | 0 | 11.320 | 0.01477 |
| Automation | Skills only | 0 | 11.316 | 0.01292 |
| Automation | Memory only | 2 | 16.043 | 0.02175 |
| Automation | Combined | 2 | 20.111 | 0.02496 |

Test Cases had **11/24** delivered-contract failures. Object-valued `test_data` was rejected as a non-string, silently discarding cases after evidence counts were computed. `auth/test_cases/combined/1` claimed 12 final cases but returned 2 deterministic cases; another combined sample returned none. Missing output is not scored as zero assumptions or better latency/cost. Full quality adjudication is withheld because the delivered contract fails. Fix and rerun in **#280**.

Automation had **4/24** syntax/completeness failures, including 2/6 combined samples. Python was parsed/compiled per file as data; no generated code was executed. Valid combined samples skipped cases without selector evidence, but still included guessed draft locators and the adapter reported the cases as generated. Other configurations emitted ungrounded executable locators or disabled TLS verification. Syntax validity alone does not pass grounding. Fix output validation and truthful manual diagnostics in **#281**, then rerun with both missing and supplied selector evidence.

## Local enablement

| Entry point | Skills | Memory | Decision |
|---|---|---|---|
| Requirements | On | On | Limited combined-configuration pilot |
| Use Cases | On | On | Limited pilot with explicit human review |
| Test Cases | Off | Off | Held on #280 |
| Automation | Off | Off | Held on #281 |

The ignored local `.env` now has `ADK_SKILLS_ENABLED=true`, `ADK_MEMORY_ENABLED=true` and `ADK_GUIDANCE_STAGES=requirements,use_cases`. Defaults remain disabled for other installations. The local backend was restarted and health verified. Authenticated Context displays both flags on for Requirements/Use Cases and off for Test Cases/Automation. The existing project remains revision 5 with no knowledge entries added.

The stage gate applies to the entry point itself. Held Test Cases runs cannot inherit newly enabled Use Cases guidance through their internal planner. Once explicitly enabled, Test Cases can select both stages and inject by agent role. Existing supplied/approved artifacts remain normal source inputs.

Rollback: set both feature flags to false and restart the backend; no knowledge deletion or artifact rewrite is needed. No deployment, IAM change, automatic approval or history import accompanied this pilot. Keep #274 and the epic open pending held-stage fixes and broader rollout criteria.

## Verification and evidence

- 419 backend tests; Ruff lint/format; strict Requirements, Generation and Orchestrator offline benchmarks; OpenAPI and generated contract check passed.
- Frontend build/lint/format passed with the existing bundle-size advisory; **175 required workflow browser tests passed**, including the enabled/held stage labels and accessible knowledge dialogs.
- Earlier full frontend run: 186 passed; the legacy Home-upload test failed (#251), unrelated to guidance.
- The evaluator checkpoints samples, excludes explicit fallbacks, validates delivered case counts/coverage, and validates Python syntax/test representation. These checks do not modify model outputs during adjudication.
- Exact hashes, rubric notes and every arm (including failures) are retained locally in `.execution_artifacts/guidance-evaluation-2026-09-11/` with file checksums. Generated evidence is ignored by Git. Reproduction commands are in `docs/agent-knowledge.md`.
