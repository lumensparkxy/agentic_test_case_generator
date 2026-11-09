import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, installUseCaseReviewApi, useCaseProjectFixture } from "./support/use-case-review.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const suiteId = "booking-findings-suite-v4";
const caseId = "TC-BOOK-06";
const requirementId = "REQ-101";
const caseTitle = "Book for six attendees";
const boundaryQuote = "Bookings for up to six attendees receive a confirmation.";
const savedCountQuote = "The confirmation shows the saved attendee count.";
const receiptQuote = "The booking history keeps a receipt number.";
const boundaryBehavior = "Observe confirmation at the allowed boundary";
const structural = "TC-BOOK-06 needs a complete precondition.";
const general = "Observe the result after the booking is saved.";
const setupName = "supervisor_fixture";
const findings = (page) => page.getByRole("region", { name: "Business coverage assessment", exact: true });

const fixtureCase = {
	id: caseId,
	title: caseTitle,
	description: "Confirm the saved upper boundary.",
	artifact_set_id: "booking-fixture",
	artifact_item_id: "booking-capacity-case",
	artifact_version_id: "booking-case-version-4",
	artifact_version_number: 4,
	linked_requirement_ids: [requirementId],
	scenario_refs: ["REQ-101-SCN-01"],
	priority: "High",
	steps: [
		{ step: 1, action: "Submit the form with six attendees.", expected: "The booking confirmation is visible.", test_data: "  6  " },
		{ step: 2, action: "Read the attendee count.", expected: "The confirmation displays six attendees." },
	],
};

async function setup(page, { stale = false } = {}) {
	const project = useCaseProjectFixture({ reviewState: "approved", name: "Booking findings" });
	const source = project.current_snapshots.requirements;
	source.payload.requirements = [
		{ id: requirementId, text: boundaryQuote, source_excerpt: savedCountQuote, review_status: "Approved" },
		{ id: "REQ-202", text: receiptQuote, review_status: "Approved" },
	];
	const suite = {
		snapshot_id: suiteId,
		project_id: project.project_id,
		stage: "test_cases",
		version: 4,
		project_revision: project.current_revision,
		operation: "test_cases.review",
		source_snapshot_id: project.current_snapshots.use_cases.snapshot_id,
		metadata: { source_snapshot_ids: { requirements: source.snapshot_id, use_cases: project.current_snapshots.use_cases.snapshot_id } },
		payload: {
			test_cases: [fixtureCase],
			review: { approved: false, score: 75, threshold: 90, blocking_issues: [structural], unmet_criteria: [structural, general] },
			substantive_assessment: {
				status: "incomplete",
				design_status: "incomplete",
				execution_status: "blocked",
				execution_blockers: [{ test_case_id: caseId, prerequisite_name: setupName }],
				reason: "The saved case omits business checks and still needs browser setup.",
				obligations: [
					{
						requirement_id: requirementId,
						obligation: boundaryBehavior,
						source_quote: boundaryQuote,
						status: "covered",
						reason: "The saved confirmation is asserted.",
						evidence: [{ test_case_id: caseId, ...fixtureCase.steps[0] }],
					},
					{
						requirement_id: requirementId,
						obligation: boundaryBehavior,
						source_quote: savedCountQuote,
						status: "unmet",
						reason: "Verify the persisted count after reopening.",
						evidence: [
							{ test_case_id: caseId, ...fixtureCase.steps[1] },
							{ test_case_id: caseId, step: 4, action: "Inspect the old list.", expected: "An earlier evidence row remains visible." },
						],
					},
					{
						requirement_id: "REQ-202",
						obligation: "Observe the receipt in booking history",
						source_quote: receiptQuote,
						status: "unknown",
						reason: "No verified saved receipt step was found.",
						evidence: [],
					},
				],
				case_grounding: [{ test_case_id: caseId, reason: "The source identifies the allowed boundary.", prerequisites: [] }],
			},
		},
	};
	project.current_snapshots.test_cases = suite;
	project.design_delivery_enabled = true;
	project.reviewed_design_available = false;
	project.suite_quality = {
		suite_snapshot_id: suiteId,
		source_freshness: stale ? "stale" : "current",
		design_status: "incomplete",
		scenario_decision: "approved",
		human_acceptance: "unavailable",
		declared_setup: "blocked",
	};
	project.stage_state.test_cases = {
		current_snapshot_id: suiteId,
		version: suite.version,
		approved: false,
		stale,
		metadata: suite.metadata,
	};
	const writes = [];
	const evidenceReads = [];
	const errors = [];
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
	await installUseCaseReviewApi(page, { initialProject: project });
	await page.route(new RegExp(`${root}/approval-continuations(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, tests_enabled: true, saved_review_enabled: true, runs: [] } })
	);
	await page.route(new RegExp(`${root}/test-cases/repair(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, blocked: false, attempt: null, target: null } })
	);
	await page.route(`**${root}/quality-findings`, (route) =>
		route.fulfill({ json: { findings: [], project_revision: project.current_revision } })
	);
	await page.route(new RegExp(`${root}/test-case-evidence(?:\\?.*)?$`), (route) => {
		const { searchParams } = new URL(route.request().url());
		evidenceReads.push([searchParams.get("snapshot_id"), searchParams.get("case_id")]);
		if (searchParams.get("snapshot_id") !== suiteId || searchParams.get("case_id") !== caseId) return route.fulfill({ status: 404 });
		return route.fulfill({
			json: {
				project_id: project.project_id,
				snapshot_id: suiteId,
				suite_version: suite.version,
				project_revision: suite.project_revision,
				historical: false,
				stale,
				created_at: "2026-07-17T09:35:00Z",
				test_case: fixtureCase,
			},
		});
	});
	await seedAuthenticatedSession(page);
	return { errors, writes, evidenceReads };
}

