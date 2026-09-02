import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./support/auth.js";
import { openQaProjectByName } from "./support/projects.js";

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

function requirement(index, text, reviewStatus = "Approved") {
	return {
		id: `REQ-${String(index).padStart(3, "0")}`,
		text,
		review_status: reviewStatus,
	};
}

function buildRequirements({ changed = false } = {}) {
	return Array.from({ length: 10 }, (_, offset) => {
		const index = offset + 1;
		const id = `REQ-${String(index).padStart(3, "0")}`;
		const baseText = `${id} baseline checkout behavior`;
		if (changed && (id === "REQ-003" || id === "REQ-010")) {
			return requirement(index, `${id} changed approval and retry behavior`);
		}
		return requirement(index, baseText);
	});
}

function buildCoveragePlan(requirements) {
	return requirements.map((item) => ({
		requirement_id: item.id,
		requirement_text: item.text,
		scenarios: [
			{
				id: `${item.id}-SCN-01`,
				requirement_id: item.id,
				scenario_type: "Happy Path",
				title: `${item.id} primary behavior`,
				objective: `Validate ${item.text}`,
				priority: "High",
				must_have: true,
			},
		],
	}));
}

function testCase(requirementId, title, tags = []) {
	return {
		id: `TC-${requirementId.slice(-3)}`,
		title,
		description: `Baseline coverage for ${requirementId}`,
		priority: "High",
		type: "Regression",
		status: "Ready",
		preconditions: "A user is signed in.",
		steps: [{ step: 1, action: `Exercise ${requirementId}`, expected: "Behavior is correct", test_data: null }],
		expected_result: "The behavior satisfies the requirement.",
		test_data: null,
		estimated_time: "5 mins",
		automation_status: "Manual",
		component: "Checkout",
		tags,
		linked_requirement_ids: [requirementId],
		scenario_refs: [`${requirementId}-SCN-01`],
		artifact_set_id: "tc-set-1",
		artifact_item_id: `tc-item-${requirementId.slice(-3)}`,
		artifact_version_id: `tc-version-${requirementId.slice(-3)}`,
		artifact_version_number: tags.includes("impact:update") ? 2 : 1,
	};
}

