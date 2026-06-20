import { expect, test } from "@playwright/test";

import { sampleRequirementsFile, seedAuthenticatedSession } from "./support/auth.js";

const PROJECT_ID = "project-lifecycle";
const PROJECT_NAME = "Lifecycle QA";

function jsonResponse(route, payload, status = 200, headers = {}) {
	return route.fulfill({
		status,
		contentType: "application/json",
		headers,
		body: JSON.stringify(payload),
	});
}

function requirement(index, changed = false) {
	const id = `REQ-${String(index).padStart(3, "0")}`;
	return {
		id,
		text: changed
			? `${id} changed payment retry and approval behavior shall be validated.`
			: `${id} baseline checkout behavior shall be validated.`,
		review_status: "Approved",
	};
}

function requirements(changed = false) {
	return Array.from({ length: 10 }, (_, offset) => {
		const index = offset + 1;
		const id = `REQ-${String(index).padStart(3, "0")}`;
		return requirement(index, changed && (id === "REQ-003" || id === "REQ-010"));
	});
}

function coveragePlan(items = requirements()) {
	return items.map((item) => ({
		requirement_id: item.id,
		requirement_text: item.text,
		scenarios: [
			{
				id: `${item.id}-SCN-01`,
				requirement_id: item.id,
				scenario_type: "Happy Path",
				title: `${item.id} primary checkout behavior`,
				objective: `Validate ${item.text}`,
				priority: "High",
				must_have: true,
			},
		],
	}));
}

function testCase(requirementId, version = 1) {
	return {
		id: `TC-${requirementId.slice(-3)}`,
		title: `${requirementId} checkout regression coverage`,
		description: `Coverage for ${requirementId}`,
		priority: "High",
		type: "Regression",
		status: "Ready",
		preconditions: "A signed-in user can access checkout.",
		steps: [{ step: 1, action: `Exercise ${requirementId}`, expected: "The behavior is correct", test_data: null }],
		expected_result: "The behavior satisfies the requirement.",
		test_data: null,
		estimated_time: "5 mins",
		automation_status: "Automated",
		component: "Checkout",
		tags: [requirementId, ...(version > 1 ? ["impact:update"] : [])],
		linked_requirement_ids: [requirementId],
		scenario_refs: [`${requirementId}-SCN-01`],
		artifact_set_id: "tc-set-lifecycle",
		artifact_item_id: `tc-item-${requirementId.slice(-3)}`,
		artifact_version_id: `tc-version-${requirementId.slice(-3)}-${version}`,
		artifact_version_number: version,
	};
}

function testCases(afterApply = false) {
	return requirements().map((item) => testCase(item.id, afterApply && ["REQ-003", "REQ-010"].includes(item.id) ? 2 : 1));
}

function review() {
	return {
		approved: true,
		score: 100,
		threshold: 90,
		summary: "Approved.",
		blocking_issues: [],
		suggestions: [],
		unmet_criteria: [],
	};
}

function projectSummary(project) {
	return {
		project_id: project.project_id,
		name: project.name,
		description: project.description,
		status: project.status,
		owner_user_id: project.owner_user_id,
		current_revision: project.current_revision,
		created_at: project.created_at,
		updated_at: project.updated_at,
		stage_state: project.stage_state,
	};
}

