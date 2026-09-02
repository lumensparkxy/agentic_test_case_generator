import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { seedAuthenticatedSession } from "./support/auth.js";

const PROJECT_ID = "automation-preview-project";
const TEST_CASE_SNAPSHOT_ID = "snap-test-v1";

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });
}

function testCase(id) {
	return {
		id,
		title: `${id} checkout coverage`,
		description: `Coverage for ${id}`,
		priority: "High",
		type: "Regression",
		status: "Ready",
		preconditions: "A signed-in user exists.",
		steps: [{ step: 1, action: `Exercise ${id}`, expected: "Behavior is correct", test_data: null }],
		expected_result: "The behavior satisfies the requirement.",
		automation_status: "Automated",
		component: "Checkout",
		tags: ["automation"],
		linked_requirement_ids: ["REQ-001"],
		scenario_refs: ["REQ-001-SCN-01"],
	};
}

function executableCandidate(id, sourceTestCaseId) {
	return {
		id,
		source_test_case_id: sourceTestCaseId,
		title: `${sourceTestCaseId} checkout coverage`,
		status: "executable",
		spec: { steps: ["Given I open the checkout page", "Then checkout should be visible"] },
		metadata: {},
		unsupported_steps: [],
		review_reasons: [],
		traceability_ids: ["REQ-001"],
	};
}

function nonExecutableCandidate(id, sourceTestCaseId, status) {
	return {
		id,
		source_test_case_id: sourceTestCaseId,
		title: `${sourceTestCaseId} ${status} coverage`,
		status,
		spec: null,
		metadata: {},
		unsupported_steps:
			status === "manual" ? [] : [{ step: 1, reason_code: `${status}_step`, suggested_next_action: `Review the ${status} candidate.` }],
		review_reasons: [`This candidate requires ${status} review.`],
		traceability_ids: ["REQ-001"],
	};
}

function previewFixture(executable = [], overrides = {}) {
	return {
		executable,
		manual: [],
		unsupported: [],
		invalid: [],
		warnings: [],
		summary: { executable: executable.length, manual: 0, unsupported: 0, invalid: 0 },
		...overrides,
	};
}

function persistedPreviewPayload(executable = []) {
	return {
		target_environment: "staging",
		target_base_url: "https://staging.example.test/app",
		summary: { executable: executable.length, manual: 0, unsupported: 0, invalid: 0 },
		candidate_counts: { executable: executable.length, manual: 0, unsupported: 0, invalid: 0 },
		warnings: [],
		candidates: {
			executable: executable.map(({ id, source_test_case_id, title, status }) => ({ id, source_test_case_id, title, status })),
			manual: [],
			unsupported: [],
			invalid: [],
		},
	};
}