function projectDetail({ withAnalysis = false, afterApply = false } = {}) {
	const baselineRequirements = buildRequirements();
	const currentRequirements = buildRequirements({ changed: true });
	const coveragePlan = buildCoveragePlan(baselineRequirements);
	const testCases = [
		testCase("REQ-001", "REQ-001 preserved coverage"),
		testCase("REQ-003", "REQ-003 approval retry coverage", afterApply ? ["impact:update"] : []),
		testCase("REQ-010", "REQ-010 approval retry coverage", afterApply ? ["impact:update"] : []),
	];
	const impactAnalysis = {
		baseline_snapshot_ids: { requirements: "snap-req-v1", context: null, use_cases: "snap-use-v1", test_cases: "snap-test-v1" },
		current_snapshot_ids: { requirements: "snap-req-v2", context: null, use_cases: "snap-use-v1", test_cases: "snap-test-v1" },
		changed_items: [
			{
				item_id: "REQ-003",
				kind: "requirement",
				change_type: "modified",
				title: "REQ-003 modified",
				current_text: "REQ-003 changed approval and retry behavior",
				previous_text: "REQ-003 baseline checkout behavior",
				approved: true,
				requirement_id: "REQ-003",
				scenario_ids: [],
			},
			{
				item_id: "REQ-010",
				kind: "requirement",
				change_type: "modified",
				title: "REQ-010 modified",
				current_text: "REQ-010 changed approval and retry behavior",
				previous_text: "REQ-010 baseline checkout behavior",
				approved: true,
				requirement_id: "REQ-010",
				scenario_ids: [],
			},
		],
		impacted_test_cases: [
			{
				test_case_id: "TC-003",
				title: "REQ-003 approval retry coverage",
				impact_source: "direct",
				linked_requirement_ids: ["REQ-003"],
				scenario_refs: ["REQ-003-SCN-01"],
				reason: "Direct traceability match via linked requirements: REQ-003",
			},
			{
				test_case_id: "TC-010",
				title: "REQ-010 approval retry coverage",
				impact_source: "direct",
				linked_requirement_ids: ["REQ-010"],
				scenario_refs: ["REQ-010-SCN-01"],
				reason: "Direct traceability match via linked requirements: REQ-010",
			},
		],
		recommendations: [
			{
				recommendation_id: "impact-keep-TC-001",
				action: "keep",
				title: "Keep TC-001",
				reason: "No direct or semantic impact detected.",
				confidence: 0.91,
				accepted: true,
				impact_source: "direct",
				test_case_id: "TC-001",
				scenario_refs: [],
			},
			{
				recommendation_id: "impact-update-TC-003",
				action: "update",
				title: "Update TC-003",
				reason: "Direct traceability match via linked requirements: REQ-003",
				confidence: 0.82,
				accepted: true,
				impact_source: "direct",
				test_case_id: "TC-003",
				scenario_refs: ["REQ-003-SCN-01"],
			},
			{
				recommendation_id: "impact-update-TC-010",
				action: "update",
				title: "Update TC-010",
				reason: "Direct traceability match via linked requirements: REQ-010",
				confidence: 0.82,
				accepted: true,
				impact_source: "direct",
				test_case_id: "TC-010",
				scenario_refs: ["REQ-010-SCN-01"],
			},
		],
		summary: {
			changed_item_count: 2,
			added_count: 0,
			modified_count: 2,
			removed_count: 0,
			unchanged_requirement_count: 8,
			directly_impacted_test_case_count: 2,
			semantic_neighbor_count: 0,
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
			payload: { requirements: currentRequirements },
			metadata: {},
			created_at: "2026-06-12T00:00:00Z",
		},
		use_cases: {
			snapshot_id: "snap-use-v1",
			project_id: "project-1",
			stage: "use_cases",
			version: 1,
			project_revision: 3,
			operation: "testcases.generate.use_cases",
			approved: true,
			payload: { coverage_plan: coveragePlan, requirement_analysis: [] },
			metadata: {},
			created_at: "2026-06-12T00:00:00Z",
		},
		test_cases: {
			snapshot_id: afterApply ? "snap-test-v2" : "snap-test-v1",
			project_id: "project-1",
			stage: "test_cases",
			version: afterApply ? 2 : 1,
			project_revision: afterApply ? 7 : 4,
			operation: afterApply ? "impact.update.apply" : "testcases.generate",
			approved: true,
			payload: {
				test_cases: testCases,
				coverage_plan: coveragePlan,
				requirement_analysis: [],
				impact_analysis: afterApply ? impactAnalysis : undefined,
				impact_update_result: afterApply
					? {
							applied_recommendation_ids: ["impact-keep-TC-001", "impact-update-TC-003", "impact-update-TC-010"],
							preserved_count: 1,
							updated_count: 2,
							added_count: 0,
							deprecated_count: 0,
						}
					: undefined,
				review: {
					approved: true,
					score: 100,
					threshold: 0,
					summary: "Approved.",
					blocking_issues: [],
					suggestions: [],
					unmet_criteria: [],
				},
			},
			metadata: { source_requirements_snapshot_id: "snap-req-v1", source_use_case_snapshot_id: "snap-use-v1" },
			created_at: "2026-06-12T00:00:00Z",
		},
	};
	if (withAnalysis || afterApply) {
		currentSnapshots.impact_analysis = {
			snapshot_id: "snap-impact-v1",
			project_id: "project-1",
			stage: "impact_analysis",
			version: 1,
			project_revision: 6,
			operation: "impact.analysis",
			approved: false,
			payload: impactAnalysis,
			metadata: { changed_item_count: 2, recommendation_counts: impactAnalysis.summary.recommendation_counts },
			created_at: "2026-06-12T00:00:00Z",
		};
	}
	return {
		project_id: "project-1",
		name: "Impact QA",
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: afterApply ? 7 : withAnalysis ? 6 : 5,
		created_at: "2026-06-12T00:00:00Z",
		updated_at: "2026-06-12T00:00:00Z",
		stage_state: {
			requirements: { current_snapshot_id: "snap-req-v2", version: 2, approved: true, stale: false, metadata: {} },
			use_cases: {
				current_snapshot_id: "snap-use-v1",
				version: 1,
				approved: true,
				stale: true,
				stale_reason: "requirements changed",
				metadata: {},
			},
			impact_analysis:
				withAnalysis || afterApply
					? { current_snapshot_id: "snap-impact-v1", version: 1, approved: false, stale: false, metadata: { changed_item_count: 2 } }
					: undefined,
			test_cases: {
				current_snapshot_id: afterApply ? "snap-test-v2" : "snap-test-v1",
				version: afterApply ? 2 : 1,
				approved: true,
				stale: !afterApply,
				stale_reason: afterApply ? null : "requirements changed",
				metadata: {},
			},
		},
		current_snapshots: currentSnapshots,
		timeline: [],
		execution_runs: [],
	};
}

