import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { USE_CASE_PROJECT_ID, installUseCaseReviewApi, useCaseProjectFixture } from "./support/use-case-review.js";

const enabled = process.env.E2E_PROJECT_WORKSPACES_V2 === "true" && process.env.E2E_APPROVAL_CONTINUATION_ENABLED === "true";
const root = `/projects/${USE_CASE_PROJECT_ID}`;
const statusPath = `${root}/impact-update/status`;
const applyPath = `${root}/impact-update/apply`;
const analysisId = "analysis-booking-8-to-6";
const oldSuiteId = "booking-tests-capacity-8";
const newSuiteId = "booking-tests-capacity-6";
const directIds = Array.from({ length: 6 }, (_, index) => `direct-update-${index + 1}`);
const changedIds = directIds.map((_, index) => `TC-${String(index + 1).padStart(3, "0")}`);
const exactExpected = "Booking is rejected and no booking for seven attendees exists.";
const literal = "  QA–07  ";
const operationId = `draft_${"d".repeat(64)}`;

function fixture() {
	const project = useCaseProjectFixture({ reviewState: "approved", name: "Booking 8 to 6" });
	const contextId = "booking-context-current";
	const plan = project.current_snapshots.use_cases;
	const requirements = project.current_snapshots.requirements;
	requirements.operation = "requirements.review";
	requirements.payload.requirements[0].text = "The current Booking form accepts at most six attendees.";
	plan.operation = "use_cases.generate";
	plan.source_snapshot_id = requirements.snapshot_id;
	plan.metadata = {
		source_requirements_snapshot_id: requirements.snapshot_id,
		source_context_snapshot_id: contextId,
		scenario_refresh_predecessor: { source_test_cases_snapshot_id: oldSuiteId },
		use_case_refresh: {
			previous_snapshot_id: "old-plan-capacity-8",
			refreshed_requirement_ids: ["REQ-101"],
			retained_requirement_ids: ["REQ-202"],
			removed_requirement_ids: [],
		},
	};
	project.current_snapshots.context = {
		snapshot_id: contextId,
		project_id: project.project_id,
		stage: "context",
		approved: true,
		version: 2,
		operation: "context.enrich",
		payload: { notes: "Booking fixture uses a current capacity of six attendees." },
	};
	project.stage_state.context = { current_snapshot_id: contextId, stale: false, approved: true, version: 2 };
	const cases = Array.from({ length: 14 }, (_, index) => {
		const id = `TC-${String(index + 1).padStart(3, "0")}`;
		return {
			id,
			title: index === 0 ? "Booking accepts seven before the source update" : `Booking regression ${index + 1}`,
			status: "Approved",
			linked_requirement_ids: [index < 6 ? "REQ-101" : "REQ-202"],
			scenario_refs: [index < 6 ? "REQ-101-SCN-01" : "REQ-202-SCN-01"],
			artifact_set_id: "booking-saved-cases",
			artifact_item_id: `item-${id}`,
			artifact_version_id: `version-1-${id}`,
			artifact_version_number: 1,
			steps: [
				index === 0
					? { step: 1, action: "Book for seven attendees", expected: "The booking is confirmed.", test_data: literal }
					: { step: 1, action: `Run booking action ${index + 1}`, expected: `Booking result ${index + 1} is visible.` },
			],
		};
	});
	const original = {
		snapshot_id: oldSuiteId,
		project_id: project.project_id,
		project_revision: project.current_revision - 1,
		stage: "test_cases",
		version: 1,
		operation: "testcases.generate",
		source_snapshot_id: "old-plan-capacity-8",
		approved: true,
		metadata: { source_use_case_snapshot_id: "old-plan-capacity-8" },
		payload: {
			test_cases: cases,
			approved: true,
			review: { approved: true, score: 100, threshold: 90 },
			coverage_plan: plan.payload.coverage_plan,
			generation_tasks: [],
		},
	};
	project.current_snapshots.test_cases = original;
	project.stage_state.test_cases = {
		current_snapshot_id: oldSuiteId,
		stale: true,
		approved: true,
		version: 1,
		metadata: original.metadata,
	};

	const recommendation = (index, kind) => ({
		recommendation_id: kind === "direct" ? directIds[index] : `${kind}-${index + 1}`,
		action: kind === "keep" ? "keep" : "update",
		title:
			kind === "direct" ? `Update ${cases[index].id}` : `${kind === "semantic_neighbor" ? "Optional update" : "Keep"} ${cases[index].id}`,
		reason: kind === "direct" ? "The case directly covers the changed capacity." : "This case has no direct changed-capacity evidence.",
		impact_source: kind === "keep" ? "direct" : kind,
		confidence: kind === "semantic_neighbor" ? 0.42 : 0.92,
		accepted: kind !== "semantic_neighbor",
		test_case_id: cases[index].id,
		requirement_id: cases[index].linked_requirement_ids[0],
		scenario_refs: cases[index].scenario_refs,
	});
	const analysis = {
		snapshot_id: analysisId,
		project_id: project.project_id,
		project_revision: project.current_revision,
		stage: "impact_analysis",
		operation: "impact.analysis",
		source_snapshot_id: oldSuiteId,
		approved: false,
		metadata: {},
		payload: {
			current_snapshot_ids: {
				requirements: requirements.snapshot_id,
				context: contextId,
				use_cases: plan.snapshot_id,
				test_cases: oldSuiteId,
			},
			changed_items: [
				{
					item_id: "REQ-101",
					requirement_id: "REQ-101",
					kind: "requirement",
					change_type: "modified",
					title: "Booking capacity updated",
					previous_text: "Maximum capacity 8",
					current_text: "Maximum capacity 6",
					approved: true,
				},
			],
			impacted_test_cases: cases.slice(0, 12).map((row, index) => ({
				test_case_id: row.id,
				title: row.title,
				impact_source: index < 6 ? "direct" : "semantic_neighbor",
				linked_requirement_ids: row.linked_requirement_ids,
				scenario_refs: row.scenario_refs,
				reason: index < 6 ? "Direct capacity coverage" : "Optional related behavior",
			})),
			recommendations: cases.map((_, index) => recommendation(index, index < 6 ? "direct" : index < 12 ? "semantic_neighbor" : "keep")),
			summary: {
				changed_item_count: 1,
				directly_impacted_test_case_count: 6,
				semantic_neighbor_count: 6,
				recommendation_counts: { update: 12, keep: 2 },
			},
		},
	};
	project.current_snapshots.impact_analysis = analysis;
	project.stage_state.impact_analysis = { current_snapshot_id: analysisId, stale: false, approved: false };
	return { project, cases, original, analysis, plan };
}