function impactAnalysisPayload() {
	const changedItems = ["REQ-003", "REQ-010"].map((requirementId) => ({
		item_id: requirementId,
		kind: "requirement",
		change_type: "modified",
		title: `${requirementId} modified`,
		current_text: `${requirementId} changed payment retry and approval behavior shall be validated.`,
		previous_text: `${requirementId} baseline checkout behavior shall be validated.`,
		approved: true,
		requirement_id: requirementId,
		scenario_ids: [],
	}));
	const impactedCases = changedItems.map((item) => ({
		test_case_id: `TC-${item.requirement_id.slice(-3)}`,
		title: `${item.requirement_id} checkout regression coverage`,
		impact_source: "direct",
		linked_requirement_ids: [item.requirement_id],
		scenario_refs: [`${item.requirement_id}-SCN-01`],
		reason: `Direct traceability match via linked requirements: ${item.requirement_id}`,
	}));
	const recommendations = requirements().map((item) => {
		const changed = ["REQ-003", "REQ-010"].includes(item.id);
		return {
			recommendation_id: `impact-${changed ? "update" : "keep"}-TC-${item.id.slice(-3)}`,
			action: changed ? "update" : "keep",
			title: `${changed ? "Update" : "Keep"} TC-${item.id.slice(-3)}`,
			reason: changed ? `Direct traceability match via linked requirements: ${item.id}` : "No direct or semantic impact detected.",
			confidence: changed ? 0.86 : 0.93,
			accepted: true,
			impact_source: "direct",
			test_case_id: `TC-${item.id.slice(-3)}`,
			requirement_id: item.id,
			scenario_refs: [`${item.id}-SCN-01`],
		};
	});
	return {
		baseline_snapshot_ids: { requirements: "snap-req-v1", context: null, use_cases: "snap-use-v1", test_cases: "snap-test-v1" },
		current_snapshot_ids: { requirements: "snap-req-v2", context: null, use_cases: "snap-use-v1", test_cases: "snap-test-v1" },
		changed_items: changedItems,
		impacted_test_cases: impactedCases,
		recommendations,
		summary: {
			changed_item_count: 2,
			added_count: 0,
			modified_count: 2,
			removed_count: 0,
			unchanged_requirement_count: 8,
			directly_impacted_test_case_count: 2,
			semantic_neighbor_count: 0,
			recommendation_counts: { keep: 8, update: 2, add: 0, deprecate: 0 },
		},
	};
}

function baseProject(currentRevision = 0) {
	return {
		project_id: PROJECT_ID,
		name: PROJECT_NAME,
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: currentRevision,
		created_at: "2026-06-13T09:00:00Z",
		updated_at: "2026-06-13T09:00:00Z",
		stage_state: {},
		current_snapshots: {},
		timeline: [],
		execution_runs: [],
	};
}

function snapshot(stage, version, revision, operation, payload, approved = true, metadata = {}) {
	return {
		snapshot_id: `snap-${stage.replaceAll("_", "-")}-v${version}`,
		project_id: PROJECT_ID,
		stage,
		version,
		project_revision: revision,
		operation,
		approved,
		payload,
		metadata,
		created_at: "2026-06-13T09:00:00Z",
	};
}

