import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { USE_CASE_PROJECT_ID, useCaseOrchestratorFixture, useCaseProjectFixture } from "./support/use-case-review.js";
import { createDeferred, installWorkspaceApi } from "./support/workspace.js";

const enabled = process.env.E2E_PROJECT_WORKSPACES_V2 === "true" && process.env.E2E_APPROVAL_CONTINUATION_ENABLED === "true";
const root = `/projects/${USE_CASE_PROJECT_ID}`;
const repairPath = `${root}/test-cases/repair`;
const beforeId = "saved-checkout-before-repair";
const afterId = "saved-checkout-after-repair";
const caseId = "TC-ORDER";
const beforeVersion = "order-case-version-1";
const afterVersion = "order-case-version-2";
const requirementId = "REQ-101";
const literal = "  β-01  ";
const priorAssertion = "The confirmation appears for the new order.";
const addedAssertion = "The confirmation shows the ID of the new order.";
const sourceQuote = "The checkout confirmation shows the identifier of the new order.";

const target = {
	target_id: `repair_${"a".repeat(64)}`,
	case_id: caseId,
	case_version_id: beforeVersion,
	requirement_id: requirementId,
	scenario_id: "REQ-101-SCN-01",
	obligation: "Show the confirmed order identifier",
	source_quote: sourceQuote,
	reason: "The existing test checks the confirmation and omits the identifier.",
	step_numbers: [1],
};