function orchestratorStatus(phase = "stale") {
	const withAnalysis = phase === "analysis" || phase === "applied";
	const afterApply = phase === "applied";
	return {
		project_id: "project-1",
		project_revision: afterApply ? 7 : withAnalysis ? 6 : 5,
		current_stage: afterApply ? "automation" : withAnalysis ? "test_cases" : "impact_analysis",
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
			use_cases: {
				stage: "use_cases",
				status: "stale",
				current_snapshot_id: "snap-use-v1",
				version: 1,
				approved: true,
				stale: true,
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
				summary: withAnalysis ? { changed_item_count: 2 } : {},
				blockers: [],
			},
			test_cases: {
				stage: "test_cases",
				status: afterApply ? "completed" : withAnalysis ? "ready" : "stale",
				current_snapshot_id: afterApply ? "snap-test-v2" : "snap-test-v1",
				version: afterApply ? 2 : 1,
				approved: true,
				stale: !afterApply && !withAnalysis,
				summary: {},
				blockers: [],
			},
		},
		next_actions: afterApply
			? [
					{
						action: "automate",
						label: "Preview Automation",
						stage: "automation",
						enabled: true,
						primary: true,
						secondary: false,
						reason: "Updated coverage is ready for automation preview.",
						blockers: [],
					},
				]
			: withAnalysis
				? [
						{
							action: "apply_update",
							label: "Apply Accepted Updates",
							stage: "test_cases",
							enabled: true,
							primary: true,
							secondary: false,
							reason: "Accepted impact recommendations are ready to apply.",
							blockers: [],
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
						},
						{
							action: "full_regenerate",
							label: "Full Regenerate",
							stage: "test_cases",
							enabled: true,
							primary: false,
							secondary: true,
							reason: "Rebuild the complete suite as an explicit fallback.",
							blockers: [],
						},
					],
		blockers: [],
		has_baseline_test_suite: true,
		upstream_changed: !afterApply,
		changed_upstream_stages: afterApply ? [] : ["requirements"],
		generated_at: "2026-06-12T00:00:00Z",
	};
}

test.describe("Impact update flow", () => {
	test("stale existing suite uses impact analysis as the primary path", async ({ page }) => {
		const projectBefore = projectDetail();
		const projectWithAnalysis = projectDetail({ withAnalysis: true });
		const projectAfterApply = projectDetail({ afterApply: true });
		let orchestratorPhase = "stale";

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
		await page.route("**/projects/project-1/orchestrator/status", async (route) =>
			jsonResponse(route, orchestratorStatus(orchestratorPhase))
		);
		await page.route("**/projects/project-1/orchestrator/runs", async (route) =>
			jsonResponse(route, { runs: [], events: [], checkpoints: [] })
		);
		await page.route("**/projects/project-1/impact-analysis", async (route) => {
			orchestratorPhase = "analysis";
			return jsonResponse(route, projectWithAnalysis);
		});
		await page.route("**/projects/project-1/impact-update/apply", async (route) => {
			orchestratorPhase = "applied";
			return jsonResponse(route, projectAfterApply);
		});
		await page.route("**/projects/project-1", async (route) => jsonResponse(route, projectBefore));
		await page.route("**/projects**", async (route) => {
			const url = new URL(route.request().url());
			if (url.pathname !== "/projects") {
				return route.fallback();
			}
			return jsonResponse(route, {
				projects: [
					{
						project_id: "project-1",
						name: "Impact QA",
						description: null,
						status: "active",
						owner_user_id: "playwright-e2e-user",
						current_revision: 5,
						created_at: "2026-06-12T00:00:00Z",
						updated_at: "2026-06-12T00:00:00Z",
						stage_state: {},
					},
				],
			});
		});
		await page.route("**/automation/execution/preview", async (route) =>
			jsonResponse(route, {
				executable: [],
				manual: [],
				unsupported: [],
				invalid: [],
				warnings: [],
				summary: { executable: 0, manual: 3, unsupported: 0, invalid: 0 },
			})
		);

		await seedAuthenticatedSession(page);
		await page.goto("/");
		await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });
		await openQaProjectByName(page, "Impact QA");
		await expect(page.getByRole("button", { name: "Open QA project menu" })).toContainText("Impact QA");
		await expect(page.getByRole("button", { name: "Open QA project menu" })).toContainText("revision 5");
		await page
			.getByRole("navigation", { name: "Project navigation" })
			.getByRole("link", { name: /^Test Cases,/i })
			.click();

		await page.reload();
		await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });
		await expect(page.getByRole("button", { name: "Open QA project menu" })).toContainText("Impact QA");
		await expect(page.getByRole("button", { name: "Open QA project menu" })).toContainText("revision 5");

		const task = page.getByLabel("Contextual task");
		await expect(task.getByRole("heading", { name: /^Analyze Impact$/i })).toBeVisible();
		await expect(task.getByRole("button", { name: /^Start analysis$/i })).toBeVisible();
		await expect(page.getByRole("button", { name: /Full Regenerate from 10 Approved/i })).toHaveCount(0);

		await task.getByRole("button", { name: /^Start analysis$/i }).click();
		await expect(page.getByRole("heading", { name: /^Impact Analysis$/i })).toBeVisible();
		await expect(page.getByText("REQ-003 modified")).toBeVisible();
		await expect(page.getByText("Update TC-010")).toBeVisible();
		await expect(page.getByRole("button", { name: /Apply 3 Accepted Recommendations/i })).toBeEnabled();

		await page.getByRole("button", { name: /Apply 3 Accepted Recommendations/i }).click();
		await expect(page.getByText(/Impact update applied: 1 preserved, 2 updated, 0 added, 0 deprecated/i)).toBeVisible();
		await expect(page.getByText(/3 test cases/i)).toBeVisible();
		await expect(task.getByRole("button", { name: /^Start analysis$/i })).toHaveCount(0);
	});
});