function projectForPhase(phase) {
	const project = baseProject(0);
	if (phase === "empty") {
		return project;
	}
	const v1Requirements = requirements();
	project.current_revision =
		phase === "requirements"
			? 1
			: phase === "suite"
				? 4
				: phase === "stale"
					? 5
					: phase === "analysis"
						? 6
						: phase === "applied"
							? 7
							: phase === "executed"
								? 8
								: 9;
	project.stage_state.requirements = {
		current_snapshot_id: phase === "requirements" || phase === "suite" ? "snap-requirements-v1" : "snap-requirements-v2",
		version: phase === "requirements" || phase === "suite" ? 1 : 2,
		approved: true,
		stale: false,
		metadata: { requirement_count: 10 },
	};
	project.current_snapshots.requirements =
		phase === "requirements" || phase === "suite"
			? snapshot("requirements", 1, 1, "requirements.parse", { requirements: v1Requirements, review: review() }, true, {
					requirement_count: 10,
				})
			: snapshot("requirements", 2, 5, "requirements.refine", { requirements: requirements(true), review: review() }, true, {
					requirement_count: 10,
				});
	if (phase === "requirements") {
		return project;
	}
	project.stage_state.use_cases = {
		current_snapshot_id: "snap-use-cases-v1",
		version: 1,
		approved: true,
		stale: ["stale", "analysis"].includes(phase),
		stale_reason: ["stale", "analysis"].includes(phase) ? "requirements changed in project revision 5" : null,
		metadata: { scenario_count: 10 },
	};
	project.current_snapshots.use_cases = snapshot(
		"use_cases",
		1,
		2,
		"testcases.generate.use_cases",
		{ coverage_plan: coveragePlan(v1Requirements), requirement_analysis: [] },
		true,
		{ scenario_count: 10 }
	);
	const hasAnalysis = ["analysis", "applied", "executed", "reported"].includes(phase);
	const afterApply = ["applied", "executed", "reported"].includes(phase);
	if (hasAnalysis) {
		project.stage_state.impact_analysis = {
			current_snapshot_id: "snap-impact-analysis-v1",
			version: 1,
			approved: false,
			stale: false,
			metadata: { changed_item_count: 2, recommendation_counts: { keep: 8, update: 2, add: 0, deprecate: 0 } },
		};
		project.current_snapshots.impact_analysis = snapshot("impact_analysis", 1, 6, "impact.analysis", impactAnalysisPayload(), false, {
			changed_item_count: 2,
		});
	}
	project.stage_state.test_cases = {
		current_snapshot_id: afterApply ? "snap-test-cases-v2" : "snap-test-cases-v1",
		version: afterApply ? 2 : 1,
		approved: true,
		stale: ["stale", "analysis"].includes(phase),
		stale_reason: ["stale", "analysis"].includes(phase) ? "requirements changed in project revision 5" : null,
		metadata: afterApply ? { test_case_count: 10, preserved_count: 8, updated_count: 2 } : { test_case_count: 10 },
	};
	project.current_snapshots.test_cases = snapshot(
		"test_cases",
		afterApply ? 2 : 1,
		afterApply ? 7 : 4,
		afterApply ? "impact.update.apply" : "testcases.generate",
		{
			test_cases: testCases(afterApply),
			coverage_plan: coveragePlan(v1Requirements),
			requirement_analysis: [],
			impact_analysis: afterApply ? impactAnalysisPayload() : null,
			impact_update_result: afterApply
				? {
						preserved_count: 8,
						updated_count: 2,
						added_count: 0,
						deprecated_count: 0,
						applied_recommendation_ids: impactAnalysisPayload().recommendations.map((item) => item.recommendation_id),
					}
				: null,
			review: review(),
		},
		true,
		{ test_case_count: 10 }
	);
	if (["executed", "reported"].includes(phase)) {
		project.stage_state.execution = {
			current_snapshot_id: "snap-execution-v1",
			version: 1,
			approved: true,
			stale: false,
			metadata: { status: "passed", target_environment: "staging", run_id: "run-staging" },
		};
		project.current_snapshots.execution = snapshot(
			"execution",
			1,
			8,
			"automation.execution.run",
			{ run_id: "run-staging", status: "passed", target_environment: "staging", summary: { passed: 1, failed: 0 } },
			true,
			{ status: "passed", run_id: "run-staging", target_environment: "staging" }
		);
		project.execution_runs = [
			{
				run_record_id: "record-staging",
				project_id: PROJECT_ID,
				run_id: "run-staging",
				target_environment: "staging",
				target_base_url: "https://staging.example.test/app",
				project_revision: 8,
				test_case_count: 1,
				status: "passed",
				summary: { passed: 1, failed: 0, invalid: 0, skipped: 0 },
				snapshot_id: "snap-execution-v1",
				source_snapshot_id: "snap-test-cases-v2",
				selected_test_case_ids: ["TC-001"],
				request_id: "req-run",
				created_at: "2026-06-13T09:08:00Z",
			},
		];
	}
	if (phase === "reported") {
		project.stage_state.reports = {
			current_snapshot_id: "snap-reports-v1",
			version: 1,
			approved: true,
			stale: false,
			metadata: { format: "json" },
		};
		project.current_snapshots.reports = snapshot(
			"reports",
			1,
			9,
			"export.json",
			{
				format: "json",
				evidence: {
					source_snapshot_ids: {
						requirements: "snap-requirements-v2",
						test_cases: "snap-test-cases-v2",
						execution: "snap-execution-v1",
					},
					execution_run_ids: ["run-staging"],
				},
			},
			true,
			{ format: "json" }
		);
	}
	return project;
}