async function setup(page, { loseReply = false, interrupted = false, holdPost = false } = {}) {
	const project = useCaseProjectFixture({ reviewState: "approved", name: "Checkout repair" });
	project.current_snapshots.requirements.payload.requirements[0].text = sourceQuote;
	const plan = project.current_snapshots.use_cases;
	const original = {
		id: caseId,
		title: "Verify the order confirmation",
		linked_requirement_ids: [requirementId],
		scenario_refs: [target.scenario_id],
		artifact_set_id: "checkout-cases",
		artifact_item_id: "order-case",
		artifact_version_id: beforeVersion,
		artifact_version_number: 1,
		steps: [{ step: 1, action: "Submit the fixture order", expected: priorAssertion, test_data: literal }],
	};
	const sibling = {
		id: "TC-SUPERVISOR",
		title: "Verify the supervisor decision",
		linked_requirement_ids: ["REQ-202"],
		scenario_refs: ["REQ-202-SCN-01"],
		artifact_set_id: "checkout-cases",
		artifact_item_id: "supervisor-case",
		artifact_version_id: "supervisor-case-version-1",
		artifact_version_number: 1,
		steps: [{ step: 1, action: "Submit the approval", expected: "The supervisor approval is recorded." }],
	};
	const revised = {
		...original,
		artifact_version_id: afterVersion,
		artifact_version_number: 2,
		steps: [...original.steps, { step: 2, action: "Read the ID on the confirmation", expected: addedAssertion }],
	};
	const makeSuite = ({ snapshotId, operation, version, cases, review }) => ({
		snapshot_id: snapshotId,
		project_id: project.project_id,
		stage: "test_cases",
		version,
		project_revision: project.current_revision,
		operation,
		source_snapshot_id: plan.snapshot_id,
		approved: false,
		metadata: {
			source_snapshot_ids: { requirements: project.current_snapshots.requirements.snapshot_id, use_cases: plan.snapshot_id },
			...(version === 2 ? { source_test_cases_snapshot_id: beforeId } : {}),
		},
		payload: {
			test_cases: cases,
			coverage_plan: plan.payload.coverage_plan,
			approved: false,
			review: { approved: false, score: 100, threshold: 90, blocking_issues: [review] },
			substantive_assessment: {
				status: version === 2 ? "unknown" : "incomplete",
				reason: review,
				obligations:
					version === 2
						? []
						: [
								{
									obligation: target.obligation,
									requirement_id: requirementId,
									source_quote: sourceQuote,
									status: "unmet",
									reason: target.reason,
									evidence: [{ test_case_id: caseId, ...original.steps[0] }],
								},
							],
			},
			generation_tasks: [],
			...(version === 2 ? { scoped_refinement: { changed: 1, preserved: 1, added: 0, changed_ids: [caseId] } } : {}),
		},
	});
	const saveSuite = (suite) => {
		project.current_snapshots.test_cases = suite;
		project.stage_state.test_cases = {
			current_snapshot_id: suite.snapshot_id,
			approved: false,
			stale: false,
			operation: suite.operation,
			metadata: suite.metadata,
			version: suite.version,
		};
	};
	const before = makeSuite({
		snapshotId: beforeId,
		operation: "testcases.generate",
		version: 1,
		cases: [original, sibling],
		review: target.reason,
	});
	saveSuite(before);
	if (interrupted) project.stage_state.test_cases.stale = true;

	const errors = [];
	const writes = [];
	const repairs = [];
	const statusReads = [];
	const evidenceReads = [];
	const releaseSignal = createDeferred();
	let result = null;
	let firstReceiptUnavailable = loseReply;
	const operationId = `draft_${"b".repeat(64)}`;
	const makeAttempt = (requestId) => ({
		operation_id: operationId,
		fence: 9,
		request_id: requestId,
		state: interrupted ? "interrupted" : "running",
		reason: interrupted ? "provider_outcome_unconfirmed" : null,
		source_snapshot_id: interrupted ? "earlier-checkout-suite" : beforeId,
		case_id: caseId,
	});
	let attempt = interrupted ? makeAttempt("interrupted-repair-receipt") : null;
	const releasePath = interrupted || holdPost ? `${repairPath}/${operationId}/release` : null;
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	await page.route("**/*", (route) => {
		const request = route.request();
		if (!["fetch", "xhr"].includes(request.resourceType())) return route.fallback();
		const path = new URL(request.url()).pathname;
		if (path.endsWith("/knowledge")) return route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } });
		if (path.endsWith("/quality-findings")) {
			return route.fulfill({ json: { findings: [], project_revision: project.current_revision } });
		}
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional read." } });
	});
	await installWorkspaceApi(page, { projectDetails: { [project.project_id]: project } });
	await page.route(`**${root}/orchestrator/status`, (route) => route.fulfill({ json: useCaseOrchestratorFixture(project) }));
	await page.route(new RegExp(`${root}/approval-continuations(?:\\?.*)?$`), (route) =>
		route.fulfill({ json: { enabled: true, tests_enabled: true, runs: [], saved_review_enabled: true } })
	);
	if (releasePath) {
		await page.route(`**${releasePath}`, (route) => {
			expect(route.request().method()).toBe("POST");
			expect(route.request().postDataJSON()).toEqual({ fence: attempt.fence });
			attempt = null;
			releaseSignal.resolve();
			return route.fulfill({ json: { released: true } });
		});
	}
	await page.route(new RegExp(`${repairPath}(?:\\?.*)?$`), async (route) => {
		const request = route.request();
		if (request.method() === "GET") {
			const query = Object.fromEntries(new URL(request.url()).searchParams);
			statusReads.push(query);
			const matchesCase = query.snapshot_id === beforeId && query.case_id === caseId;
			const requestedResult = result && result.request_id === query.request_id && (matchesCase || !query.snapshot_id) ? result : null;
			if (requestedResult && firstReceiptUnavailable) {
				firstReceiptUnavailable = false;
				return route.fulfill({ status: 503, json: { detail: "Saved repair status is temporarily unavailable." } });
			}
			return route.fulfill({
				json: {
					enabled: true,
					blocked: Boolean(attempt),
					targets: !interrupted && !result && matchesCase ? [target] : [],
					attempt,
					result: requestedResult,
				},
			});
		}
		expect(request.method()).toBe("POST");
		const input = request.postDataJSON();
		const requestId = request.headers()["x-request-id"];
		expect(input).toEqual({
			snapshot_id: beforeId,
			case_id: caseId,
			case_version_id: beforeVersion,
			target_id: target.target_id,
			base_project_revision: project.current_revision,
		});
		expect(requestId).toBeTruthy();
		repairs.push({ input, requestId });
		if (holdPost) {
			attempt = makeAttempt(requestId);
			await releaseSignal.promise;
			return route.fulfill({ status: 409, json: { detail: { code: "project_draft_claim_lost", message: "The repair was released." } } });
		}
		project.current_revision++;
		saveSuite(
			makeSuite({
				snapshotId: afterId,
				operation: "testcases.repair",
				version: 2,
				cases: [revised, sibling],
				review: "Review the saved suite with its new check.",
			})
		);
		result = {
			request_id: requestId,
			source_snapshot_id: beforeId,
			result_snapshot_id: afterId,
			project_revision: project.current_revision,
			case_id: caseId,
			before_case_version_id: beforeVersion,
			after_case_version_id: afterVersion,
			preserved_case_count: 1,
		};
		return loseReply ? route.abort("failed") : route.fulfill({ json: result });
	});
	page.on("close", () => releaseSignal.resolve());
	await page.route(`**${root}/test-case-evidence?*`, (route) => {
		const query = Object.fromEntries(new URL(route.request().url()).searchParams);
		evidenceReads.push(query);
		expect(query.case_id).toBe(caseId);
		expect([beforeId, afterId]).toContain(query.snapshot_id);
		const historical = query.snapshot_id === beforeId;
		return route.fulfill({
			json: {
				project_id: project.project_id,
				snapshot_id: query.snapshot_id,
				suite_version: historical ? 1 : 2,
				project_revision: historical ? before.project_revision : project.current_revision,
				historical,
				stale: false,
				test_case: historical ? original : revised,
				requirements: project.current_snapshots.requirements.payload.requirements,
				obligations: [],
			},
		});
	});
	await seedAuthenticatedSession(page);
	return {
		errors,
		writes,
		repairs,
		statusReads,
		evidenceReads,
		releasePath,
		savedCases: () => project.current_snapshots.test_cases.payload.test_cases,
	};
}

