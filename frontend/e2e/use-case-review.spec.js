import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { seedAuthenticatedSession } from "./support/auth.js";
import {
	USE_CASE_BASE_REVISION,
	USE_CASE_PROJECT_ID,
	USE_CASE_SNAPSHOT_ID,
	applyUseCaseReviewDecision,
	installUseCaseReviewApi,
	useCaseCoveragePlanFixture,
	useCaseProjectFixture,
	useCaseReviewResponseFixture,
	useCaseSnapshotFixture,
} from "./support/use-case-review.js";
import { createDeferred } from "./support/workspace.js";

// Accessibility contract for the workbench: content sections are named
// landmarks, requirement groups are named by source ID, and review outcomes use
// status/alert semantics. Tests intentionally avoid CSS and implementation selectors.
function reviewMain(page) {
	return page.getByRole("main", { name: /^Use Cases$/i });
}

function machineReviewRegion(page) {
	return reviewMain(page).getByRole("region", { name: /^Machine quality review$/i });
}

function humanReviewRegion(page) {
	return reviewMain(page).getByRole("region", { name: /^Human review status$/i });
}

function decisionPanel(page) {
	return reviewMain(page).getByRole("form", { name: /^Human review decision$/i });
}

function requirementGroup(page, requirementId) {
	return reviewMain(page).getByRole("region", { name: new RegExp(`^${requirementId}(?:\\b|\\s|·)`, "i") });
}

function approveButton(page) {
	return decisionPanel(page).getByRole("button", { name: /^Approve Use Cases$/i });
}

function approveOption(page) {
	return decisionPanel(page).getByRole("radio", { name: /^Approve\b/i });
}

function requestChangesOption(page) {
	return decisionPanel(page).getByRole("radio", { name: /^Request changes\b/i });
}

function requestChangesButton(page) {
	return decisionPanel(page).getByRole("button", { name: /^Request changes$/i });
}

function reviewComment(page) {
	return decisionPanel(page).getByRole("textbox", { name: /^Review comment/i });
}

function reviewAnnouncement(page) {
	return decisionPanel(page).getByRole("status");
}

