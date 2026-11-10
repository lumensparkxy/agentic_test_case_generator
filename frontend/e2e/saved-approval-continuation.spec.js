import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import {
	USE_CASE_PROJECT_ID,
	useCaseOrchestratorFixture,
	useCaseProjectFixture,
	useCaseSnapshotFixture,
} from "./support/use-case-review.js";
import { installWorkspaceApi } from "./support/workspace.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const statusPath = `${root}/approval-continuations`;
const generatePath = `${statusPath}/generate-tests-from-saved-approval`;
const testsPath = `${root}/review/tests`;
const suiteId = "first-suite-from-saved-approval";
const testTitle = "Preserve checkout cart after a declined payment";
const progress = (page) => page.getByRole("region", { name: "Test case generation", exact: true });
const start = (page) => progress(page).getByRole("button", { name: "Generate tests from saved approval", exact: true });
const legacyStart = (page) => page.getByRole("button", { name: "Start generation", exact: true });
const check = (page) => progress(page).getByRole("button", { name: "Check test generation status", exact: true });
const openTests = (page) => progress(page).getByRole("link", { name: "Open generated tests", exact: true });

async function installSavedApproval(page, { dropResponse = false, conflict = false, competing = false, enabled = true } = {}) {
	const project = useCaseProjectFixture({
		reviewState: "approved",
		snapshot: useCaseSnapshotFixture({ operation: "use_cases.generate", request_id: "ordinary-scenario-generation" }),
	});
	const savedReview = project.stage_state.use_cases.metadata.latest_human_review;
	const offer = {
		base_project_revision: project.current_revision,
		snapshot_id: project.current_snapshots.use_cases.snapshot_id,
		review_id: savedReview.review_id,
	};
	let run = null;
	let revealRun = !dropResponse;
	const writes = [];
	const submissions = [];
	const statusReads = [];
	const errors = [];
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (["POST", "PATCH", "PUT", "DELETE"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		return route.fulfill({ status: 404, json: { detail: "Optional endpoint unavailable in this fixture." } });
	});
	await installWorkspaceApi(page, { projectDetails: { [project.project_id]: project } });
	await page.route(`**${root}/orchestrator/status`, (route) =>
		route.fulfill({
			json: useCaseOrchestratorFixture(project, {
				next_actions: project.current_snapshots.test_cases
					? []
					: [
							{
								action: "generate",
								label: "Generate Test Cases",
								stage: "test_cases",
								enabled: true,
								primary: true,
								secondary: false,
								reason: "Ready to generate test cases",
								blockers: [],
							},
						],
			}),
		})
	);
	await page.route(new RegExp(`${statusPath}(?:\\?.*)?$`), (route) => {
		expect(route.request().method()).toBe("GET");
		const query = Object.fromEntries(new URL(route.request().url()).searchParams);
		statusReads.push(query);
		const visibleRun = revealRun ? run : null;
		return route.fulfill({
			json: {
				enabled,
				tests_enabled: enabled,
				runs: visibleRun ? [visibleRun] : [],
				receipt:
					visibleRun?.request_id === query.request_id && query.stage === "generate_tests"
						? { request_id: query.request_id, run_id: visibleRun.run_id, blocked_reason: null }
						: null,
				saved_review_enabled: enabled,
				...(enabled && !run && !project.stage_state.use_cases.stale ? { saved_plan_generation: offer } : {}),
			},
		});
	});
	await page.route(`**${generatePath}`, (route) => {
		const request = route.request();
		const requestId = request.headers()["x-request-id"];
		const payload = request.postDataJSON();
		submissions.push({ requestId, payload });
		expect(request.method()).toBe("POST");
		expect(requestId).toBeTruthy();
		expect(payload).toEqual({ ...offer, template: expect.objectContaining({ name: "default", fields: expect.any(Array) }) });
		if (conflict) {
			project.current_revision++;
			project.stage_state.use_cases.stale = true;
			return route.fulfill({
				status: 409,
				json: {
					detail: { code: "saved_plan_approval_changed", message: "The saved scenario approval changed. Review the current scenarios." },
				},
			});
		}
		run = {
			run_id: "saved-approval-test-generation",
			stage: "generate_tests",
			state: competing ? "cancelled" : "queued",
			project_revision: project.current_revision,
			request_id: competing ? "other-tabs-request" : requestId,
			reason: null,
			source_snapshot_id: offer.snapshot_id,
			result_snapshot_id: null,
		};
		return dropResponse || competing ? route.abort("connectionreset") : route.fulfill({ json: { continuation: run } });
	});
	await seedAuthenticatedSession(page);
	return {
		writes,
		submissions,
		statusReads,
		errors,
		savedReview,
		publishSuite() {
			project.current_revision++;
			const test_cases = [
				{
					id: "TC-SAVED-01",
					title: testTitle,
					linked_requirement_ids: ["REQ-101"],
					steps: [{ step: 1, action: "Submit a declined saved card", expected: "The cart remains available for another payment method." }],
				},
			];
			project.current_snapshots.test_cases = {
				snapshot_id: suiteId,
				project_id: project.project_id,
				project_revision: project.current_revision,
				stage: "test_cases",
				version: 1,
				operation: "testcases.generate",
				source_snapshot_id: offer.snapshot_id,
				approved: false,
				metadata: {},
				payload: { test_cases },
			};
			project.stage_state.test_cases = { current_snapshot_id: suiteId, version: 1, approved: false, stale: false, metadata: {} };
			run = { ...run, state: "completed", project_revision: project.current_revision, result_snapshot_id: suiteId };
			revealRun = true;
		},
	};
}

