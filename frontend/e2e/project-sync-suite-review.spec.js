import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, installUseCaseReviewApi, useCaseProjectFixture, useCaseSnapshotFixture } from "./support/use-case-review.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const planId = "current-sync-scenario-draft";
const draftId = "current-sync-suite-draft";
const assessedId = "independently-assessed-sync-suite";
const changeId = "saved-checkout-context-correction";
const reviewPath = `${root}/test-cases/review`;
const decisionPath = `${root}/use-cases/reviews`;
const syncReview = (page) => page.getByRole("region", { name: "Project Sync suite review", exact: true });
const reviewAction = (page) => page.getByRole("button", { name: "Review saved suite", exact: true });

async function setup(page, { state = "pending" } = {}) {
	const project = useCaseProjectFixture({
		reviewState: state === "pending" ? "pending" : "approved",
		snapshot: useCaseSnapshotFixture({
			snapshot_id: planId,
			operation: "project.sync.draft",
			source_snapshot_id: "snapshot-requirements-v2",
		}),
	});
	const cases = [
		{
			id: "TC-SYNC",
			title: "Confirm one express checkout order",
			artifact_version_id: "express-checkout-version-2",
			linked_requirement_ids: ["REQ-101"],
			scenario_refs: ["REQ-101-SCN-01"],
			steps: [{ step: 1, action: "Submit express checkout", expected: "One order is confirmed.", test_data: "  β-01  " }],
		},
		{
			id: "TC-UNCHANGED",
			title: "Check supervisor approval",
			artifact_version_id: "unchanged-supervisor-version-1",
			linked_requirement_ids: ["REQ-202"],
			scenario_refs: ["REQ-202-SCN-01"],
			steps: [{ step: 1, action: "Submit the approval", expected: "The approval is recorded." }],
		},
	];
	const draft = {
		snapshot_id: draftId,
		project_id: project.project_id,
		stage: "test_cases",
		version: 2,
		project_revision: project.current_revision,
		operation: "project.sync.draft",
		source_snapshot_id: planId,
		approved: false,
		metadata: { change_id: changeId, ...(state === "superseded" ? { review_retry_available: true } : {}) },
		payload: {
			coverage_plan: project.current_snapshots.use_cases.payload.coverage_plan,
			test_cases: cases,
			approved: false,
			substantive_assessment: { status: "unknown" },
		},
	};
	function saveSuite(target, suite) {
		target.current_snapshots.test_cases = suite;
		target.stage_state.test_cases = {
			current_snapshot_id: suite.snapshot_id,
			operation: suite.operation,
			version: suite.version,
			approved: false,
			stale: state === "superseded",
			source_snapshot_id: suite.source_snapshot_id,
			metadata: suite.metadata,
		};
	}
	saveSuite(project, draft);
	project.project_sync = {
		id: changeId,
		status: state === "superseded" ? "superseded" : "ready_for_review",
		stages: { requirements: "preserved", context: "draft", use_cases: "draft", test_cases: "draft", automation: "not_requested" },
	};

	const writes = [];
	const errors = [];
	const reviewRequests = [];
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional read." } });
	});
	const api = await installUseCaseReviewApi(page, {
		initialProject: project,
		reviewScenarios: [
			{
				serverProject: (saved, payload) => {
					if (payload.decision === "approve") saved.current_snapshots.test_cases.metadata.review_retry_available = true;
					return saved;
				},
			},
		],
	});
	await page.route(new RegExp(`${root}/approval-continuations(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, tests_enabled: true, saved_review_enabled: true, runs: [] } })
	);
	await page.route(`**${root}/changes/admission`, (route) => route.fulfill({ json: { enabled: true, blocked: false, attempt: null } }));
	await page.route(new RegExp(`${root}/test-cases/repair(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, blocked: false, attempt: null, target: null } })
	);
	await page.route(`**${reviewPath}`, (route) => {
		const request = route.request();
		const saved = api.getProject();
		expect(saved.stage_state.use_cases.metadata.latest_human_review).toMatchObject({ snapshot_id: planId, decision: "approve" });
		reviewRequests.push({ payload: request.postDataJSON(), id: request.headers()["x-request-id"], revision: saved.current_revision });
		saved.current_revision++;
		saveSuite(saved, {
			...saved.current_snapshots.test_cases,
			snapshot_id: assessedId,
			source_snapshot_id: draftId,
			version: 3,
			project_revision: saved.current_revision,
			operation: "test_cases.review",
			metadata: { sync_review_binding: { change_id: changeId, test_cases_snapshot_id: draftId, use_cases_snapshot_id: planId } },
			payload: {
				...saved.current_snapshots.test_cases.payload,
				review: { approved: false, summary: "The saved design is complete and browser setup still needs evidence." },
				substantive_assessment: { status: "passed", design_status: "assessed_complete", execution_status: "blocked" },
			},
		});
		saved.suite_quality = {
			suite_snapshot_id: assessedId,
			source_freshness: "current",
			design_status: "complete",
			scenario_decision: "approved",
			scenario_snapshot_id: planId,
			scenario_review_id: saved.stage_state.use_cases.metadata.latest_human_review.review_id,
			human_acceptance: "unavailable",
			declared_setup: "blocked",
		};
		saved.design_delivery_enabled = true;
		saved.reviewed_design_available = true;
		api.setProject(saved);
		return route.fulfill({ json: saved });
	});
	await seedAuthenticatedSession(page);
	return { api, cases, errors, reviewRequests, writes };
}

