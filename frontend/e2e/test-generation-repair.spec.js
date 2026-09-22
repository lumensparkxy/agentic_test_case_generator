import { test, expect } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";
import { createDeferred } from "./support/workspace.js";

const path = `/projects/${USE_CASE_PROJECT_ID}`;
const concrete = {
	id: "TC-GOOD",
	title: "Preserved concrete test",
	description: "Verify checkout",
	steps: [{ step: 1, action: "Submit the order", expected: "Confirmation number is displayed" }],
	linked_requirement_ids: ["REQ-101"],
	scenario_refs: ["REQ-101-SCN-01"],
	status: "Ready",
};
async function setup(page, { failure = false, gate } = {}) {
	let project = useCaseProjectFixture();
	project.current_snapshots.test_cases = {
		snapshot_id: "tests-old",
		version: 1,
		stage: "test_cases",
		payload: {
			test_cases: [concrete],
			approved: false,
			review: { approved: false, score: 0, threshold: 90 },
			generation_tasks: [
				{
					source_test_case_id: "TC-FB-001",
					requirement_ids: ["REQ-101"],
					scenario_refs: ["REQ-101-SCN-02"],
					reason: "This row contains generation instructions instead of concrete test actions.",
				},
			],
		},
	};
	project.stage_state.test_cases = { current_snapshot_id: "tests-old", approved: false, stale: false };
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	const requests = [];
	await page.route(`**${path}/impact-analysis`, async (route) => {
		project = structuredClone(project);
		project.current_revision++;
		project.current_snapshots.impact_analysis = {
			snapshot_id: "impact-repair",
			payload: {
				changed_items: [],
				impacted_test_cases: [],
				summary: { changed_item_count: 0, recommendation_counts: { update: 1 } },
				recommendations: [
					{
						recommendation_id: "repair-1",
						title: "Generate concrete checkout coverage",
						reason: "Replace unfinished generation instructions",
						action: "update",
						accepted: false,
						requirement_id: "REQ-101",
						test_case_id: "TC-FB-001",
						scenario_refs: ["REQ-101-SCN-02"],
					},
				],
			},
		};
		api.setProject(project);
		await route.fulfill({ json: project });
	});
	await page.route(`**${path}/impact-update/apply`, async (route) => {
		requests.push(route.request().postDataJSON());
		if (gate) await gate;
		if (failure) return route.fulfill({ status: 409, json: { detail: "Project changed. Analyze impact again." } });
		project = structuredClone(project);
		project.current_revision++;
		project.current_snapshots.test_cases.payload = {
			test_cases: [
				concrete,
				{
					...concrete,
					id: "TC-FB-001",
					title: "Declined card keeps the cart",
					steps: [{ step: 1, action: "Submit a declined saved card", expected: "Payment error appears and cart contents remain" }],
					scenario_refs: ["REQ-101-SCN-02"],
					status: "In Review",
				},
			],
			generation_tasks: [],
			approved: false,
			review: { approved: false, score: 0, threshold: 90, summary: "Targeted changes require suite review." },
			impact_update_result: { preserved_count: 1, updated_count: 1, added_count: 0, deprecated_count: 0 },
		};
		project.impact_application = {
			status: "applied",
			analysis_snapshot_id: "impact-repair",
			accepted_recommendation_ids: ["repair-1"],
			completed_at: "2026-09-22T10:00:00Z",
			result_snapshot_id: "tests-new",
			changed_test_case_ids: ["TC-FB-001"],
			preserved_count: 1,
			updated_count: 1,
			added_count: 0,
			deprecated_count: 0,
		};
		project.current_snapshots.test_cases.snapshot_id = "tests-new";
		api.setProject(project);
		await route.fulfill({ json: project });
	});
	await seedAuthenticatedSession(page);
	await page.goto(`${path}/test-cases`);
	await expect(page.getByRole("heading", { name: "1 generation tasks need concrete tests" })).toBeVisible();
	return { requests };
}

test("plans and explicitly accepts repair, then keeps concrete tests and requires review", async ({ page }) => {
	const deferred = createDeferred();
	const { requests } = await setup(page, { gate: deferred.promise });
	await expect(page.getByRole("button", { name: /TC-FB-001/ })).toHaveCount(0);
	await page.getByRole("button", { name: "Plan targeted repair" }).click();
	await expect(page.getByRole("button", { name: "Apply 0 recommendations" })).toBeDisabled();
	await page.getByRole("checkbox", { name: "Accept Generate concrete checkout coverage" }).check();
	await page.getByRole("button", { name: "Apply 1 recommendation", exact: true }).click();
	await expect(page.getByRole("button", { name: /Applying/ })).toBeDisabled();
	expect(requests).toHaveLength(1);
	expect(requests[0].accepted_recommendation_ids).toEqual(["repair-1"]);
	deferred.resolve();
	await expect(page.getByText(/Recommendations applied/).first()).toBeVisible();
	await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
	await expect(page.getByRole("button", { name: /TC-FB-001 Declined card keeps the cart/ })).toBeVisible();
	await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
	await expect(page.getByRole("button", { name: /TC-GOOD Preserved concrete test/ })).toBeVisible();
	await expect(page.getByRole("heading", { name: "1 generation tasks need concrete tests" })).toHaveCount(0);
	await expect(page.getByText(/Review the changed tests before export/).first()).toBeVisible();
});

test("failed repair retains concrete tests and unfinished work", async ({ page }) => {
	await setup(page, { failure: true });
	await page.setViewportSize({ width: 390, height: 844 });
	await page.screenshot({ path: test.info().outputPath("repair-work-mobile.png"), fullPage: true });
	await page.getByRole("button", { name: "Plan targeted repair" }).click();
	await page.getByRole("checkbox", { name: "Accept Generate concrete checkout coverage" }).check();
	await page.getByRole("button", { name: "Apply 1 recommendation", exact: true }).click();
	await expect(page.getByText(/Impact update failed: Project changed/)).toBeVisible();
	await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
	await expect(page.getByRole("button", { name: /TC-GOOD Preserved concrete test/ })).toBeVisible();
	await expect(page.getByRole("heading", { name: "1 generation tasks need concrete tests" })).toBeVisible();
});