function action(
	action,
	label,
	stage,
	{ primary = false, secondary = false, enabled = true, reason = "Ready to run.", blockers = [] } = {}
) {
	return {
		action,
		label,
		stage,
		enabled,
		primary,
		secondary,
		reason,
		blockers,
		agent_kind:
			action === "report"
				? "report"
				: action === "automate" || action === "execute"
					? "automation"
					: action === "analyze_impact" || action === "apply_update"
						? "impact"
						: "test_cases",
		agent_contract_version: "2026-06-13.v1",
		agent_implementation: "local",
	};
}

function stage(stage, status, version = 0, extra = {}) {
	return {
		stage,
		status,
		version,
		approved: status === "completed",
		stale: status === "stale",
		summary: {},
		blockers: [],
		...extra,
	};
}

function statusForPhase(phase) {
	const project = projectForPhase(phase);
	const base = {
		project_id: PROJECT_ID,
		project_revision: project.current_revision,
		current_stage: "requirements",
		stages: {
			requirements: stage(
				"requirements",
				project.stage_state.requirements ? "completed" : "not_started",
				project.stage_state.requirements?.version || 0
			),
			use_cases: stage(
				"use_cases",
				project.stage_state.use_cases?.stale ? "stale" : project.stage_state.use_cases ? "completed" : "not_started",
				project.stage_state.use_cases?.version || 0
			),
			impact_analysis: stage(
				"impact_analysis",
				project.stage_state.impact_analysis ? "completed" : "not_started",
				project.stage_state.impact_analysis?.version || 0,
				{
					summary: project.stage_state.impact_analysis?.metadata || {},
				}
			),
			test_cases: stage(
				"test_cases",
				project.stage_state.test_cases?.stale ? "stale" : project.stage_state.test_cases ? "completed" : "not_started",
				project.stage_state.test_cases?.version || 0
			),
			automation: stage("automation", project.stage_state.test_cases && !project.stage_state.test_cases.stale ? "ready" : "not_started"),
			execution: stage(
				"execution",
				project.stage_state.execution ? "completed" : "not_started",
				project.stage_state.execution?.version || 0,
				{
					summary: project.stage_state.execution?.metadata || {},
				}
			),
			review: stage("review", project.stage_state.test_cases && !project.stage_state.test_cases.stale ? "completed" : "not_started"),
			reports: stage("reports", project.stage_state.reports ? "completed" : "not_started", project.stage_state.reports?.version || 0),
		},
		next_actions: [],
		blockers: [],
		has_baseline_test_suite: Boolean(project.stage_state.test_cases),
		upstream_changed: ["stale", "analysis"].includes(phase),
		changed_upstream_stages: ["stale", "analysis"].includes(phase) ? ["requirements"] : [],
		generated_at: "2026-06-13T09:00:00Z",
	};
	if (phase === "empty") {
		base.current_stage = "requirements";
		base.next_actions = [action("refine", "Refine Requirements", "requirements", { primary: true })];
		return base;
	}
	if (phase === "requirements") {
		base.current_stage = "test_cases";
		base.next_actions = [action("generate", "Generate First Test Suite", "test_cases", { primary: true })];
		return base;
	}
	if (phase === "stale") {
		base.current_stage = "impact_analysis";
		base.next_actions = [
			action("analyze_impact", "Analyze Impact", "impact_analysis", { primary: true }),
			action("full_regenerate", "Full Regenerate", "test_cases", { secondary: true }),
		];
		return base;
	}
	if (phase === "analysis") {
		base.current_stage = "test_cases";
		base.next_actions = [action("apply_update", "Apply Accepted Updates", "test_cases", { primary: true })];
		return base;
	}
	if (phase === "executed") {
		base.current_stage = "reports";
		base.next_actions = [
			action("report", "Create Evidence Report", "reports", { primary: true }),
			action("review", "Review Evidence", "review", { secondary: true }),
		];
		return base;
	}
	base.current_stage = "automation";
	base.next_actions = [
		action("automate", "Preview Automation", "automation", { primary: true }),
		action("report", "Create Test Case Report", "reports", { secondary: true }),
		action("review", "Review Evidence", "review", { secondary: true }),
	];
	return base;
}

