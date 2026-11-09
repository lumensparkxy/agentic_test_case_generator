import { test, expect } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installUseCaseReviewApi, useCaseProjectFixture, useCaseSnapshotFixture, USE_CASE_PROJECT_ID } from "./support/use-case-review.js";
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
async function setup(
	page,
	{ failure = false, gate, reviewGate, reviewUnavailableOnce = false, repeatedTitle = false, generationOutage = false } = {}
) {
	let project = useCaseProjectFixture({ reviewState: "approved" });
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
	if (generationOutage) {
		const snapshot = project.current_snapshots.test_cases;
		snapshot.operation = "testcases.generate";
		snapshot.metadata = { review_retry_available: true };
		snapshot.payload.generation_tasks = [];
		snapshot.payload.substantive_assessment = { status: "unknown", diagnostics: { failure_reason: "provider_unavailable" } };
	}
	project.stage_state.test_cases = { current_snapshot_id: "tests-old", approved: false, stale: false };
	const api = await installUseCaseReviewApi(page, { initialProject: project });
	const requests = [];
	const reviewRequests = [];
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
		if (repeatedTitle) {
			const { recommendations, summary } = project.current_snapshots.impact_analysis.payload;
			recommendations.push({ ...recommendations[0], recommendation_id: "repair-2", test_case_id: "TC-FB-002" });
			summary.recommendation_counts.update = 2;
		}
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
			substantive_assessment: { status: "unknown", reason: "Targeted changes require assessment of the combined suite." },
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
		project.current_snapshots.test_cases.operation = "impact.update.apply";
		project.stage_state.test_cases.current_snapshot_id = "tests-new";
		api.setProject(project);
		await route.fulfill({ json: project });
	});
	await page.route(`**${path}/test-cases/review`, async (route) => {
		reviewRequests.push(route.request().postDataJSON());
		if (reviewGate) await reviewGate;
		if (reviewUnavailableOnce && reviewRequests.length === 1) {
			return route.fulfill({ status: 503, json: { detail: "Independent review is unavailable. The saved suite is unchanged." } });
		}
		project = structuredClone(project);
		project.current_revision++;
		const suite = project.current_snapshots.test_cases;
		suite.source_snapshot_id = suite.snapshot_id;
		suite.snapshot_id = "tests-reviewed";
		suite.operation = "test_cases.review";
		delete suite.metadata?.review_retry_available;
		suite.approved = true;
		suite.payload.approved = true;
		suite.payload.substantive_assessment = { status: "assessed_complete", design_status: "assessed_complete" };
		suite.payload.review = { approved: true, score: 100, threshold: 90 };
		project.stage_state.test_cases = { ...project.stage_state.test_cases, current_snapshot_id: suite.snapshot_id, approved: true };
		api.setProject(project);
		await route.fulfill({ json: project });
	});
	await seedAuthenticatedSession(page);
	await page.goto(`${path}/test-cases`);
	if (!generationOutage) await expect(page.getByRole("heading", { name: "1 generation task needs concrete tests" })).toBeVisible();
	return { requests, reviewRequests, currentRevision: () => project.current_revision };
}

test("distinguishes repair recommendations with the same title by case ID", { tag: "@p1" }, async ({ page }) => {
	const { requests } = await setup(page, { repeatedTitle: true });
	await page.getByRole("button", { name: "Plan targeted repair" }).click();
	const first = page.getByRole("checkbox", { name: "Accept Generate concrete checkout coverage (TC-FB-001)", exact: true });
	const second = page.getByRole("checkbox", { name: "Accept Generate concrete checkout coverage (TC-FB-002)", exact: true });
	await first.check();
	await expect(second).not.toBeChecked();
	await page.getByRole("button", { name: "Apply 1 recommendation", exact: true }).click();
	await expect.poll(() => requests.map((request) => request.accepted_recommendation_ids)).toEqual([["repair-1"]]);
});

for (const [interruption, draft] of [
	["transport", false],
	["unconfirmed", false],
	["transport", true],
]) {
	test(
		`recovers ${interruption} ${draft ? "draft" : "approved-plan"} generation with the same request identity`,
		{ tag: "@p1" },
		async ({ page }) => {
			const project = draft ? useCaseProjectFixture({ snapshot: null }) : useCaseProjectFixture({ reviewState: "approved" });
			const api = await installUseCaseReviewApi(page, { initialProject: project });
			const attempts = [];
			const source = draft ? useCaseSnapshotFixture() : project.current_snapshots.use_cases;
			const payload = {
				test_cases: [concrete],
				approved: !draft,
				review: { approved: !draft, score: draft ? 72 : 100, threshold: 90 },
				coverage_plan: source.payload.coverage_plan,
				requirement_analysis: source.payload.requirement_analysis,
			};
			await page.route("**/testcases/generate", async (route) => {
				const request = route.request();
				attempts.push({ id: request.headers()["x-request-id"], payload: request.postDataJSON() });
				if (attempts.length === 1) {
					const saved = structuredClone(project);
					if (draft) {
						saved.current_revision++;
						saved.current_snapshots.use_cases = { ...source, project_revision: saved.current_revision };
						saved.stage_state.use_cases = { current_snapshot_id: source.snapshot_id, approved: false, stale: false };
					}
					saved.current_revision++;
					saved.current_snapshots.test_cases = {
						snapshot_id: "tests-published",
						source_snapshot_id: source.snapshot_id,
						operation: "testcases.generate",
						approved: !draft,
						payload,
					};
					saved.stage_state.test_cases = { current_snapshot_id: "tests-published", approved: !draft, stale: false };
					api.setProject(saved);
					if (interruption === "transport") return route.abort("failed");
					return route.fulfill({ status: 503, json: { detail: { code: "generation_publication_unconfirmed" } } });
				}
				await route.fulfill({ json: payload });
			});
			await seedAuthenticatedSession(page);
			await page.goto(`${path}/test-cases`);
			await page.getByRole("button", { name: /^start generation$|^generate from \d+ approved$/i }).click();
			await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
			await expect(page.getByText(concrete.title).first()).toBeVisible();
			if (draft) {
				await expect(page.getByText("Export blocked", { exact: true })).toBeVisible();
				await expect(page.getByLabel("Test Cases readiness")).toContainText("Awaiting Use Cases review");
			}
			expect(attempts).toHaveLength(2);
			expect(attempts[0].id).toBeTruthy();
			expect(attempts[1]).toEqual(attempts[0]);
		}
	);
}