function projectFixture({ testCaseIds = ["TC-001"], executionPayload = null, executionSourceSnapshotId = TEST_CASE_SNAPSHOT_ID } = {}) {
	const cases = testCaseIds.map(testCase);
	const executionSnapshot = executionPayload
		? {
				snapshot_id: "snap-execution-v1",
				project_id: PROJECT_ID,
				stage: "execution",
				version: 1,
				project_revision: 5,
				operation: "automation.execution.preview",
				approved: true,
				source_snapshot_id: executionSourceSnapshotId,
				payload: executionPayload,
				metadata: {},
				created_at: "2026-07-17T08:00:00Z",
			}
		: null;

	return {
		project_id: PROJECT_ID,
		name: "Automation Preview QA",
		description: null,
		status: "active",
		owner_user_id: "playwright-e2e-user",
		current_revision: executionPayload ? 5 : 4,
		created_at: "2026-07-17T08:00:00Z",
		updated_at: "2026-07-17T08:00:00Z",
		stage_state: {
			requirements: { current_snapshot_id: "snap-req-v1", version: 1, approved: true, stale: false, metadata: {} },
			test_cases: { current_snapshot_id: TEST_CASE_SNAPSHOT_ID, version: 1, approved: true, stale: false, metadata: {} },
			...(executionSnapshot
				? { execution: { current_snapshot_id: executionSnapshot.snapshot_id, version: 1, approved: true, stale: false, metadata: {} } }
				: {}),
		},
		current_snapshots: {
			requirements: {
				snapshot_id: "snap-req-v1",
				project_id: PROJECT_ID,
				stage: "requirements",
				version: 1,
				project_revision: 1,
				operation: "requirements.parse",
				approved: true,
				payload: { requirements: [{ id: "REQ-001", text: "Customers can complete checkout.", review_status: "Approved" }] },
				metadata: {},
				created_at: "2026-07-17T08:00:00Z",
			},
			test_cases: {
				snapshot_id: TEST_CASE_SNAPSHOT_ID,
				project_id: PROJECT_ID,
				stage: "test_cases",
				version: 1,
				project_revision: 4,
				operation: "testcases.generate",
				approved: true,
				payload: {
					test_cases: cases,
					review: { approved: true, score: 100, threshold: 85, summary: "Approved.", blocking_issues: [] },
				},
				metadata: { test_case_count: cases.length },
				created_at: "2026-07-17T08:00:00Z",
			},
			...(executionSnapshot ? { execution: executionSnapshot } : {}),
		},
		timeline: [],
		execution_runs: [],
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

function orchestratorStatus(project, nextActions = []) {
	return {
		project_id: PROJECT_ID,
		project_revision: project.current_revision,
		current_stage: "automation",
		stages: {
			requirements: { stage: "requirements", status: "completed", version: 1, approved: true, stale: false, summary: {}, blockers: [] },
			test_cases: { stage: "test_cases", status: "completed", version: 1, approved: true, stale: false, summary: {}, blockers: [] },
			automation: { stage: "automation", status: "ready", version: 0, approved: false, stale: false, summary: {}, blockers: [] },
		},
		next_actions: nextActions,
		blockers: [],
		has_baseline_test_suite: true,
		upstream_changed: false,
		changed_upstream_stages: [],
		generated_at: "2026-07-17T08:00:00Z",
	};
}

function runResponse(preview, selectedIds) {
	return {
		status: "passed",
		run_id: "exec-selection-subset",
		artifacts_root: null,
		playwright_report_paths: [],
		results: selectedIds.map((id) => ({
			id: `result-${id}`,
			source_test_case_id: id,
			title: `${id} execution result`,
			status: "passed",
		})),
		preview,
		warnings: [],
		summary: { passed: selectedIds.length, failed: 0, invalid: 0, skipped: 0, unsupported: 0, manual: 0 },
	};
}

async function installApi(page, scenario) {
	const requests = { previews: 0, runs: 0, runBodies: [] };

	await page.route("**/*", async (route) => {
		const request = route.request();
		if (!["fetch", "xhr"].includes(request.resourceType())) {
			return route.fallback();
		}

		const url = new URL(request.url());
		const { pathname } = url;
		const method = request.method();
		if (pathname === "/auth/me") {
			return jsonResponse(route, {
				sub: "playwright-e2e-user",
				email: "playwright-e2e@example.com",
				name: "Playwright E2E",
				picture: null,
			});
		}
		if (pathname === "/reports/usage/me") return jsonResponse(route, { groups: [] });
		if (pathname === "/entitlements/me") {
			return jsonResponse(route, {
				account: { plan_tier: "premium", support_contact_email: "support@example.test" },
				requirements: { remaining: 500, exhausted: false },
				test_cases: { remaining: 500, exhausted: false },
				wallet: { balance_units: 5000, balance_token_display: "5000" },
				shadow_mode: false,
			});
		}
		if (pathname === "/workspace/summary") {
			return jsonResponse(route, {
				continue_working: null,
				projects: [],
				work_items: [],
				recent_runs: [],
				recent_reports: [],
				generated_at: "2026-07-17T08:00:00Z",
			});
		}
		if (pathname.startsWith("/integrations/")) return jsonResponse(route, { connected: false, connection: null });
		if (pathname === "/projects" && method === "GET") {
			return jsonResponse(route, { projects: [projectSummary(scenario.project)] });
		}
		if (pathname === `/projects/${PROJECT_ID}` && method === "GET") return jsonResponse(route, scenario.project);
		if (pathname === `/projects/${PROJECT_ID}/orchestrator/status`) {
			return jsonResponse(route, orchestratorStatus(scenario.project, scenario.nextActions));
		}
		if (pathname === `/projects/${PROJECT_ID}/orchestrator/runs`) {
			return jsonResponse(route, { runs: [], events: [], checkpoints: [] });
		}
		if (pathname === "/automation/execution/preview" && method === "POST") {
			const previewIndex = requests.previews;
			requests.previews += 1;
			if (scenario.handlePreview) {
				return scenario.handlePreview(route, previewIndex);
			}
			const response = scenario.previews[Math.min(previewIndex, scenario.previews.length - 1)];
			return jsonResponse(route, response);
		}
		if (pathname === "/automation/execution/run" && method === "POST") {
			const body = JSON.parse(request.postData() || "{}");
			const runIndex = requests.runs;
			requests.runs += 1;
			requests.runBodies.push(body);
			if (scenario.handleRun) {
				return scenario.handleRun(route, body, runIndex);
			}
			return jsonResponse(route, runResponse(scenario.runPreview || scenario.previews.at(-1), body.selected_test_case_ids || []));
		}
		return jsonResponse(route, { detail: `Unhandled test route: ${method} ${pathname}` }, 404);
	});

	return requests;
}

async function openAutomation(page) {
	await seedAuthenticatedSession(page);
	await page.goto(buildProjectPath(PROJECT_ID, "automation"));
	await expect(page.getByRole("button", { name: /open account menu/i })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByRole("heading", { name: /^Automation$/i })).toBeVisible();
}

test.describe("Automation preview consistency", () => {
	test("renders a valid mixed preview with counts that match every candidate collection", async ({ page }) => {
		const mixedPreview = previewFixture([executableCandidate("candidate-executable", "TC-001")], {
			manual: [nonExecutableCandidate("candidate-manual", "TC-002", "manual")],
			unsupported: [nonExecutableCandidate("candidate-unsupported", "TC-003", "unsupported")],
			invalid: [nonExecutableCandidate("candidate-invalid", "TC-004", "invalid")],
			summary: { executable: 1, manual: 1, unsupported: 1, invalid: 1 },
		});
		await installApi(page, {
			project: projectFixture({ testCaseIds: ["TC-001", "TC-002", "TC-003", "TC-004"] }),
			previews: [mixedPreview],
		});
		await openAutomation(page);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		for (const bucket of ["Executable", "Manual", "Unsupported", "Invalid"]) {
			await expect(page.getByText(`${bucket} 1`, { exact: true })).toBeVisible();
		}
		await expect(page.getByRole("region", { name: "Executable automation candidates table" }).getByRole("row")).toHaveCount(2);
		await expect(page.getByRole("heading", { name: "Manual", exact: true }).locator("..").getByRole("listitem")).toHaveCount(1);
		await expect(
			page.getByRole("heading", { name: "Unsupported", exact: true }).locator("..").locator(".jira-sync-preview-card")
		).toHaveCount(1);
		await expect(page.getByRole("heading", { name: "Invalid", exact: true }).locator("..").locator(".jira-sync-preview-card")).toHaveCount(
			1
		);
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();
	});

	test("normalizes an inconsistent summary to rendered zero candidates, distinguishes the empty states, and emits no run request", async ({
		page,
	}) => {
		const inconsistentPreview = previewFixture([], {
			summary: { executable: 20, manual: 0, unsupported: 0, invalid: 0 },
		});
		const scenario = { project: projectFixture(), previews: [inconsistentPreview] };
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await expect(page.getByText("No preview yet. Preview execution readiness for the current test cases.", { exact: true })).toBeVisible();
		await page.getByRole("button", { name: /^Preview Execution$/ }).click();

		await expect(page.getByText("Executable 0", { exact: true })).toBeVisible();
		await expect(page.getByText("Preview completed with zero executable candidates.", { exact: true })).toBeVisible();
		await expect(page.getByRole("alert")).toContainText(/preview data was inconsistent/i);
		await expect(page.getByText(/20 executable/i)).toHaveCount(0);
		const runButton = page.getByRole("button", { name: /^Run 0 Candidates$/ });
		await expect(runButton).toBeDisabled();
		await runButton.evaluate((button) => button.click());
		expect(requests.runs).toBe(0);
	});

	test("runs only the selected executable subset and sends exact candidate IDs", async ({ page }) => {
		const candidates = [executableCandidate("candidate-checkout", "TC-001"), executableCandidate("candidate-refund", "TC-002")];
		const livePreview = previewFixture(candidates);
		const scenario = {
			project: projectFixture({ testCaseIds: ["TC-001", "TC-002"] }),
			previews: [livePreview],
			runPreview: livePreview,
		};
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByRole("button", { name: /^Run 2 Candidates$/ })).toBeEnabled();
		await expect(page.getByRole("checkbox", { name: "Select TC-001 for execution" })).toBeChecked();
		const refundCandidate = page.getByRole("checkbox", { name: "Select TC-002 for execution" });
		await expect(refundCandidate).toBeChecked();
		await refundCandidate.uncheck();
		await expect(page.getByRole("checkbox", { name: "Select all executable candidates" })).toHaveAttribute("aria-checked", "mixed");

		const runButton = page.getByRole("button", { name: /^Run 1 Candidate$/ });
		await expect(runButton).toBeEnabled();
		await runButton.click();
		await expect.poll(() => requests.runs).toBe(1);
		expect(requests.runBodies[0].selected_test_case_ids).toEqual(["candidate-checkout"]);
		await expect(page.locator("#main-content").getByText(/Execution passed: 1 passed, 0 failed/i)).toBeVisible();
		await expect(refundCandidate).not.toBeChecked();
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();
	});

	test("keeps repeated source IDs actionable when execution candidate IDs are unique", async ({ page }) => {
		const candidates = [executableCandidate("candidate-checkout-1", "TC-001"), executableCandidate("candidate-checkout-2", "TC-001")];
		const livePreview = previewFixture(candidates);
		const scenario = { project: projectFixture(), previews: [livePreview], runPreview: livePreview };
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByRole("alert")).toHaveCount(0);
		const duplicateSourceCandidates = page.getByRole("checkbox", { name: /^Select TC-001 candidate candidate-checkout-/ });
		await expect(duplicateSourceCandidates).toHaveCount(2);
		await expect(page.getByRole("button", { name: /^Run 2 Candidates$/ })).toBeEnabled();
		await duplicateSourceCandidates.nth(1).uncheck();
		await page.getByRole("button", { name: /^Run 1 Candidate$/ }).click();

		await expect.poll(() => requests.runs).toBe(1);
		expect(requests.runBodies[0].selected_test_case_ids).toEqual(["candidate-checkout-1"]);
	});

	test("contextual Execute creates a reviewable preview without implicitly running it", async ({ page }) => {
		const scenario = {
			project: projectFixture(),
			previews: [previewFixture([executableCandidate("candidate-checkout", "TC-001")])],
			nextActions: [
				{
					action: "execute",
					label: "Run approved browser cases",
					stage: "automation",
					enabled: true,
					primary: true,
					secondary: false,
					reason: "Approved automation candidates are ready.",
					blockers: [],
				},
			],
		};
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await page.getByRole("button", { name: /^Run approved cases$/ }).click();
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();
		expect(requests.previews).toBe(1);
		expect(requests.runs).toBe(0);
	});

	test("does not restore an obsolete run preview after execution state is reset", async ({ page }) => {
		const livePreview = previewFixture([executableCandidate("candidate-checkout", "TC-001")]);
		const scenario = {
			project: projectFixture(),
			previews: [livePreview],
			handleRun: async (route, body) => {
				await new Promise((resolve) => setTimeout(resolve, 500));
				return jsonResponse(route, runResponse(livePreview, body.selected_test_case_ids || []));
			},
		};
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await page.getByRole("button", { name: /^Run 1 Candidate$/ }).click({ noWaitAfter: true });
		await expect.poll(() => requests.runs).toBe(1);
		const targetEnvironment = page.getByPlaceholder("staging, dev, customer-a");
		await targetEnvironment.evaluate((input) => {
			input.disabled = false;
			const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
			valueSetter.call(input, "replacement-target");
			input.dispatchEvent(new Event("input", { bubbles: true }));
		});

		await expect(page.getByText("No preview yet. Preview execution readiness for the current test cases.", { exact: true })).toBeVisible();
		await new Promise((resolve) => setTimeout(resolve, 700));
		await expect(page.getByRole("heading", { name: "Execution Results" })).toHaveCount(0);
		await expect(page.getByText("No preview yet. Preview execution readiness for the current test cases.", { exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: /^Run 0 Candidates$/ })).toBeDisabled();
	});

	test("rejects a malformed run response without replacing the reviewed preview", async ({ page }) => {
		const livePreview = previewFixture([executableCandidate("candidate-checkout", "TC-001")]);
		const scenario = {
			project: projectFixture(),
			previews: [livePreview],
			handleRun: async (route) => jsonResponse(route, null),
		};
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await page.getByRole("button", { name: /^Run 1 Candidate$/ }).click();
		await expect.poll(() => requests.runs).toBe(1);
		await expect(page.getByRole("heading", { name: "Execution Results" })).toHaveCount(0);
		await expect(page.getByRole("alert")).toContainText(/Execution run failed: Execution response did not include valid run data/i);
		await expect(page.getByTestId("application-live-status")).toBeEmpty();
		await expect(page.getByText("Executable 1", { exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();
	});

	test("ignores an older same-project preview that resolves after the latest request", async ({ page }) => {
		const olderPreview = previewFixture([executableCandidate("candidate-old", "TC-001")]);
		const latestPreview = previewFixture([
			executableCandidate("candidate-new-1", "TC-001"),
			executableCandidate("candidate-new-2", "TC-002"),
		]);
		const scenario = {
			project: projectFixture({ testCaseIds: ["TC-001", "TC-002"] }),
			previews: [olderPreview, latestPreview],
			handlePreview: async (route, previewIndex) => {
				await new Promise((resolve) => setTimeout(resolve, previewIndex === 0 ? 2000 : 100));
				return jsonResponse(route, previewIndex === 0 ? olderPreview : latestPreview);
			},
		};
		const requests = await installApi(page, scenario);
		await openAutomation(page);
		await expect(page.getByText("No preview yet. Preview execution readiness for the current test cases.", { exact: true })).toBeVisible();
		await page.waitForLoadState("networkidle");

		const previewButton = page.getByRole("button", { name: /^Preview Execution$/ });
		await previewButton.click({ noWaitAfter: true });
		await previewButton.evaluate((button) => {
			button.disabled = false;
			button.click();
		});

		await expect.poll(() => requests.previews).toBe(2);
		await expect(page.getByText("Executable 2", { exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: /^Run 2 Candidates$/ })).toBeEnabled();
		await expect(page.getByRole("checkbox", { name: "Select TC-002 for execution" })).toBeChecked();
		await expect(previewButton).toBeEnabled();
		await new Promise((resolve) => setTimeout(resolve, 2100));
		await expect(page.getByText("Executable 2", { exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: /^Preview Execution$/ })).toBeEnabled();
	});

	test("treats a successful null preview payload as a failed no-preview response", async ({ page }) => {
		const scenario = { project: projectFixture(), previews: [null] };
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByText("No preview yet. Preview execution readiness for the current test cases.", { exact: true })).toBeVisible();
		await expect(page.getByText("Preview completed with zero executable candidates.", { exact: true })).toHaveCount(0);
		await expect(page.getByRole("alert")).toContainText(/Execution preview failed: Preview response did not include candidate data/i);
		await expect(page.getByTestId("application-live-status")).toBeEmpty();
		await expect(page.getByRole("button", { name: /^Run 0 Candidates$/ })).toBeDisabled();
		expect(requests.previews).toBe(1);
		expect(requests.runs).toBe(0);
	});

	test("marks the Automation panel busy and blocks controls while a preview is pending", async ({ page }) => {
		let releasePreview;
		const previewGate = new Promise((resolve) => {
			releasePreview = resolve;
		});
		const livePreview = previewFixture([executableCandidate("candidate-checkout", "TC-001")]);
		const scenario = {
			project: projectFixture(),
			previews: [livePreview],
			handlePreview: async (route) => {
				await previewGate;
				return jsonResponse(route, livePreview);
			},
		};
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		const panel = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: /^Automation$/i }) });
		const previewButton = page.getByRole("button", { name: /^Preview Execution$/ });
		await previewButton.click({ noWaitAfter: true });
		await expect.poll(() => requests.previews).toBe(1);
		await expect(panel).toHaveAttribute("aria-busy", "true");
		await expect(page.getByLabel("Target environment")).toBeDisabled();
		await expect(page.getByLabel("Target base URL")).toBeDisabled();
		await expect(page.getByRole("button", { name: /^Previewing/ })).toBeDisabled();
		await expect(page.getByRole("button", { name: /^Run 0 Candidates$/ })).toBeDisabled();

		releasePreview();
		await expect(page.getByText("Executable 1", { exact: true })).toBeVisible();
		await expect(panel).not.toHaveAttribute("aria-busy", "true");
	});

	test("editing the target clears the prior preview and selection before a zero-candidate refresh", async ({ page }) => {
		const livePreview = previewFixture([executableCandidate("candidate-checkout", "TC-001")]);
		const zeroPreview = previewFixture([]);
		const scenario = { project: projectFixture(), previews: [livePreview, zeroPreview] };
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();

		await page.getByPlaceholder("staging, dev, customer-a").fill("production-like");
		await expect(page.getByText("No preview yet. Preview execution readiness for the current test cases.", { exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: /^Run 0 Candidates$/ })).toBeDisabled();
		expect(requests.runs).toBe(0);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByText("Preview completed with zero executable candidates.", { exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: /^Run 0 Candidates$/ })).toBeDisabled();
		expect(requests.previews).toBe(2);
	});

	test("renders a persisted compact preview but requires a fresh preview before execution", async ({ page }) => {
		const candidate = executableCandidate("candidate-checkout", "TC-001");
		const livePreview = previewFixture([candidate]);
		const scenario = {
			project: projectFixture({
				executionPayload: persistedPreviewPayload([candidate]),
				executionSourceSnapshotId: "snap-test-older",
			}),
			previews: [livePreview],
		};
		const requests = await installApi(page, scenario);
		await openAutomation(page);

		await expect(page.getByText("Executable 1", { exact: true })).toBeVisible();
		await expect(page.getByText(/stored preview belongs to an older test-case snapshot/i)).toBeVisible();
		await expect(page.getByRole("checkbox", { name: "Select TC-001 for execution" })).toBeDisabled();
		await expect(page.getByRole("button", { name: /^Run 0 Candidates$/ })).toBeDisabled();
		expect(requests.runs).toBe(0);

		await page.getByRole("button", { name: /^Preview Execution$/ }).click();
		await expect(page.getByRole("alert")).toHaveCount(0);
		await expect(page.getByRole("checkbox", { name: "Select TC-001 for execution" })).toBeChecked();
		await expect(page.getByRole("button", { name: /^Run 1 Candidate$/ })).toBeEnabled();
		expect(requests.previews).toBe(1);
	});
});