function runsForPhase(phase) {
	const events = [];
	if (["suite", "stale", "analysis", "applied", "executed", "reported"].includes(phase)) {
		events.push({
			event_id: "event-generate",
			run_id: "run-generate",
			project_id: PROJECT_ID,
			event_type: "action_completed",
			summary: "Generated v1 baseline suite.",
			action: "generate",
			stage: "test_cases",
			project_revision: 4,
			checkpoint_id: "checkpoint-generate",
			occurred_at: "2026-06-13T09:04:00Z",
		});
	}
	if (["analysis", "applied", "executed", "reported"].includes(phase)) {
		events.push({
			event_id: "event-impact",
			run_id: "run-impact",
			project_id: PROJECT_ID,
			event_type: "agent_invoked",
			summary: "Impact agent identified 2 changed items.",
			action: "analyze_impact",
			stage: "impact_analysis",
			project_revision: 6,
			checkpoint_id: "checkpoint-impact",
			occurred_at: "2026-06-13T09:06:00Z",
		});
	}
	return {
		runs: events.length
			? [
					{
						run_id: events.at(-1).run_id,
						project_id: PROJECT_ID,
						action: events.at(-1).action,
						status: "completed",
						current_stage: events.at(-1).stage,
						current_action: events.at(-1).action,
						project_revision: events.at(-1).project_revision,
						request_id: "req-run",
						actor_user_id: "playwright-e2e-user",
						idempotency_key: `${events.at(-1).action}:req-run`,
						current_checkpoint_id: events.at(-1).checkpoint_id,
						produced_snapshot_ids: {},
						execution_run_ids: phase === "executed" || phase === "reported" ? ["run-staging"] : [],
						blockers: [],
						metadata: {},
						started_at: events.at(-1).occurred_at,
						updated_at: events.at(-1).occurred_at,
						completed_at: events.at(-1).occurred_at,
					},
				]
			: [],
		events,
		checkpoints: events.map((event) => ({
			checkpoint_id: event.checkpoint_id,
			run_id: event.run_id,
			project_id: PROJECT_ID,
			action: event.action,
			stage: event.stage,
			project_revision: event.project_revision,
			source_snapshot_ids: { requirements: "snap-requirements-v2", test_cases: "snap-test-cases-v1" },
			output_snapshot_ids:
				event.stage === "impact_analysis" ? { impact_analysis: "snap-impact-analysis-v1" } : { test_cases: "snap-test-cases-v1" },
			agent_output_refs: [],
			execution_run_ids: phase === "executed" || phase === "reported" ? ["run-staging"] : [],
			blockers: [],
			next_action: event.stage === "impact_analysis" ? "apply_update" : "analyze_impact",
			metadata: {},
			updated_at: event.occurred_at,
		})),
	};
}