async function openUseCaseReview(page, options = {}) {
	const api = await installUseCaseReviewApi(page, options);
	await seedAuthenticatedSession(page);
	await page.goto(buildProjectPath(USE_CASE_PROJECT_ID, "use-cases"));
	await expect(page.getByRole("heading", { name: /^Use Cases$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
	return api;
}

async function expectDurableRefresh(api) {
	await expect.poll(() => api.requests.projectDetail.length).toBeGreaterThanOrEqual(2);
	await expect.poll(() => api.requests.projectList.length).toBeGreaterThanOrEqual(2);
	await expect.poll(() => api.requests.orchestratorStatus.length).toBeGreaterThanOrEqual(2);
	await expect.poll(() => api.requests.orchestratorRuns.length).toBe(0);
	await expect.poll(() => api.requests.workspaceSummary.length).toBeGreaterThanOrEqual(2);
}

test.describe("Use Cases review workbench", () => {
	test("renders the exact pending snapshot, canonical scenario count, requirement groups, and secondary provenance", async ({ page }) => {
		const project = useCaseProjectFixture();
		project.current_snapshots.test_cases = {
			...useCaseSnapshotFixture({ snapshot_id: "snapshot-test-cases-copy", stage: "test_cases" }),
			payload: {
				coverage_plan: [
					{
						requirement_id: "REQ-LEGACY",
						requirement_text: "This stale Test Cases copy must not render.",
						scenarios: [],
					},
				],
			},
		};
		const api = await openUseCaseReview(page, { initialProject: project });

		const main = reviewMain(page);
		await expect(main).toContainText(project.name);
		await expect(main).toContainText("Use Cases v3");
		await expect(main).toContainText("4 scenarios");
		await expect(main).toContainText("2 requirement groups");
		await expect(main).toContainText("Use Case plan coverage");
		await expect(main).toContainText("100%");
		await expect(main).toContainText("Requirements analyzed");
		await expect(main).toContainText("2 / 2");
		await expect(main).toContainText(/current|fresh/i);

		const scenarioLists = main.getByRole("list", { name: /^Scenarios for REQ-/i });
		await expect(scenarioLists).toHaveCount(2);
		await expect(scenarioLists.getByRole("listitem")).toHaveCount(4);

		const checkoutGroup = requirementGroup(page, "REQ-101");
		await expect(checkoutGroup).toContainText("Source requirement REQ-101");
		await expect(checkoutGroup).toContainText("Complete express checkout");
		await expect(checkoutGroup).toContainText(/declined saved card/i);
		await checkoutGroup.getByText(/^Coverage context$/i).click();
		await expect(checkoutGroup).toContainText("saved_payment_method");
		await expect(checkoutGroup).toContainText("Duplicate charge after retry");

		const approvalGroup = requirementGroup(page, "REQ-202");
		await expect(approvalGroup).toContainText("Source requirement REQ-202");
		await expect(approvalGroup).toContainText("Require supervisor approval");
		await approvalGroup.getByText(/^Coverage context$/i).click();
		await expect(approvalGroup).toContainText("Threshold bypass");

		const machineReview = machineReviewRegion(page);
		await expect(machineReview).toContainText("72");
		await expect(machineReview).toContainText("85");
		await expect(machineReview).toContainText("requires attention");
		await expect(main.getByRole("region", { name: /^Machine review findings$/i }).getByRole("listitem")).toHaveCount(1);
		await expect(humanReviewRegion(page)).toContainText(/pending human decision/i);

		await expect(main.getByText(USE_CASE_SNAPSHOT_ID, { exact: true })).not.toBeVisible();
		await main.getByText(/^Details$/i).click();
		await expect(main.getByText(USE_CASE_SNAPSHOT_ID, { exact: true })).toBeVisible();
		await expect(main.getByText("1.0", { exact: true })).toBeVisible();
		await expect(main).not.toContainText("This stale Test Cases copy must not render.");

		const search = main.getByRole("searchbox", { name: /^Search use cases$/i });
		const detailRequestsBeforeSearch = api.requests.projectDetail.length;
		await search.fill("authorization");
		await expect(requirementGroup(page, "REQ-202")).toBeVisible();
		await expect(requirementGroup(page, "REQ-101")).toHaveCount(0);
		expect(api.requests.projectDetail).toHaveLength(detailRequestsBeforeSearch);
	});

	test("shows incomplete Use Cases planning coverage from the artifact contract", async ({ page }) => {
		const snapshot = useCaseSnapshotFixture();
		snapshot.payload.coverage_metrics = {
			...snapshot.payload.coverage_metrics,
			requirements_total: 3,
			requirements_with_analysis: 2,
			requirements_with_coverage_plan: 2,
			use_case_plan_coverage_ratio: 0.67,
		};
		await openUseCaseReview(page, { initialProject: useCaseProjectFixture({ snapshot }) });

		const metrics = reviewMain(page).getByRole("region", { name: /^Coverage metrics$/i });
		await expect(metrics).toContainText("Use Case plan coverage");
		await expect(metrics).toContainText("67%");
		await expect(metrics).toContainText("Requirements analyzed");
		await expect(metrics).toContainText("2 / 3");
		await expect(metrics).toContainText("Requirements planned");
	});

	test("routes an Approve Use Cases recommendation to this workbench and never Upload", async ({ page }) => {
		await installUseCaseReviewApi(page);
		await seedAuthenticatedSession(page);
		await page.goto(buildProjectPath(USE_CASE_PROJECT_ID));

		const task = page.getByLabel("Contextual task");
		await expect(task.getByRole("heading", { name: /^Approve Use Cases$/i })).toBeVisible({ timeout: 30_000 });
		await task.getByRole("button", { name: /^Open workbench$/i }).click();

		await expect(page).toHaveURL(buildProjectPath(USE_CASE_PROJECT_ID, "use-cases"));
		await expect(reviewMain(page)).toBeVisible();
		await expect(machineReviewRegion(page)).toBeVisible();
		await expect(page.getByRole("heading", { name: /^Upload Requirements$/i })).toHaveCount(0);
	});

	test("keeps machine-passed Use Cases pending until a matching human approval exists", async ({ page }) => {
		const project = useCaseProjectFixture();
		project.current_snapshots.use_cases.payload.review = {
			...project.current_snapshots.use_cases.payload.review,
			approved: true,
			score: 96,
			summary: "Automated quality checks passed.",
			blocking_issues: [],
			unmet_criteria: [],
		};
		project.stage_state.use_cases.approved = true;
		await openUseCaseReview(page, { initialProject: project });

		await expect(machineReviewRegion(page)).toContainText(/quality check passed/i);
		await expect(humanReviewRegion(page)).toContainText(/pending human decision/i);
		await expect(reviewMain(page).getByRole("status", { name: "Current human review status" })).toContainText(/Awaiting human review/i);
		await page
			.getByRole("navigation", { name: "Project navigation" })
			.getByRole("link", { name: /^Overview,/i })
			.click();
		await expect(
			page.getByRole("navigation", { name: "Project navigation" }).getByRole("link", { name: /^Use Cases, Needs attention$/i })
		).toBeVisible();
		await expect(page.getByLabel("Contextual task").getByRole("heading", { name: /^Approve Use Cases$/i })).toBeVisible();
		await page
			.getByRole("navigation", { name: "Project navigation" })
			.getByRole("link", { name: /^Requirements,/i })
			.click();
		await expect(page.getByLabel("Contextual task")).toHaveCount(0);
	});

	test("approves the exact snapshot and revision, then refreshes durable project, orchestrator, Home, and future Inbox state", async ({
		page,
	}) => {
		const api = await openUseCaseReview(page);
		await reviewComment(page).fill("  Coverage is ready for downstream generation.  ");
		await approveButton(page).click();

		await expect.poll(() => api.requests.review.length).toBe(1);
		expect(api.requests.review[0].payload).toEqual({
			snapshot_id: USE_CASE_SNAPSHOT_ID,
			base_project_revision: USE_CASE_BASE_REVISION,
			decision: "approve",
			comment: "Coverage is ready for downstream generation.",
		});
		expect(api.requests.review[0].headers["x-request-id"]).toBeTruthy();
		await expect(reviewAnnouncement(page)).toContainText(/Use Cases approved/i);
		await expectDurableRefresh(api);
		await expect(humanReviewRegion(page)).toContainText(/approved/i);
		await expect(humanReviewRegion(page)).toContainText("Coverage is ready for downstream generation.");

		await page.reload();
		await expect(page.getByRole("heading", { name: /^Use Cases$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		await expect(humanReviewRegion(page)).toContainText(/approved/i);
		await expect(machineReviewRegion(page)).toContainText("requires attention");

		await page
			.getByRole("navigation", { name: "Global navigation" })
			.getByRole("link", { name: /^Home$/i })
			.click();
		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i, level: 1 })).toBeVisible();
		await expect(page.getByRole("region", { name: /^My work$/i })).not.toContainText("Approve Use Cases");
	});

	test("requires feedback for Request changes and persists the exact decision", async ({ page }) => {
		const api = await openUseCaseReview(page);

		await requestChangesOption(page).check();
		await requestChangesButton(page).click();
		await expect(decisionPanel(page).getByRole("alert")).toContainText(/comment describing the requested changes/i);
		await expect(reviewComment(page)).toBeFocused();
		await expect(reviewComment(page)).toHaveAttribute("required", "");
		await expect(reviewComment(page)).toHaveAttribute("aria-required", "true");
		await expect(reviewComment(page)).toHaveAttribute("aria-invalid", "true");
		await expect(reviewComment(page)).toHaveAttribute("aria-errormessage", "use-case-review-error");
		expect(api.requests.review).toHaveLength(0);

		await reviewComment(page).fill("  Add converted-currency threshold coverage.  ");
		await requestChangesButton(page).click();
		await expect.poll(() => api.requests.review.length).toBe(1);
		expect(api.requests.review[0].payload).toEqual({
			snapshot_id: USE_CASE_SNAPSHOT_ID,
			base_project_revision: USE_CASE_BASE_REVISION,
			decision: "request_changes",
			comment: "Add converted-currency threshold coverage.",
		});
		await expect(reviewAnnouncement(page)).toContainText(/changes requested/i);
		await expectDurableRefresh(api);
		await expect(humanReviewRegion(page)).toContainText(/changes requested/i);
		await expect(humanReviewRegion(page)).toContainText("Add converted-currency threshold coverage.");

		await page.reload();
		await expect(page.getByRole("heading", { name: /^Use Cases$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
		await expect(humanReviewRegion(page)).toContainText(/changes requested/i);
		await expect(humanReviewRegion(page)).toContainText("Add converted-currency threshold coverage.");
	});

	test("preserves feedback and the selected decision on 409 until Reload latest replaces the artifact", async ({ page }) => {
		const latestCoveragePlan = useCaseCoveragePlanFixture();
		latestCoveragePlan[1].scenarios.push({
			id: "REQ-202-SCN-03",
			requirement_id: "REQ-202",
			scenario_type: "Data Variation",
			title: "Apply approval after currency conversion",
			objective: "Compare the converted account-currency amount with the approval threshold.",
			priority: "High",
			must_have: true,
		});
		const latestSnapshot = useCaseSnapshotFixture({
			snapshot_id: "snapshot-use-cases-v4",
			version: 4,
			project_revision: USE_CASE_BASE_REVISION + 1,
			created_at: "2026-07-17T12:30:00Z",
			payload: {
				...useCaseSnapshotFixture().payload,
				coverage_plan: latestCoveragePlan,
				coverage_metrics: {
					...useCaseSnapshotFixture().payload.coverage_metrics,
					planned_scenarios_total: 5,
				},
			},
		});
		const latestProject = useCaseProjectFixture({ snapshot: latestSnapshot, current_revision: USE_CASE_BASE_REVISION + 1 });
		const api = await openUseCaseReview(page, {
			reviewScenarios: [
				{
					status: 409,
					serverProject: latestProject,
					payload: {
						detail: {
							message: "The reviewed Use Cases snapshot is no longer current. Reload before submitting a decision.",
							latest_revision: USE_CASE_BASE_REVISION + 1,
							current_snapshot_id: latestSnapshot.snapshot_id,
							reload_required: true,
						},
					},
				},
			],
		});

		const comment = "Keep my feedback while the latest artifact loads.";
		await reviewComment(page).fill(comment);
		await requestChangesOption(page).check();
		await requestChangesButton(page).click();

		const conflict = decisionPanel(page).getByRole("alert");
		await expect(conflict).toContainText(/no longer current/i);
		await expect(reviewComment(page)).toHaveValue(comment);
		await expect(requestChangesOption(page)).toBeChecked();
		await expect(reviewMain(page)).not.toContainText("Apply approval after currency conversion");
		expect(api.requests.review).toHaveLength(1);

		await conflict.getByRole("button", { name: /^Reload latest$/i }).click();
		await expect(reviewMain(page)).toContainText("Use Cases v4");
		await expect(reviewMain(page)).toContainText("5 scenarios");
		await expect(reviewMain(page)).toContainText("Apply approval after currency conversion");
		await expect(reviewComment(page)).toHaveValue(comment);
		await expect(requestChangesOption(page)).toBeChecked();
		await expect(decisionPanel(page)).toBeFocused();
		expect(api.requests.review).toHaveLength(1);
	});

	test("retains the attempted decision after a 503 and retries the identical idempotent request", async ({ page }) => {
		const api = await openUseCaseReview(page, {
			reviewScenarios: [{ status: 503, payload: { detail: "Use Cases review persistence is unavailable" } }, {}],
		});
		const comment = "Approve after persistence recovers.";
		await reviewComment(page).fill(comment);
		await approveButton(page).click();

		const error = decisionPanel(page).getByRole("alert");
		await expect(error).toContainText("Use Cases review persistence is unavailable");
		await expect(reviewComment(page)).toHaveValue(comment);
		await expect(approveOption(page)).toBeChecked();
		await error.getByRole("button", { name: /^Retry$/i }).click();

		await expect.poll(() => api.requests.review.length).toBe(2);
		expect(api.requests.review[1].payload).toEqual(api.requests.review[0].payload);
		expect(api.requests.review[1].headers["x-request-id"]).toBe(api.requests.review[0].headers["x-request-id"]);
		await expect(reviewAnnouncement(page)).toContainText(/Use Cases approved/i);
		await expect(reviewComment(page)).toHaveValue(comment);
		await expect(decisionPanel(page)).toBeFocused();
	});

	test("distinguishes a saved decision from a failed workspace refresh and recovers without reposting", async ({ page }) => {
		const api = await openUseCaseReview(page, {
			detailScenarios: [{}, { status: 503, payload: { detail: "Project refresh is temporarily unavailable" } }, {}],
		});
		const comment = "Keep the durable approval context.";
		await reviewComment(page).fill(comment);
		await approveButton(page).click();

		const refreshError = decisionPanel(page).getByRole("alert");
		await expect(refreshError).toContainText(/Decision saved; refresh required/i);
		await expect(refreshError).toContainText(/project state could not be refreshed/i);
		await expect(reviewComment(page)).toHaveValue(comment);
		await expect(approveOption(page)).toBeDisabled();
		await expect(humanReviewRegion(page)).toContainText(/approved/i);
		await expect(page.getByRole("status", { name: /^Current human review status$/i })).toHaveText(/Human approved/i);
		expect(api.requests.review).toHaveLength(1);

		await refreshError.getByRole("button", { name: /^Reload latest$/i }).click();
		await expect(decisionPanel(page).getByRole("alert")).toHaveCount(0);
		await expect(humanReviewRegion(page)).toContainText(/approved/i);
		await expect(reviewComment(page)).toHaveValue(comment);
		await expect(decisionPanel(page)).toBeFocused();
		expect(api.requests.review).toHaveLength(1);
	});

	test("loads a newer same-project artifact after saving without leaving the workbench busy", async ({ page }) => {
		const initialProject = useCaseProjectFixture();
		const committedProject = applyUseCaseReviewDecision(initialProject, {
			decision: "approve",
			comment: "Approve the reviewed version.",
		});
		const latestSnapshot = useCaseSnapshotFixture({
			snapshot_id: "snapshot-use-cases-v4-after-review",
			version: 4,
			project_revision: committedProject.current_revision + 1,
			created_at: "2026-07-17T12:45:00Z",
		});
		const latestProject = useCaseProjectFixture({
			snapshot: latestSnapshot,
			current_revision: committedProject.current_revision + 1,
			updated_at: latestSnapshot.created_at,
		});
		const api = await openUseCaseReview(page, {
			initialProject,
			reviewScenarios: [
				{
					nextProject: latestProject,
					payload: (_serverProject, payload, requestId) => useCaseReviewResponseFixture(committedProject, { ...payload, requestId }),
				},
			],
		});
		await reviewComment(page).fill("Approve the reviewed version.");
		await approveButton(page).click();

		await expect(reviewMain(page)).toContainText("Use Cases v4");
		await expect(reviewMain(page)).not.toHaveAttribute("aria-busy", "true");
		await expect(reviewAnnouncement(page)).toContainText(/newer Use Cases version is ready for review/i);
		await expect(approveOption(page)).toBeEnabled();
		await expect(reviewComment(page)).toHaveValue("Approve the reviewed version.");
		await expect(humanReviewRegion(page)).toContainText(/pending human decision/i);
		expect(api.requests.review).toHaveLength(1);
	});

	for (const state of [
		{ reviewState: "approved", label: "Approved", comment: "Coverage is ready." },
		{
			reviewState: "request_changes",
			label: "Changes requested",
			comment: "Add converted-currency approval coverage.",
		},
	]) {
		test(`restores the durable ${state.reviewState.replace("_", "-")} state after reload`, async ({ page }) => {
			await openUseCaseReview(page, { initialProject: useCaseProjectFixture({ reviewState: state.reviewState }) });
			await expect(humanReviewRegion(page)).toContainText(new RegExp(state.label, "i"));
			await expect(reviewMain(page).getByRole("status", { name: "Current human review status" })).toContainText(
				new RegExp(state.label === "Approved" ? "Human approved" : state.label, "i")
			);
			await expect(humanReviewRegion(page)).toContainText(state.comment);
			await expect(humanReviewRegion(page)).toContainText("Playwright E2E");

			await page.reload();
			await expect(page.getByRole("heading", { name: /^Use Cases$/i, level: 1 })).toBeVisible({ timeout: 30_000 });
			await expect(humanReviewRegion(page)).toContainText(new RegExp(state.label, "i"));
			await expect(humanReviewRegion(page)).toContainText(state.comment);
		});
	}

	test("keeps a durable internal reviewer ID in Details instead of the primary review status", async ({ page }) => {
		const project = useCaseProjectFixture({ reviewState: "approved" });
		const durableReview = project.stage_state.use_cases.metadata.latest_human_review;
		delete durableReview.reviewer_name;
		delete durableReview.reviewer_email;
		await openUseCaseReview(page, { initialProject: project });

		await expect(humanReviewRegion(page)).toContainText(/authenticated reviewer/i);
		await expect(humanReviewRegion(page)).not.toContainText(durableReview.reviewer_user_id);
		await reviewMain(page)
			.getByText(/^Details$/i)
			.click();
		await expect(reviewMain(page).getByText(durableReview.reviewer_user_id, { exact: true })).toBeVisible();
	});

	test("explains the missing snapshot prerequisite and links to the orchestrator generation destination", async ({ page }) => {
		await openUseCaseReview(page, { initialProject: useCaseProjectFixture({ snapshot: null }) });

		const main = reviewMain(page);
		await expect(main.getByRole("heading", { name: /^No Use Cases snapshot$/i })).toBeVisible();
		await expect(main).toContainText(/approved requirements.*generate|generate.*approved requirements/i);
		await expect(main.getByRole("link", { name: /Open Test Cases|Generate First Test Suite/i })).toHaveAttribute(
			"href",
			buildProjectPath(USE_CASE_PROJECT_ID, "test-cases")
		);
		await expect(decisionPanel(page)).toHaveCount(0);
	});

	for (const requirementState of [
		{ label: "unapproved", mutate: (state) => (state.approved = false) },
		{ label: "stale", mutate: (state) => (state.stale = true) },
	]) {
		test(`routes a missing Use Cases snapshot with ${requirementState.label} requirements back to Requirements`, async ({ page }) => {
			const project = useCaseProjectFixture({ snapshot: null });
			requirementState.mutate(project.stage_state.requirements);
			await openUseCaseReview(page, { initialProject: project });

			await expect(reviewMain(page)).toContainText(/review and approve the current project requirements/i);
			await expect(reviewMain(page).getByRole("link", { name: /^Open Requirements$/i })).toHaveAttribute(
				"href",
				buildProjectPath(USE_CASE_PROJECT_ID, "requirements")
			);
		});
	}

	test("supports keyboard search and decision order and exposes completion through a live status", async ({ page }) => {
		await openUseCaseReview(page);
		const main = reviewMain(page);
		const search = main.getByRole("searchbox", { name: /^Search use cases$/i });

		for (const shortcut of ["Control+K", "Meta+K"]) {
			await page.getByRole("heading", { name: /^Use Cases$/i, level: 1 }).click();
			await page.keyboard.press(shortcut);
			await expect(search).toBeFocused();
		}

		await approveOption(page).focus();
		await page.keyboard.press("ArrowDown");
		await expect(requestChangesOption(page)).toBeFocused();
		await expect(requestChangesOption(page)).toBeChecked();
		await page.keyboard.press("Tab");
		await expect(reviewComment(page)).toBeFocused();
		await reviewComment(page).fill("Keyboard review is ready.");
		await page.keyboard.press("Control+K");
		await expect(reviewComment(page)).toBeFocused();
		await page.keyboard.press("Tab");
		await expect(requestChangesButton(page)).toBeFocused();
		await page.keyboard.press("Enter");
		await expect(reviewAnnouncement(page)).toContainText(/changes requested/i);
	});

	test("reflows by available workbench width without cramped columns or mobile overflow", async ({ page }) => {
		await page.setViewportSize({ width: 320, height: 900 });
		await openUseCaseReview(page);
		const collection = reviewMain(page).getByRole("region", { name: /^Use case scenarios$/i });
		for (const width of [320, 390, 640, 760, 900, 1280, 1440, 1920]) {
			await page.setViewportSize({ width, height: width === 390 ? 844 : width === 1920 ? 1080 : 900 });
			await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
			if (width <= 390) {
				const searchBox = await reviewMain(page)
					.getByRole("searchbox", { name: /^Search use cases$/i })
					.boundingBox();
				expect(searchBox.height).toBeGreaterThanOrEqual(40);
			}
			if (width >= 1440) {
				const [collectionBox, decisionBox] = await Promise.all([collection.boundingBox(), decisionPanel(page).boundingBox()]);
				expect(decisionBox.x).toBeGreaterThan(collectionBox.x + collectionBox.width - 4);
				expect(Math.abs(collectionBox.y - decisionBox.y)).toBeLessThanOrEqual(4);
			}
		}
	});

	test("prevents double submission while a review decision is pending", async ({ page }) => {
		const deferred = createDeferred();
		const api = await openUseCaseReview(page, { reviewScenarios: [{ gate: deferred.promise }] });
		await reviewComment(page).fill("Submit this approval once.");

		await approveButton(page).evaluate((button) => {
			button.click();
			button.click();
		});
		await expect.poll(() => api.requests.review.length).toBe(1);
		await expect(decisionPanel(page)).toHaveAttribute("aria-busy", "true");
		await expect(approveOption(page)).toBeDisabled();
		await expect(requestChangesOption(page)).toBeDisabled();
		await expect(reviewComment(page)).toBeDisabled();
		await expect(decisionPanel(page).getByRole("button", { name: /^Saving decision/i })).toBeDisabled();

		deferred.resolve();
		await expect(reviewAnnouncement(page)).toContainText(/Use Cases approved/i);
		expect(api.requests.review).toHaveLength(1);
	});

	test("ignores a late review completion after the user returns Home", async ({ page }) => {
		const deferred = createDeferred();
		const api = await openUseCaseReview(page, { reviewScenarios: [{ gate: deferred.promise }] });
		await reviewComment(page).fill("Do not reopen this project after I leave.");
		await approveButton(page).click();
		await expect.poll(() => api.requests.review.length).toBe(1);

		await page
			.getByRole("navigation", { name: "Global navigation" })
			.getByRole("link", { name: /^Home$/i })
			.click();
		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i, level: 1 })).toBeVisible();
		deferred.resolve();

		await expect.poll(() => api.getProject().stage_state.use_cases.metadata.latest_human_review?.decision).toBe("approve");
		await expect(page).toHaveURL(/\/$/);
		await expect(page.getByRole("heading", { name: /^Home$/i, level: 1 })).toBeVisible();
		await expect(
			page
				.getByRole("main")
				.getByRole("status")
				.filter({ hasText: /Use Cases approved/i })
		).toHaveCount(0);
		await expect(page.getByRole("navigation", { name: "Project navigation" })).toHaveCount(0);
	});
});