test.describe("Grouped saved review findings", () => {
	test.skip(process.env.E2E_PROJECT_WORKSPACES_V2 !== "true", "Requires the opt-in Review workspace");

	test("keeps every business finding and exact step visible without mixing setup or structural blockers", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const api = await setup(page);
		await page.goto(`${root}/review/tests`);
		const improve = page.getByRole("tab", { name: /^Improve tests/ });
		await improve.click();
		await expect(findings(page)).toContainText("Business coverage: incomplete");
		const disclosure = findings(page).getByText("Source obligations and prerequisites", { exact: true });
		await disclosure.press("Enter");
		for (const value of [
			boundaryQuote,
			savedCountQuote,
			receiptQuote,
			"Observe confirmation at the allowed boundary",
			"Verify the persisted count after reopening",
			"Observe the receipt in booking history",
			...fixtureCase.steps.map((step) => step.expected),
		]) {
			await expect(findings(page)).toContainText(value);
		}
		const behavior = findings(page).getByRole("heading", { name: requirementId, exact: true });
		await expect(behavior).toBeVisible();
		const boundary = findings(page).getByRole("region", { name: boundaryBehavior, exact: true });
		await expect(boundary).toContainText(boundaryQuote);
		await expect(boundary).toContainText(savedCountQuote);
		await expect(findings(page).getByText("Covered", { exact: true })).toBeVisible();
		await expect(findings(page).getByText("Unmet", { exact: true })).toBeVisible();
		await expect(findings(page).getByText("Unverified", { exact: true })).toBeVisible();
		await expect(findings(page)).toContainText("An earlier evidence row remains visible.");
		await expect(findings(page).getByText("Exact saved step unavailable.", { exact: true })).toBeVisible();
		const original = findings(page).getByRole("link", { name: `Open saved case ${caseId}, step 2`, exact: true });
		await expect(original).toHaveAttribute("href", new RegExp(`[?&]suite=${suiteId}&case=${caseId}#saved-case-step-2$`));
		await expect(findings(page).getByRole("link", { name: `Open saved case ${caseId}, step 4`, exact: true })).toHaveCount(0);
		const improvePanel = page.getByRole("region", { name: "Improve test quality", exact: true });
		await expect(improvePanel.getByText("Structural blockers", { exact: true })).toBeVisible();
		await expect(improvePanel.getByText("Other review criteria", { exact: true })).toBeVisible();
		await expect(page.getByRole("region", { name: "Execution setup", exact: true })).toContainText(setupName);
		await expect(findings(page)).not.toContainText(setupName);
		await expect(findings(page)).not.toContainText(structural);
		await expectNoDocumentOverflow(page);
		const accessibility = await new AxeBuilder({ page }).include(".improve-tests").analyze();
		expect(accessibility.violations).toEqual([]);
		await page.screenshot({ path: testInfo.outputPath("grouped-booking-review.png"), fullPage: true });
		await original.press("Enter");
		await expect(page).toHaveURL(new RegExp(`[?&]suite=${suiteId}&case=${caseId}#saved-case-step-2$`));
		await expect(page.getByRole("heading", { name: caseTitle, exact: true })).toBeVisible();
		await expect(page.getByRole("row").filter({ hasText: "The confirmation displays six attendees." })).toBeFocused();
		expect(api.evidenceReads).toEqual([[suiteId, caseId]]);
		await page.goBack();
		await expect(page).toHaveURL(`${root}/review/tests`);
		await expect(findings(page).getByRole("region", { name: boundaryBehavior, exact: true })).toBeVisible();
		expect(api.writes).toEqual([]);
		expect(api.errors).toEqual([]);
	});
});