async function mockLifecycleApi(page) {
	let phase = "empty";
	let parseCount = 0;
	const projects = () => (phase === "none" ? [] : [projectSummary(projectForPhase(phase))]);

	await page.route("**/*", async (route) => {
		const url = new URL(route.request().url());
		const method = route.request().method();
		if (
			url.port === "5173" ||
			url.pathname.startsWith("/@") ||
			url.pathname.startsWith("/src/") ||
			url.pathname.startsWith("/node_modules/")
		) {
			return route.fallback();
		}
		if (url.pathname === "/auth/me") {
			return jsonResponse(route, {
				sub: "playwright-e2e-user",
				email: "playwright-e2e@example.com",
				name: "Playwright E2E",
				picture: null,
			});
		}
		if (url.pathname === "/reports/usage/me") {
			return jsonResponse(route, { groups: [] });
		}
		if (url.pathname === "/entitlements/me") {
			return jsonResponse(route, {
				account: { plan_tier: "premium", support_contact_email: "hello@spica-digital.eu" },
				requirements: { remaining: 500, exhausted: false },
				test_cases: { remaining: 500, exhausted: false },
				wallet: { balance_units: 5000, balance_token_display: "5000" },
				shadow_mode: false,
			});
		}
		if (url.pathname.startsWith("/integrations/")) {
			return jsonResponse(route, { connected: false, connection: null });
		}
		if (url.pathname === "/projects" && method === "GET") {
			return jsonResponse(route, { projects: projects() });
		}
		if (url.pathname === "/projects" && method === "POST") {
			phase = "empty";
			return jsonResponse(route, projectForPhase(phase));
		}
		if (url.pathname === `/projects/${PROJECT_ID}` && method === "GET") {
			return jsonResponse(route, projectForPhase(phase));
		}
		if (url.pathname === `/projects/${PROJECT_ID}/orchestrator/status`) {
			return jsonResponse(route, statusForPhase(phase));
		}
		if (url.pathname === `/projects/${PROJECT_ID}/orchestrator/runs`) {
			return jsonResponse(route, runsForPhase(phase));
		}
		if (url.pathname === "/requirements/parse") {
			parseCount += 1;
			phase = parseCount === 1 ? "requirements" : "stale";
			const changed = parseCount > 1;
			return jsonResponse(route, {
				raw_text: "Synthetic lifecycle requirements.",
				requirements: requirements(changed),
				review: review(),
				coverage_metrics: { total_requirements: 10, approved_count: 10, shall_format_ratio: 1.0 },
				workflow_diagnostics: null,
				workflow_settings: null,
				iteration_history: [],
			});
		}
		if (url.pathname === "/testcases/generate") {
			phase = "suite";
			return jsonResponse(route, {
				test_cases: testCases(false),
				requirement_analysis: [],
				coverage_plan: coveragePlan(requirements()),
				coverage_metrics: {
					traceability_coverage_ratio: 1.0,
					scenario_coverage_ratio: 1.0,
					requirements_without_tests: [],
				},
				review: review(),
				workflow_diagnostics: null,
				workflow_settings: null,
				iteration_history: [],
			});
		}
		if (url.pathname === `/projects/${PROJECT_ID}/impact-analysis`) {
			phase = "analysis";
			return jsonResponse(route, projectForPhase(phase));
		}
		if (url.pathname === `/projects/${PROJECT_ID}/impact-update/apply`) {
			phase = "applied";
			return jsonResponse(route, projectForPhase(phase));
		}
		if (url.pathname === "/automation/execution/preview") {
			return jsonResponse(route, {
				executable: [
					{
						id: "candidate-TC-001",
						source_test_case_id: "TC-001",
						title: "TC-001 checkout regression coverage",
						status: "executable",
						spec: { steps: [{ action: "Open checkout" }] },
						traceability_ids: ["REQ-001"],
					},
				],
				manual: [],
				unsupported: [],
				invalid: [],
				warnings: [],
				summary: { executable: 1, manual: 0, unsupported: 0, invalid: 0 },
			});
		}
		if (url.pathname === "/automation/execution/run") {
			phase = "executed";
			return jsonResponse(route, {
				status: "passed",
				run_id: "run-staging",
				results: [{ id: "result-TC-001", source_test_case_id: "TC-001", title: "TC-001 checkout regression coverage", status: "passed" }],
				preview: {
					executable: [],
					manual: [],
					unsupported: [],
					invalid: [],
					warnings: [],
					summary: { executable: 0, manual: 0, unsupported: 0, invalid: 0 },
				},
				warnings: [],
				summary: { passed: 1, failed: 0, invalid: 0, skipped: 0 },
			});
		}
		if (url.pathname === "/export/json") {
			phase = "reported";
			return jsonResponse(
				route,
				{
					test_cases: testCases(true),
					evidence: {
						source_snapshot_ids: { requirements: "snap-requirements-v2", test_cases: "snap-test-cases-v2", execution: "snap-execution-v1" },
						execution_run_ids: ["run-staging"],
					},
				},
				200,
				{ "content-disposition": 'attachment; filename="test_cases.json"' }
			);
		}
		return route.fallback();
	});
}