test.describe("Exact selected case repair", () => {
	test.skip(!enabled, "Requires the opt-in workspace and continuation");

	test("opens a hidden finding for scoped repair and restores the filtered case selection", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const api = await setup(page);
		const originalCases = api.savedCases();
		const testsPath = `${root}/review/tests`;
		await page.goto(testsPath);
		await page.getByRole("tab", { name: /^Generated Test Cases/ }).press("Enter");
		const search = page.getByRole("searchbox", { name: "Search test cases", exact: true });
		await search.fill("supervisor");
		const sibling = page.getByRole("button", { name: "TC-SUPERVISOR Verify the supervisor decision", exact: true });
		await sibling.press("Enter");
		const reader = page.getByRole("region", { name: "Selected test case", exact: true });
		await expect(reader).toContainText(originalCases[1].steps[0].expected);
		const sections = page.getByRole("navigation", { name: "Workspace sections", exact: true });
		const findingsEntry = sections.getByRole("link", { name: "Test findings", exact: true });
		const openFindingCase = async () => {
			await findingsEntry.press("Enter");
			const coverage = page.getByRole("region", { name: "Business coverage assessment", exact: true });
			const disclosure = coverage.getByText("Source obligations and prerequisites", { exact: true });
			if (!(await disclosure.evaluate((summary) => summary.parentElement.open))) await disclosure.press("Enter");
			await coverage.getByRole("button", { name: `Open ${caseId} in Review`, exact: true }).press("Enter");
		};
		await sections.getByRole("link", { name: "Scenarios", exact: true }).press("Enter");
		await openFindingCase();
		await expect(page).toHaveURL(testsPath);
		await expect(search).toHaveValue("supervisor");
		await expect(reader).toContainText("This case is outside the current filters.");
		await expect(reader).toContainText(priorAssertion);
		const repair = reader.getByRole("region", { name: "Selected test repair", exact: true });
		const addCheck = repair.getByRole("button", { name: "Add missing check", exact: true });
		await expect(addCheck).toBeEnabled();
		await expect(repair).toContainText(sourceQuote);
		await expect(reader.getByRole("heading", { name: originalCases[0].title, exact: true })).toBeFocused({ timeout: 5000 });
		expect(api.statusReads).toContainEqual({ snapshot_id: beforeId, case_id: caseId });
		expect(api.writes).toEqual([]);
		await page.screenshot({ path: testInfo.outputPath("hidden-exact-repair-target.png") });

		await reader.getByRole("button", { name: "Return to filtered tests", exact: true }).press("Enter");
		await expect(search).toHaveValue("supervisor");
		await expect(sibling).toHaveAttribute("aria-pressed", "true");
		await expect(reader.getByRole("heading", { name: originalCases[1].title, exact: true })).toBeFocused();
		expect(api.writes).toEqual([]);
		await page.screenshot({ path: testInfo.outputPath("restored-filtered-repair-reader.png") });

		await openFindingCase();
		await addCheck.press("Enter");
		const comparison = page.getByRole("region", { name: "Saved repair evidence", exact: true });
		await comparison.getByText("Compare saved steps", { exact: true }).press("Enter");
		await expect(comparison).toContainText(priorAssertion);
		await expect(comparison).toContainText(addedAssertion);
		await expect(comparison).toContainText(/1 .*preserved/i);
		expect(api.repairs).toHaveLength(1);
		expect(api.savedCases()[0].steps[0]).toEqual(originalCases[0].steps[0]);
		expect(api.savedCases()[1]).toEqual(originalCases[1]);
		expect(api.writes).toEqual([`POST ${repairPath}`]);
		expect(api.errors).toEqual([]);
	});

	test("explicitly releases an interrupted repair on an older suite", async ({ page }) => {
		const api = await setup(page, { interrupted: true });
		await page.goto(`${root}/review/tests`);
		const status = page.getByRole("region", { name: "Selected repair status", exact: true });
		const release = status.getByRole("button", { name: "Acknowledge interrupted repair", exact: true });
		await expect(status).toContainText("This attempt uses an earlier saved suite.");
		await expect(release).toBeEnabled();
		await expect(page.getByRole("button", { name: "Add missing check", exact: true })).toHaveCount(0);
		expect(api.writes).toEqual([]);
		await release.click();
		await expect(release).toHaveCount(0);
		expect(api.repairs).toHaveLength(0);
		expect(api.writes).toEqual([`POST ${api.releasePath}`]);
		expect(api.errors).toEqual([]);
	});

	test("can cancel the active repair while the original response is pending", async ({ page }) => {
		const api = await setup(page, { holdPost: true });
		await page.goto(`${root}/review/tests`);
		await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
		await page.getByRole("button", { name: "Add missing check", exact: true }).click();
		await expect.poll(() => api.repairs.length).toBe(1);
		const status = page.getByRole("region", { name: "Selected repair status", exact: true });
		await status.getByRole("button", { name: "Check repair status", exact: true }).click();
		const cancel = status.getByRole("button", { name: "Cancel selected repair", exact: true });
		await expect(cancel).toBeEnabled();
		await cancel.click();
		await expect(cancel).toHaveCount(0);
		await expect(page.getByRole("region", { name: "Selected test case", exact: true })).toContainText(priorAssertion);
		await expect(page.getByRole("region", { name: "Saved repair evidence", exact: true })).toHaveCount(0);
		expect(api.evidenceReads).toEqual([]);
		expect(api.writes).toEqual([`POST ${repairPath}`, `POST ${api.releasePath}`]);
		expect(api.errors).toEqual([]);
	});

	for (const loseReply of [false, true]) {
		test(`keeps both saved case versions after ${loseReply ? "a lost repair acknowledgement" : "an exact repair"}`, async ({
			page,
		}, testInfo) => {
			const api = await setup(page, { loseReply });
			await page.setViewportSize({ width: 1440, height: 900 });
			await page.goto(`${root}/review/tests`);
			await page.getByRole("tab", { name: /Generated Test Cases/ }).click();
			const selected = page.getByRole("region", { name: "Selected test case", exact: true });
			await expect(selected.getByRole("heading", { name: "Verify the order confirmation" })).toBeVisible();
			await expect(selected).toContainText(priorAssertion);

			const repair = page.getByRole("region", { name: "Selected test repair", exact: true });
			await expect(repair).toContainText(sourceQuote);
			await expect(repair).toContainText(target.obligation);
			expect(api.writes).toEqual([]);
			await repair.getByRole("button", { name: /Add missing check/ }).click();
			if (loseReply) {
				const status = page.getByRole("region", { name: "Selected repair status", exact: true });
				await expect(status.getByRole("alert")).toContainText("Could not check selected repair status");
				await expect(status.getByRole("button", { name: "Check repair status", exact: true })).toBeEnabled();
				await page.reload();
				await page.getByRole("button", { name: "Check repair status", exact: true }).click();
			}

			const comparison = page.getByRole("region", { name: "Saved repair evidence", exact: true });
			await comparison.getByText("Compare saved steps", { exact: true }).click();
			await expect(comparison).toContainText(priorAssertion);
			await expect(comparison).toContainText(addedAssertion);
			await expect(comparison).toContainText(/1 .*preserved/i);
			const savedData = comparison.locator(".test-step-data-value");
			await expect(savedData).toHaveCount(2);
			expect(await savedData.allTextContents()).toEqual([literal, literal]);
			await expect(savedData.first()).toHaveCSS("white-space", "break-spaces");
			await expect(page.getByRole("button", { name: "Review saved suite", exact: true })).toBeVisible();
			await expectNoDocumentOverflow(page, "Saved repair evidence");
			await page.screenshot({ path: testInfo.outputPath("saved-repair-evidence.png") });

			const originalLink = comparison.getByRole("link", { name: "View original saved case", exact: true });
			const repairedLink = comparison.getByRole("link", { name: "View repaired saved case", exact: true });
			expect(Object.fromEntries(new URL(await originalLink.getAttribute("href"), page.url()).searchParams)).toEqual({
				suite: beforeId,
				case: caseId,
			});
			expect(Object.fromEntries(new URL(await repairedLink.getAttribute("href"), page.url()).searchParams)).toEqual({
				suite: afterId,
				case: caseId,
			});
			expect(api.evidenceReads).toEqual(
				expect.arrayContaining([
					{ snapshot_id: beforeId, case_id: caseId },
					{ snapshot_id: afterId, case_id: caseId },
				])
			);
			expect(api.repairs).toHaveLength(1);
			expect(api.statusReads.some(({ request_id }) => request_id === api.repairs[0].requestId)).toBe(true);
			expect(api.writes).toEqual([`POST ${repairPath}`]);
			expect(api.errors).toEqual([]);
		});
	}
});