async function setup(page, { interrupted = false } = {}) {
	const { project, cases, original, analysis, plan } = fixture();
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	const writes = [];
	const errors = [];
	const applies = [];
	const statuses = [];
	let result = null;
	let attempt = interrupted
		? {
				operation_id: operationId,
				fence: 7,
				request_id: "source-application-interrupted",
				state: "interrupted",
				reason: "provider_outcome_unconfirmed",
				analysis_snapshot_id: analysisId,
				source_snapshot_id: oldSuiteId,
				accepted_recommendation_ids: directIds,
			}
		: null;
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	await page.route(new RegExp(`${root}/approval-continuations(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, tests_enabled: true, saved_review_enabled: true, runs: [] } })
	);
	await page.route(new RegExp(`${root}/test-cases/repair(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, blocked: false, targets: [], attempt: null, result: null } })
	);
	await page.route(new RegExp(`${statusPath}(?:\\?.*)?$`), (route) => {
		expect(route.request().method()).toBe("GET");
		const requestId = new URL(route.request().url()).searchParams.get("request_id");
		statuses.push(requestId);
		return route.fulfill({
			json: {
				enabled: true,
				eligible: !result,
				blocked: Boolean(attempt),
				selectable_recommendation_ids: result ? [] : directIds,
				attempt,
				result: result?.request_id === requestId ? result : null,
			},
		});
	});
	const releasePath = `${root}/impact-update/${operationId}/release`;
	await page.route(`**${releasePath}`, (route) => {
		expect(route.request().method()).toBe("POST");
		expect(route.request().postDataJSON()).toEqual({ fence: attempt.fence });
		attempt = null;
		return route.fulfill({ json: { released: true } });
	});
	await page.route(`**${applyPath}`, (route) => {
		const request = route.request();
		const requestId = request.headers()["x-request-id"];
		expect(request.method()).toBe("POST");
		expect(requestId).toBeTruthy();
		const body = request.postDataJSON();
		expect(body).toEqual({
			analysis_snapshot_id: analysisId,
			base_project_revision: project.current_revision,
			accepted_recommendation_ids: directIds,
		});
		applies.push({ body, requestId });
		const current = api.getProject();
		current.current_revision++;
		result = {
			request_id: requestId,
			analysis_snapshot_id: analysisId,
			source_snapshot_id: oldSuiteId,
			result_snapshot_id: newSuiteId,
			project_revision: current.current_revision,
			accepted_recommendation_ids: directIds,
			changed_test_case_ids: changedIds,
			preserved_count: 8,
			updated_count: 6,
		};
		current.impact_application = {
			...result,
			status: "applied",
			result_project_revision: current.current_revision,
			added_count: 0,
			deprecated_count: 0,
		};
		const updated = cases.map((row, index) =>
			index >= 6
				? row
				: {
						...row,
						title: index === 0 ? "Reject booking for seven after the capacity update" : `Revised booking regression ${index + 1}`,
						status: "In Review",
						artifact_version_id: `version-2-${row.id}`,
						artifact_version_number: 2,
						steps: index === 0 ? [{ ...row.steps[0], expected: exactExpected }] : row.steps,
					}
		);
		current.current_snapshots.test_cases = {
			...original,
			snapshot_id: newSuiteId,
			project_revision: current.current_revision,
			source_snapshot_id: analysis.snapshot_id,
			version: 2,
			operation: "impact.update.apply",
			approved: false,
			metadata: { source_use_case_snapshot_id: plan.snapshot_id, source_snapshot_ids: analysis.payload.current_snapshot_ids },
			payload: {
				...original.payload,
				test_cases: updated,
				approved: false,
				review: { approved: false, score: 0, threshold: 90, summary: "Review the combined suite." },
				substantive_assessment: { status: "unknown", reason: "Review the combined suite." },
				impact_update_result: current.impact_application,
			},
		};
		current.stage_state.test_cases = {
			current_snapshot_id: newSuiteId,
			stale: false,
			approved: false,
			operation: "impact.update.apply",
			version: 2,
		};
		current.suite_quality = {
			suite_snapshot_id: newSuiteId,
			source_freshness: "unknown",
			design_status: "unknown",
			scenario_decision: "approved",
			human_acceptance: "unavailable",
			declared_setup: "not_assessed",
		};
		api.setProject(current);
		return route.abort("failed");
	});
	await seedAuthenticatedSession(page);
	return { api, cases, writes, errors, applies, statuses, releasePath };
}