test.describe("Generate tests from a saved scenario approval", () => {
	test.skip(
		process.env.E2E_PROJECT_WORKSPACES_V2 !== "true" || process.env.E2E_APPROVAL_CONTINUATION_ENABLED !== "true",
		"Requires VITE_PROJECT_WORKSPACES_V2=true and VITE_APPROVAL_CONTINUATION_ENABLED=true"
	);

	test("reuses the exact ordinary approval after one deliberate generation action", async ({ page }, testInfo) => {
		const api = await installSavedApproval(page);
		await page.goto(`${root}/review/scenarios`);
		await expect(page.getByRole("status", { name: "Current human review status" })).toHaveText("Human approved");
		await expect(page.getByRole("button", { name: "Generate tests from saved approval", exact: true })).toHaveCount(0);
		await page.getByRole("navigation", { name: "Workspace sections" }).getByRole("link", { name: "Tests" }).click();
		await expect(start(page)).toBeEnabled();
		await expect(legacyStart(page)).toHaveCount(0);
		expect(api.writes).toEqual([]);
		await page.screenshot({ path: testInfo.outputPath("saved-approval-generation-ready.png") });
		await start(page).press("Enter");
		await expect(page).toHaveURL(testsPath);
		await expect(progress(page)).toContainText("Scenarios approved. Test case generation is queued.");
		await expect(start(page)).toHaveCount(0);
		await expect(legacyStart(page)).toHaveCount(0);
		expect(api.submissions.map((entry) => entry.payload.review_id)).toEqual([api.savedReview.review_id]);
		expect(api.writes).toEqual([`POST ${generatePath}`]);
		expect(api.errors).toEqual([]);
	});

	test("recovers a lost acknowledgement after reload by reading the exact saved suite", async ({ page }) => {
		const api = await installSavedApproval(page, { dropResponse: true });
		await page.goto(testsPath);
		await start(page).click();
		await expect(progress(page)).toContainText("Could not confirm the generation request. Check test generation status.");
		await page.reload();
		await expect(progress(page)).toContainText("Could not confirm the generation request. Check test generation status.");
		await expect(openTests(page)).toHaveCount(0);
		const [{ requestId }] = api.submissions;
		api.publishSuite();
		await check(page).click();
		await expect(openTests(page)).toBeVisible();
		await expect(progress(page)).toContainText("Test cases are saved for review.");
		await expect(page.getByText(testTitle, { exact: true }).first()).toBeVisible();
		expect(api.statusReads).toContainEqual({ request_id: requestId, stage: "generate_tests" });
		expect(api.writes).toEqual([`POST ${generatePath}`]);
		expect(api.errors).toEqual([]);
	});

	test("shows a known stale refusal and reloads the project without retrying", async ({ page }) => {
		const api = await installSavedApproval(page, { conflict: true });
		await page.goto(testsPath);
		await start(page).click();
		await expect(progress(page).getByRole("alert")).toHaveText("The saved scenario approval changed. Review the current scenarios.");
		await expect(start(page)).toHaveCount(0);
		await expect(progress(page).getByRole("button", { name: "Retry generation request", exact: true })).toHaveCount(0);
		await expect(legacyStart(page)).toBeDisabled();
		expect(api.writes).toEqual([`POST ${generatePath}`]);
		expect(api.errors).toEqual([]);
	});

	test("a lost competing response shows the existing run at the unchanged revision", async ({ page }) => {
		const api = await installSavedApproval(page, { competing: true });
		await page.goto(testsPath);
		await start(page).click();
		await expect(progress(page)).toContainText("Test case generation was cancelled.");
		const [{ requestId }] = api.submissions;
		expect(api.statusReads).toContainEqual({ request_id: requestId, stage: "generate_tests" });
		await expect.poll(() => api.statusReads.at(-1)).toEqual({});
		await page.reload();
		await expect(progress(page)).toContainText("Test case generation was cancelled.");
		await expect(start(page)).toHaveCount(0);
		await expect(legacyStart(page)).toHaveCount(0);
		expect(api.writes).toEqual([`POST ${generatePath}`]);
		expect(api.errors).toEqual([]);
	});

	test("retains ordinary generation when the server disables the demo capability", async ({ page }) => {
		const api = await installSavedApproval(page, { enabled: false });
		await page.goto(testsPath);
		await expect(legacyStart(page)).toBeEnabled();
		await expect(start(page)).toHaveCount(0);
		expect(api.writes).toEqual([]);
	});
});
