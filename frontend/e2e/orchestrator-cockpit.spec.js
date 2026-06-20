import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./support/auth.js";

const STORAGE_CURRENT_PROJECT_ID = "tcg.current.project_id";

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

function requirement(id, text) {
	return { id, text, review_status: "Approved" };
}

function testCase(requirementId, title) {
	return {
		id: `TC-${requirementId.slice(-3)}`,
		title,
		description: `Coverage for ${requirementId}`,
		priority: "High",
		type: "Regression",
		status: "Ready",
		preconditions: "A signed-in user exists.",
		steps: [{ step: 1, action: `Exercise ${requirementId}`, expected: "Behavior is correct", test_data: null }],
		expected_result: "The behavior satisfies the requirement.",
		test_data: null,
		estimated_time: "5 mins",
		automation_status: "Manual",
		component: "Checkout",
		tags: [requirementId],
		linked_requirement_ids: [requirementId],
		scenario_refs: [`${requirementId}-SCN-01`],
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

function firstTimeProject() {
	return {
		project_id: "project-first",
		name: "New Checkout QA",
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: 1,
		created_at: "2026-06-12T00:00:00Z",
		updated_at: "2026-06-12T00:00:00Z",
		stage_state: {
			requirements: {
				current_snapshot_id: "snap-req-first",
				version: 1,
				approved: true,
				stale: false,
				metadata: {},
			},
		},
		current_snapshots: {
			requirements: {
				snapshot_id: "snap-req-first",
				project_id: "project-first",
				stage: "requirements",
				version: 1,
				project_revision: 1,
				operation: "requirements.parse",
				approved: true,
				payload: {
					requirements: [requirement("REQ-001", "REQ-001 checkout users can submit payment")],
					review: { approved: true, score: 96, threshold: 85, summary: "Approved.", blocking_issues: [] },
				},
				metadata: {},
				created_at: "2026-06-12T00:00:00Z",
			},
		},
		timeline: [],
		execution_runs: [],
	};
}

function staleImpactProject({ withAnalysis = false } = {}) {
	const changedRequirements = [
		requirement("REQ-001", "REQ-001 checkout users can submit payment"),
		requirement("REQ-003", "REQ-003 checkout approvals include retry handling"),
		requirement("REQ-010", "REQ-010 declined cards show recovery guidance"),
	];
	const impactAnalysis = {
		changed_items: [
			{
				item_id: "REQ-003",
				kind: "requirement",
				change_type: "modified",
				title: "REQ-003 modified",
				approved: true,
				requirement_id: "REQ-003",
			},
			{
				item_id: "REQ-010",
				kind: "requirement",
				change_type: "modified",
				title: "REQ-010 modified",
				approved: true,
				requirement_id: "REQ-010",
			},
		],
		impacted_test_cases: [
			{ test_case_id: "TC-003", title: "REQ-003 retry coverage", impact_source: "direct", reason: "Direct traceability match." },
			{ test_case_id: "TC-010", title: "REQ-010 recovery coverage", impact_source: "direct", reason: "Direct traceability match." },
		],
		recommendations: [
			{
				recommendation_id: "impact-keep-TC-001",
				action: "keep",
				title: "Keep TC-001",
				reason: "No impact.",
				confidence: 0.91,
				accepted: true,
			},
			{
				recommendation_id: "impact-update-TC-003",
				action: "update",
				title: "Update TC-003",
				reason: "REQ-003 changed.",
				confidence: 0.84,
				accepted: true,
			},
			{
				recommendation_id: "impact-update-TC-010",
				action: "update",
				title: "Update TC-010",
				reason: "REQ-010 changed.",
				confidence: 0.83,
				accepted: true,
			},
		],
		summary: {
			changed_item_count: 2,
			modified_count: 2,
			added_count: 0,
			removed_count: 0,
			directly_impacted_test_case_count: 2,
			recommendation_counts: { keep: 1, update: 2, add: 0, deprecate: 0 },
		},
	};
	const currentSnapshots = {
		requirements: {
			snapshot_id: "snap-req-v2",
			project_id: "project-1",
			stage: "requirements",
			version: 2,
			project_revision: 5,
			operation: "requirements.refine",
			approved: true,
			payload: { requirements: changedRequirements },
			metadata: {},
			created_at: "2026-06-12T00:00:00Z",
		},
		test_cases: {
			snapshot_id: "snap-test-v1",
			project_id: "project-1",
			stage: "test_cases",
			version: 1,
			project_revision: 4,
			operation: "testcases.generate",
			approved: true,
			payload: {
				test_cases: [
					testCase("REQ-001", "REQ-001 preserved coverage"),
					testCase("REQ-003", "REQ-003 retry coverage"),
					testCase("REQ-010", "REQ-010 recovery coverage"),
				],
				review: { approved: true, score: 100, threshold: 0, summary: "Approved.", blocking_issues: [] },
			},
			metadata: { source_requirements_snapshot_id: "snap-req-v1" },
			created_at: "2026-06-12T00:00:00Z",
		},
	};
	if (withAnalysis) {
		currentSnapshots.impact_analysis = {
			snapshot_id: "snap-impact-v1",
			project_id: "project-1",
			stage: "impact_analysis",
			version: 1,
			project_revision: 6,
			operation: "impact.analysis",
			approved: false,
			payload: impactAnalysis,
			metadata: { changed_item_count: 2 },
			created_at: "2026-06-12T00:00:00Z",
		};
	}
	return {
		project_id: "project-1",
		name: "Impact QA",
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: withAnalysis ? 6 : 5,
		created_at: "2026-06-12T00:00:00Z",
		updated_at: "2026-06-12T00:00:00Z",
		stage_state: {
			requirements: { current_snapshot_id: "snap-req-v2", version: 2, approved: true, stale: false, metadata: {} },
			test_cases: {
				current_snapshot_id: "snap-test-v1",
				version: 1,
				approved: true,
				stale: !withAnalysis,
				stale_reason: withAnalysis ? null : "requirements changed",
				metadata: {},
			},
			impact_analysis: withAnalysis
				? { current_snapshot_id: "snap-impact-v1", version: 1, approved: false, stale: false, metadata: { changed_item_count: 2 } }
				: undefined,
		},
		current_snapshots: currentSnapshots,
		timeline: [],
		execution_runs: [],
	};
}

function statusForFirstProject() {
	return {
		project_id: "project-first",
		project_revision: 1,
		current_stage: "test_cases",
		stages: {
			requirements: {
				stage: "requirements",
				status: "completed",
				current_snapshot_id: "snap-req-first",
				version: 1,
				approved: true,
				stale: false,
				summary: {},
				blockers: [],
			},
			use_cases: { stage: "use_cases", status: "not_started", version: 0, approved: false, stale: false, summary: {}, blockers: [] },
			test_cases: { stage: "test_cases", status: "ready", version: 0, approved: false, stale: false, summary: {}, blockers: [] },
		},
		next_actions: [
			{
				action: "generate",
				label: "Generate Test Cases",
				stage: "test_cases",
				enabled: true,
				primary: true,
				secondary: false,
				reason: "Approved requirements are ready for the first suite.",
				blockers: [],
				agent_kind: "test_cases",
				agent_contract_version: "1.0",
				agent_implementation: "local",
			},
		],
		blockers: [],
		has_baseline_test_suite: false,
		upstream_changed: false,
		changed_upstream_stages: [],
		generated_at: "2026-06-12T00:00:00Z",
	};
}

function statusForStaleImpact({ withAnalysis = false } = {}) {
	return {
		project_id: "project-1",
		project_revision: withAnalysis ? 6 : 5,
		current_stage: "impact_analysis",
		stages: {
			requirements: {
				stage: "requirements",
				status: "completed",
				current_snapshot_id: "snap-req-v2",
				version: 2,
				approved: true,
				stale: false,
				summary: {},
				blockers: [],
			},
			impact_analysis: {
				stage: "impact_analysis",
				status: withAnalysis ? "completed" : "ready",
				current_snapshot_id: withAnalysis ? "snap-impact-v1" : null,
				version: withAnalysis ? 1 : 0,
				approved: false,
				stale: false,
				summary: { changed_item_count: 2 },
				blockers: [],
			},
			test_cases: {
				stage: "test_cases",
				status: withAnalysis ? "completed" : "stale",
				current_snapshot_id: "snap-test-v1",
				version: 1,
				approved: true,
				stale: !withAnalysis,
				stale_reason: withAnalysis ? null : "requirements changed",
				summary: {},
				blockers: withAnalysis
					? []
					: [
							{
								code: "stale_downstream_stage",
								message: "Test cases are stale because requirements changed.",
								stage: "test_cases",
								severity: "blocking",
							},
						],
			},
		},
		next_actions: withAnalysis
			? [
					{
						action: "apply_update",
						label: "Apply Accepted Updates",
						stage: "impact_analysis",
						enabled: true,
						primary: true,
						secondary: false,
						reason: "Impact analysis has accepted recommendations ready to apply.",
						blockers: [],
						agent_kind: "impact",
						agent_contract_version: "1.0",
						agent_implementation: "local",
					},
				]
			: [
					{
						action: "analyze_impact",
						label: "Analyze Impact",
						stage: "impact_analysis",
						enabled: true,
						primary: true,
						secondary: false,
						reason: "Changed requirements should be reviewed against the current suite.",
						blockers: [],
						agent_kind: "impact",
						agent_contract_version: "1.0",
						agent_implementation: "local",
					},
					{
						action: "full_regenerate",
						label: "Full Regenerate",
						stage: "test_cases",
						enabled: true,
						primary: false,
						secondary: true,
						reason: "Regenerate the whole suite if impact analysis is not enough.",
						blockers: [],
						agent_kind: "test_cases",
						agent_contract_version: "1.0",
						agent_implementation: "local",
					},
				],
		blockers: withAnalysis
			? []
			: [
					{
						code: "stale_downstream_stage",
						message: "Test cases are stale because requirements changed.",
						stage: "test_cases",
						severity: "blocking",
					},
				],
		has_baseline_test_suite: true,
		upstream_changed: !withAnalysis,
		changed_upstream_stages: withAnalysis ? [] : ["requirements"],
		generated_at: "2026-06-12T00:00:00Z",
	};
}

function runsForStaleImpact({ withAnalysis = false } = {}) {
	return {
		runs: [
			{
				run_id: "orchestrator-run-1",
				project_id: "project-1",
				action: withAnalysis ? "analyze_impact" : "generate",
				status: withAnalysis ? "running" : "completed",
				current_stage: withAnalysis ? "impact_analysis" : "test_cases",
				current_action: withAnalysis ? "analyze_impact" : "generate",
				project_revision: withAnalysis ? 6 : 5,
				request_id: "req-run",
				actor_user_id: "playwright-e2e-user",
				idempotency_key: "generate:req-run",
				current_checkpoint_id: withAnalysis ? "checkpoint-impact" : "checkpoint-tests",
				produced_snapshot_ids: withAnalysis ? { impact_analysis: "snap-impact-v1" } : { test_cases: "snap-test-v1" },
				execution_run_ids: [],
				blockers: [],
				metadata: {},
				started_at: "2026-06-12T00:00:00Z",
				updated_at: withAnalysis ? "2026-06-12T00:04:00Z" : "2026-06-12T00:02:00Z",
				completed_at: withAnalysis ? null : "2026-06-12T00:02:00Z",
			},
		],
		events: [
			{
				event_id: withAnalysis ? "event-impact" : "event-tests",
				run_id: "orchestrator-run-1",
				project_id: "project-1",
				event_type: withAnalysis ? "agent_invoked" : "action_completed",
				summary: withAnalysis ? "Impact agent identified 2 changed items." : "Test case agent produced baseline suite.",
				action: withAnalysis ? "analyze_impact" : "generate",
				stage: withAnalysis ? "impact_analysis" : "test_cases",
				project_revision: withAnalysis ? 6 : 5,
				checkpoint_id: withAnalysis ? "checkpoint-impact" : "checkpoint-tests",
				actor_user_id: "playwright-e2e-user",
				request_id: "req-run",
				metadata: {},
				occurred_at: withAnalysis ? "2026-06-12T00:04:00Z" : "2026-06-12T00:02:00Z",
			},
		],
		checkpoints: [
			{
				checkpoint_id: withAnalysis ? "checkpoint-impact" : "checkpoint-tests",
				run_id: "orchestrator-run-1",
				project_id: "project-1",
				action: withAnalysis ? "analyze_impact" : "generate",
				stage: withAnalysis ? "impact_analysis" : "test_cases",
				project_revision: withAnalysis ? 6 : 5,
				request_id: "req-run",
				actor_user_id: "playwright-e2e-user",
				source_snapshot_ids: { requirements: "snap-req-v2", test_cases: "snap-test-v1" },
				output_snapshot_ids: withAnalysis ? { impact_analysis: "snap-impact-v1" } : { test_cases: "snap-test-v1" },
				agent_output_refs: [],
				execution_run_ids: [],
				blockers: [],
				next_action: withAnalysis ? "apply_update" : "analyze_impact",
				metadata: {},
				updated_at: withAnalysis ? "2026-06-12T00:04:00Z" : "2026-06-12T00:02:00Z",
			},
		],
	};
}

async function mockShell(page, projects) {
	await page.route("**/auth/me", async (route) =>
		jsonResponse(route, {
			sub: "playwright-e2e-user",
			email: "playwright-e2e@example.com",
			name: "Playwright E2E",
			picture: null,
		})
	);
	await page.route("**/reports/usage/me", async (route) => jsonResponse(route, { groups: [] }));
	await page.route("**/entitlements/me", async (route) =>
		jsonResponse(route, {
			account: { plan_tier: "premium", support_contact_email: "hello@spica-digital.eu" },
			requirements: { remaining: 500, exhausted: false },
			test_cases: { remaining: 500, exhausted: false },
			wallet: { balance_units: 5000, balance_token_display: "5000" },
			shadow_mode: false,
		})
	);
	await page.route("**/integrations/**", async (route) => {
		const url = new URL(route.request().url());
		if (!url.pathname.startsWith("/integrations/")) {
			return route.fallback();
		}
		return jsonResponse(route, { connected: false, connection: null });
	});
	await page.route("**/projects", async (route) => jsonResponse(route, { projects: projects.map(projectSummary) }));
}

test.describe("Orchestrator cockpit", () => {
	test("first-time projects show generation without impact noise", async ({ page }) => {
		const project = firstTimeProject();

		await mockShell(page, [project]);
		await page.route("**/projects/project-first/orchestrator/status", async (route) => jsonResponse(route, statusForFirstProject()));
		await page.route("**/projects/project-first/orchestrator/runs", async (route) =>
			jsonResponse(route, { runs: [], events: [], checkpoints: [] })
		);
		await page.route("**/projects/project-first", async (route) => jsonResponse(route, project));

		await seedAuthenticatedSession(page);
		await page.addInitScript((storageKey) => window.localStorage.setItem(storageKey, "project-first"), STORAGE_CURRENT_PROJECT_ID);
		await page.goto("/");

		const cockpit = page.getByLabel("Orchestrator Cockpit");
		const rail = page.getByLabel("Project information rail");
		await expect(cockpit).toBeVisible({ timeout: 30_000 });
		await expect(rail.getByText(/Baseline suite/i)).toBeVisible();
		await expect(cockpit.getByRole("button", { name: /^Generate Test Cases$/i })).toBeVisible();
		await expect(cockpit.getByRole("button", { name: /Analyze Impact/i })).toHaveCount(0);
	});

	test("reopened stale projects show impact as primary path with durable timeline", async ({ page }) => {
		let currentProject = staleImpactProject();
		let currentStatus = statusForStaleImpact();
		let currentRuns = runsForStaleImpact();

		await mockShell(page, [currentProject]);
		await page.route("**/projects/project-1/orchestrator/status", async (route) => jsonResponse(route, currentStatus));
		await page.route("**/projects/project-1/orchestrator/runs", async (route) => jsonResponse(route, currentRuns));
		await page.route("**/projects/project-1/impact-analysis", async (route) => {
			currentProject = staleImpactProject({ withAnalysis: true });
			currentStatus = statusForStaleImpact({ withAnalysis: true });
			currentRuns = runsForStaleImpact({ withAnalysis: true });
			return jsonResponse(route, currentProject);
		});
		await page.route("**/projects/project-1", async (route) => jsonResponse(route, currentProject));

		await seedAuthenticatedSession(page);
		await page.addInitScript((storageKey) => window.localStorage.setItem(storageKey, "project-1"), STORAGE_CURRENT_PROJECT_ID);
		await page.goto("/");

		const cockpit = page.getByLabel("Orchestrator Cockpit");
		const rail = page.getByLabel("Project information rail");
		await expect(cockpit).toBeVisible({ timeout: 30_000 });
		await expect(rail.getByText(/Impact QA · revision 5/i)).toBeVisible();
		await expect(cockpit.getByRole("button", { name: /^Analyze Impact$/i })).toBeVisible();
		await expect(cockpit.getByRole("button", { name: /^Full Regenerate$/i })).toBeVisible();
		await expect(rail.getByLabel("Status overview")).toContainText("Stale");
		await expect(rail.getByLabel("Stage progress")).toContainText("Impact Analysis");
		await expect(rail.getByLabel("Blockers")).toContainText("1");
		await expect(rail.getByText("Test cases are stale because requirements changed.")).toBeVisible();
		await expect(rail.getByLabel("Agent Timeline")).toContainText("Generate completed");
		await expect(rail.getByText("Test case agent produced baseline suite.")).toBeVisible();
		await expect(rail.getByLabel("Last run")).toContainText("Generate");
		await expect(rail.getByLabel("Project evidence")).toContainText("Pending");
		await expect(rail.getByText(/Test Cases snap-test-v1/i)).toBeVisible();

		await rail.getByRole("button", { name: /^Collapse project information$/i }).click();
		await expect(rail.getByRole("button", { name: /^Expand project information$/i })).toBeVisible();
		await expect(rail.getByLabel("Status overview")).toContainText("Stale");
		await expect(rail.getByLabel("Stage progress")).toHaveCount(0);

		await page.reload();
		await expect(cockpit).toBeVisible({ timeout: 30_000 });
		await expect(rail.getByRole("button", { name: /^Expand project information$/i })).toBeVisible();
		await expect(rail.getByLabel("Status overview")).toContainText("Stale");
		await rail.getByRole("button", { name: /^Expand project information$/i }).click();
		await expect(rail.getByRole("button", { name: /^Collapse project information$/i })).toBeVisible();
		await expect(rail.getByText(/Impact QA · revision 5/)).toBeVisible();
		await expect(cockpit.getByRole("button", { name: /^Analyze Impact$/i })).toBeVisible();

		await cockpit.getByRole("button", { name: /^Analyze Impact$/i }).click();
		await expect(cockpit.getByRole("button", { name: /^Apply Accepted Updates$/i })).toBeVisible();
		await expect(rail.getByText("Impact agent identified 2 changed items.")).toBeVisible();
		await expect(rail.getByText(/Impact Analysis snap-impact-v1/i)).toBeVisible();
	});
});