test.describe("Independent Project Sync suite review", () => {
	test.skip(
		process.env.E2E_PROJECT_WORKSPACES_V2 !== "true" || process.env.E2E_APPROVAL_CONTINUATION_ENABLED !== "true",
		"Requires both opt-in project workspace flags"
	);

	test("waits for explicit scenario approval and assesses only the exact saved cases", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { api, cases, errors, reviewRequests, writes } = await setup(page);
		await page.goto(`${root}/review/tests`);
		await expect(syncReview(page)).toContainText("Sync suite awaits scenario approval");
		await expect(reviewAction(page)).toHaveCount(0);
		await page.screenshot({ path: testInfo.outputPath("sync-suite-awaits-scenarios.png") });
		await syncReview(page).getByRole("link", { name: "Review matching scenarios", exact: true }).press("Enter");
		await expect(page).toHaveURL(`${root}/review/scenarios`);
		await page.getByRole("button", { name: "Approve all", exact: true }).click();
		const decision = page.getByRole("form", { name: "Human review decision", exact: true });
		await expect(decision.getByRole("button", { name: "Approve scenarios and generate tests", exact: true })).toHaveCount(0);
		await decision.getByRole("button", { name: "Approve Use Cases", exact: true }).click();
		await expect(page.getByRole("status", { name: "Current human review status" })).toHaveText("Human approved");
		expect(api.requests.review.at(-1).payload).toMatchObject({ snapshot_id: planId, decision: "approve" });
		expect(writes).toEqual([`POST ${decisionPath}`]);

		await page.goto(`${root}/review/tests`);
		await expect(syncReview(page)).toContainText("Sync suite ready for independent review");
		await expect(reviewAction(page)).toBeEnabled();
		await reviewAction(page).click();
		const assessment = page.getByRole("status").filter({ hasText: "Saved Sync suite review recorded." });
		await expect(assessment).toContainText("The saved design is complete and browser setup still needs evidence.");
		const quality = page.getByRole("region", { name: "Saved suite quality", exact: true });
		await quality.getByText("Saved suite quality", { exact: true }).click();
		await expect(quality.getByRole("term").filter({ hasText: "Independent design" }).locator("..").getByRole("definition")).toHaveText(
			"Complete"
		);
		await expect(quality.getByRole("term").filter({ hasText: "Declared setup" }).locator("..").getByRole("definition")).toHaveText(
			"Blocked"
		);
		await expect(quality).toContainText("Reviewed design ready");
		await expect(page.getByText("Project update assessed", { exact: true })).toBeVisible();
		await expectNoDocumentOverflow(page);
		await page.screenshot({ path: testInfo.outputPath("sync-suite-assessed.png") });
		expect(reviewRequests).toHaveLength(1);
		expect(reviewRequests[0]).toMatchObject({
			id: expect.any(String),
			payload: { snapshot_id: draftId, base_project_revision: reviewRequests[0].revision },
		});
		expect(api.getProject().current_snapshots.test_cases.payload.test_cases).toEqual(cases);
		expect(writes).toEqual([`POST ${decisionPath}`, `POST ${reviewPath}`]);
		expect(errors).toEqual([]);
	});

	for (const [state, title] of [
		["unverified", "Sync draft saved"],
		["superseded", "Earlier Sync draft"],
	]) {
		test(`keeps the ${state} draft available for inspection without dispatch`, async ({ page }) => {
			const { errors, writes } = await setup(page, { state });
			await page.goto(`${root}/review/tests`);
			await expect(syncReview(page)).toContainText(title);
			await expect(syncReview(page).getByRole("button", { name: "View saved cases", exact: true })).toBeEnabled();
			await expect(reviewAction(page)).toHaveCount(0);
			expect(writes).toEqual([]);
			expect(errors).toEqual([]);
		});
	}
});