test.describe("Bounded scoped impact application", () => {
	test.skip(!enabled, "Requires the opt-in workspace and continuation");

	test("applies six explicit direct updates and recovers an exact saved result", async ({ page }, testInfo) => {
		const fixture = await setup(page);
		await page.setViewportSize({ width: 1440, height: 1000 });
		await page.goto(`${root}/review/tests`);
		await expect(page.getByRole("heading", { name: "Impact Analysis", exact: true })).toBeVisible();
		const neighbor = page.getByRole("checkbox", { name: "Accept Optional update TC-007", exact: true });
		await expect(neighbor).toBeDisabled();
		await expect(neighbor).not.toBeChecked();
		for (const [index, id] of changedIds.entries()) {
			const choice = page.getByRole("checkbox", { name: `Accept Update ${id}`, exact: true });
			await expect(choice).not.toBeChecked();
			await choice.check();
			if (!index) await expect(page.getByRole("button", { name: /^Apply 1 recommendation/ })).toBeDisabled();
		}
		expect(fixture.writes).toEqual([]);
		await page.getByRole("button", { name: "Apply 6 recommendations", exact: true }).click();

		const application = page.getByRole("region", { name: "Recommendation application", exact: true });
		await expect(application).toContainText("Recommendations applied");
		await expect(application).toContainText("8 preserved · 6 updated");
		await page.reload();
		await expect(application).toContainText("Recommendations applied");
		const comparison = page.getByText("Previous and revised saved tests", { exact: true });
		await expect(comparison).toBeVisible();
		await comparison.click();
		const versionedLinks = page.getByRole("link").filter({ hasText: /TC-001/ });
		const hrefs = await versionedLinks.evaluateAll((links) => links.map((link) => link.href));
		expect(hrefs.some((link) => new URL(link).searchParams.get("suite") === oldSuiteId)).toBe(true);
		expect(hrefs.some((link) => new URL(link).searchParams.get("suite") === newSuiteId)).toBe(true);
		await expect(page.getByText("Loading project recommendations…", { exact: true })).toHaveCount(0);
		await page.screenshot({ path: testInfo.outputPath("scoped-impact-applied.png") });

		await application.getByRole("button", { name: "Review changed tests", exact: true }).click();
		const selected = page.getByRole("region", { name: "Selected test case", exact: true });
		await expect(selected).toContainText(exactExpected);
		await expect(page.getByRole("button", { name: /TC-006/ })).toBeVisible();
		await expect(page.getByRole("button", { name: /TC-007/ })).toHaveCount(0);
		await expect(page.getByRole("button", { name: "Review saved suite", exact: true })).toBeVisible();
		expect(fixture.api.getProject().current_snapshots.test_cases.payload.test_cases.slice(6)).toEqual(fixture.cases.slice(6));
		expect(fixture.applies).toHaveLength(1);
		expect(fixture.statuses).toContain(fixture.applies[0].requestId);
		expect(fixture.writes).toEqual([`POST ${applyPath}`]);
		expect(fixture.errors).toEqual([]);
	});

	test("requires explicit release for an interrupted apply", async ({ page }) => {
		const fixture = await setup(page, { interrupted: true });
		await page.goto(`${root}/review/tests`);
		const scoped = page.getByRole("region", { name: "Scoped impact application", exact: true });
		const release = scoped.getByRole("button", { name: "Acknowledge interrupted application", exact: true });
		await expect(release).toBeEnabled();
		expect(fixture.writes).toEqual([]);
		await release.click();
		await expect(release).toHaveCount(0);
		expect(fixture.api.getProject().current_snapshots.test_cases.snapshot_id).toBe(oldSuiteId);
		expect(fixture.applies).toEqual([]);
		expect(fixture.writes).toEqual([`POST ${fixture.releasePath}`]);
		expect(fixture.errors).toEqual([]);
	});
});