test.describe("Orchestrator lifecycle validation", () => {
	test("create, generate, resume, impact update, execute, review, and report", async ({ page }) => {
		await mockLifecycleApi(page);
		await seedAuthenticatedSession(page);
		await page.goto("/");
		await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });

		await page.getByPlaceholder("New QA project name").fill(PROJECT_NAME);
		await page.getByRole("button", { name: /^New Project$/ }).click();
		await expect(page.getByText(/Lifecycle QA · revision 0/)).toBeVisible();

		await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
		await page.getByRole("button", { name: /^Parse Requirements$/ }).click();
		await expect(page.getByLabel("Review status for REQ-001")).toHaveValue("Approved");
		const approveButton = page.getByRole("button", { name: /approve non-rejected/i });
		if (await approveButton.isVisible().catch(() => false)) {
			await approveButton.click();
		}

		await page
			.getByRole("navigation", { name: "Workflow navigation" })
			.getByRole("button", { name: /^Generate,/i })
			.click();
		await page.getByRole("button", { name: /^Generate from 10 Approved$/ }).click();
		await expect(page.locator(".generate-results-summary-pill", { hasText: "10 test cases" })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByText(/Generated v1 baseline suite/i)).toBeVisible();

		await page.reload();
		await expect(page.getByText(/Lifecycle QA · revision 4/)).toBeVisible({ timeout: 30_000 });
		await expect(
			page.getByLabel("Orchestrator Cockpit").locator(".orchestrator-summary-grid div", { hasText: "Baseline suite" })
		).toContainText("Present");

		await page
			.getByRole("navigation", { name: "Workflow navigation" })
			.getByRole("button", { name: /^Upload,/i })
			.click();
		await page
			.getByPlaceholder(/Enter your feedback here/i)
			.fill("Change REQ-003 and REQ-010 to include payment retry and approval behavior.");
		await page.getByRole("button", { name: /Implement Changes/i }).click();

		await page
			.getByRole("navigation", { name: "Workflow navigation" })
			.getByRole("button", { name: /^Generate,/i })
			.click();
		await expect(page.getByRole("button", { name: /Analyze Impact for 2 Changed Items/i })).toBeVisible();
		await page.getByRole("button", { name: /Analyze Impact for 2 Changed Items/i }).click();
		await expect(page.getByRole("heading", { name: /^Impact Analysis$/i })).toBeVisible();
		await expect(page.getByText("REQ-003 modified")).toBeVisible();
		await expect(page.getByText("Update TC-010")).toBeVisible();
		await page.getByRole("button", { name: /Apply 10 Accepted Recommendations/i }).click();
		await expect(page.getByText(/Impact update applied: 8 preserved, 2 updated, 0 added, 0 deprecated/i)).toBeVisible();

		await page
			.getByRole("navigation", { name: "Workflow navigation" })
			.getByRole("button", { name: /^Automation,/i })
			.click();
		await page.getByPlaceholder("staging, dev, customer-a").fill("staging");
		await page.getByPlaceholder("Use backend default").fill("https://staging.example.test/app");
		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();
		await page.getByRole("button", { name: /^Run 1 Candidate$/ }).click();
		await expect(page.getByText(/Execution passed: 1 passed, 0 failed/i)).toBeVisible();

		const cockpit = page.getByLabel("Orchestrator Cockpit");
		await cockpit.getByRole("button", { name: /^Refresh$/ }).click();
		await expect(cockpit.getByRole("button", { name: /^Review Evidence$/ })).toBeVisible();
		await cockpit.getByRole("button", { name: /^Review Evidence$/ }).click();
		await expect(page.getByText(/Approved for export/i)).toBeVisible();

		await cockpit.getByRole("button", { name: /^Create Evidence Report$/ }).click();
		await expect(page.getByRole("heading", { name: /^Export Test Cases$/ })).toBeVisible();
		await page.getByRole("button", { name: /JSON/i }).click();
		await expect(page.getByText(/Exported to JSON successfully/i)).toBeVisible();
		await expect(page.locator(".project-history-block", { hasText: "Latest Report" })).toContainText("snap-reports-v1");
		await expect(page.locator(".project-history-block", { hasText: "Latest Report" })).toContainText("run-staging");
	});
});