test("retries a generated suite's unavailable review after reload without regeneration", { tag: "@p1" }, async ({ page }) => {
	const { requests, reviewRequests, currentRevision } = await setup(page, { generationOutage: true, reviewUnavailableOnce: true });
	const generationRequests = [];
	await page.route("**/testcases/generate", (route) => {
		generationRequests.push(route.request().postDataJSON());
		return route.abort();
	});
	const review = page.getByRole("button", { name: "Review saved suite", exact: true });
	await expect(page.getByText("Export blocked", { exact: true })).toBeVisible();
	const revision = currentRevision();
	await review.click();
	await expect(page.getByRole("alert").filter({ hasText: "The saved suite is unchanged." })).toBeVisible();
	await page.reload();
	await expect(review).toBeEnabled();
	await review.click();
	await expect(page.getByText("Saved suite review passed.", { exact: true })).toBeVisible();
	await expect(page.getByText("Export blocked", { exact: true })).toHaveCount(0);
	await expect(page.getByRole("button", { name: /TC-GOOD Preserved concrete test/ })).toBeVisible();
	expect(reviewRequests).toEqual(Array(2).fill({ snapshot_id: "tests-old", base_project_revision: revision }));
	expect(generationRequests).toEqual([]);
	expect(requests).toEqual([]);
});

test("plans and explicitly accepts repair, then keeps concrete tests and requires review", { tag: "@p1" }, async ({ page }) => {
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
	await expect(page.getByRole("heading", { name: "1 generation task needs concrete tests" })).toHaveCount(0);
	await expect(page.getByText(/Review the changed tests before export/).first()).toBeVisible();
});

test("failed repair retains concrete tests and unfinished work", { tag: "@p1" }, async ({ page }) => {
	await setup(page, { failure: true });
	await page.setViewportSize({ width: 390, height: 844 });
	await page.screenshot({ path: test.info().outputPath("repair-work-mobile.png"), fullPage: true });
	await page.getByRole("button", { name: "Plan targeted repair" }).click();
	await page.getByRole("checkbox", { name: "Accept Generate concrete checkout coverage" }).check();
	await page.getByRole("button", { name: "Apply 1 recommendation", exact: true }).click();
	await expect(page.getByText(/Impact update failed: Project changed/)).toBeVisible();
	await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
	await expect(page.getByRole("button", { name: /TC-GOOD Preserved concrete test/ })).toBeVisible();
	await expect(page.getByRole("heading", { name: "1 generation task needs concrete tests" })).toBeVisible();
});

test("reviews the exact saved repaired suite and retries an unavailable assessment", { tag: "@p1" }, async ({ page }) => {
	const gate = createDeferred();
	const { reviewRequests, currentRevision } = await setup(page, { reviewGate: gate.promise, reviewUnavailableOnce: true });
	await page.getByRole("button", { name: "Plan targeted repair" }).click();
	await page.getByRole("checkbox", { name: "Accept Generate concrete checkout coverage" }).check();
	await page.getByRole("button", { name: "Apply 1 recommendation", exact: true }).click();
	const review = page.getByRole("button", { name: "Review saved suite", exact: true });
	await expect(review).toBeEnabled();
	const revision = currentRevision();
	await review.focus();
	await page.keyboard.press("Enter");
	await page.keyboard.press("Enter");
	await expect(review).toBeDisabled();
	await expect(page.getByText("Reviewing saved test cases…", { exact: true })).toBeVisible();
	expect(reviewRequests).toEqual([{ snapshot_id: "tests-new", base_project_revision: revision }]);
	gate.resolve();
	await expect(page.getByRole("alert").filter({ hasText: "The saved suite is unchanged." })).toBeVisible();
	await expect(review).toBeEnabled();
	await review.click();
	await expect(page.getByText("Saved suite review passed.", { exact: true })).toBeVisible();
	expect(reviewRequests).toEqual(Array(2).fill({ snapshot_id: "tests-new", base_project_revision: revision }));
	await page.reload();
	await expect(review).toHaveCount(0);
	await expect(page.getByText("Export blocked", { exact: true })).toHaveCount(0);
	await expect(page.getByRole("button", { name: /TC-GOOD Preserved concrete test/ })).toBeVisible();
});
