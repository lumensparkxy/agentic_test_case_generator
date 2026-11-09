import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import {
	USE_CASE_PROJECT_ID,
	useCaseOrchestratorFixture,
	useCaseProjectFixture,
	useCaseSnapshotFixture,
} from "./support/use-case-review.js";
import { createDeferred, installWorkspaceApi } from "./support/workspace.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const secondRoot = "/projects/continuation-second-project";
const approvalPath = `${root}/requirements/approve-and-prepare-scenarios`;
const statusPath = `${root}/approval-continuations`;
const cancelPath = `${statusPath}/checkout-preparation/cancel`;
const initialRevision = 7;
const readyPlanId = "checkout-prepared-plan-v2";
const readyScenario = "Verify the current checkout confirmation";
const progress = (page) => page.getByRole("region", { name: "Scenario preparation", exact: true });
const approve = (page) => page.getByRole("button", { name: "Approve scope and prepare scenarios", exact: true });
const workspace = (page, name) =>
	page.getByRole("navigation", { name: "Project workspaces", exact: true }).getByRole("link", { name, exact: true });
const check = (page) => progress(page).getByRole("button", { name: "Check preparation status", exact: true });
const routePath = (path) => new RegExp(`${path}(?:\\?.*)?$`);

function pendingProject() {
	const project = useCaseProjectFixture();
	project.stage_state.use_cases.stale = true;
	project.stage_state.requirements.approved = false;
	project.current_snapshots.requirements.approved = false;
	project.current_snapshots.requirements.payload.requirements.forEach((row) => {
		row.requirement_uid = `checkout-${row.id}`;
		row.review_status = "Needs Review";
	});
	return project;
}

async function fixture(page, { enabled = true, loseReply = false, firstStatusGate, priorRun } = {}) {
	const project = pendingProject();
	const other = useCaseProjectFixture({ project_id: "continuation-second-project", name: "Separate checkout", snapshot: null });
	let run = priorRun || null;
	let hideRun = loseReply;
	let failPlanReadback = false;
	let planAnnounced = false;
	let failedPlanReads = 0;
	const writes = [];
	const approvalRequests = [];
	const reads = [];
	const errors = [];
	const firstStatusSettled = createDeferred();
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("console", (message) => {
		if (message.type() === "error" && /same key|unique.*key/i.test(message.text())) errors.push(message.text());
	});
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "OPTIONS", "HEAD"].includes(request.method())) {
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
		}
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		const path = new URL(route.request().url()).pathname;
		if (path.endsWith("/knowledge")) return route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } });
		if (path.endsWith("/quality-findings")) return route.fulfill({ json: { findings: [], project_revision: project.current_revision } });
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional endpoint." } });
	});
	await installWorkspaceApi(page, { projectDetails: { [project.project_id]: project, [other.project_id]: other } });
	await page.route(routePath(root), (route) => {
		if (route.request().method() === "GET" && failPlanReadback && planAnnounced) {
			failPlanReadback = false;
			failedPlanReads++;
			return route.fulfill({ status: 503, json: { detail: "The saved plan readback is temporarily unavailable." } });
		}
		return route.fallback();
	});
	await page.route(routePath(`${root}/orchestrator/status`), (route) => route.fulfill({ json: useCaseOrchestratorFixture(project) }));
	await page.route(routePath(`${secondRoot}/orchestrator/status`), (route) => route.fulfill({ json: useCaseOrchestratorFixture(other) }));

	await page.route(routePath(statusPath), async (route) => {
		const request = route.request();
		expect(request.method()).toBe("GET");
		const url = new URL(request.url());
		const requestId = url.searchParams.get("request_id");
		reads.push({ project: project.project_id, requestId });
		const runs = !hideRun && run ? [run] : [];
		if (runs[0]?.state === "awaiting_user") planAnnounced = true;
		const receipt =
			runs.length && requestId === run.request_id ? { request_id: requestId, run_id: run.run_id, blocked_reason: null } : null;
		const held = firstStatusGate && reads.length === 1;
		if (held) await firstStatusGate.promise;
		try {
			await route.fulfill({ json: { enabled, runs, receipt } });
		} catch (error) {
			if (!held) throw error;
		} finally {
			if (held) firstStatusSettled.resolve();
		}
	});
	await page.route(routePath(`${secondRoot}/approval-continuations`), (route) => {
		expect(route.request().method()).toBe("GET");
		reads.push({ project: other.project_id, requestId: null });
		return route.fulfill({ json: { enabled: true, runs: [] } });
	});

	function saveApproval(input) {
		expect(input.base_project_revision).toBe(project.current_revision);
		expect(input.idempotency_key).toBeTruthy();
		expect(input.reviews).toEqual(
			project.current_snapshots.requirements.payload.requirements.map(({ requirement_uid }) => ({
				requirement_uid,
				review_status: "Approved",
				quality_flags: [],
			}))
		);
		const snapshotId = "checkout-approved-requirements";
		project.current_revision++;
		project.current_snapshots.requirements.snapshot_id = snapshotId;
		project.current_snapshots.requirements.project_revision = project.current_revision;
		project.current_snapshots.requirements.approved = true;
		project.current_snapshots.requirements.payload.requirements.forEach((row) => {
			row.review_status = "Approved";
		});
		project.stage_state.requirements.current_snapshot_id = snapshotId;
		project.stage_state.requirements.approved = true;
	}

	await page.route(routePath(approvalPath), (route) => {
		const request = route.request();
		expect(request.method()).toBe("POST");
		const input = request.postDataJSON();
		const requestId = request.headers()["x-request-id"];
		expect(requestId).toBe(input.idempotency_key);
		approvalRequests.push({ input, requestId });
		saveApproval(input);
		run = {
			run_id: "checkout-preparation",
			state: "queued",
			stage: "prepare_scenarios",
			project_revision: project.current_revision,
			request_id: requestId,
			reason: null,
			result_snapshot_id: null,
		};
		if (loseReply) return route.abort("failed");
		return route.fulfill({ json: { project, continuation: run, blocked_reason: null } });
	});
	await page.route(routePath(cancelPath), (route) => {
		expect(route.request().method()).toBe("POST");
		expect(run.state).toBe("running");
		run = { ...run, state: "cancelled", reason: "cancelled_by_owner" };
		return route.fulfill({ json: run });
	});
	await page.route(routePath(`${root}/requirements/reviews`), (route) => {
		expect(route.request().method()).toBe("PATCH");
		saveApproval(route.request().postDataJSON());
		return route.fulfill({ json: project });
	});

	await seedAuthenticatedSession(page);
	return {
		approvalRequests,
		firstStatusSettled,
		reads,
		writes,
		errors,
		other,
		setVisible: () => {
			hideRun = false;
		},
		setRunning: () => {
			run = { ...run, state: "running" };
		},
		failedPlanReads: () => failedPlanReads,
		savePreparedPlan: () => {
			failPlanReadback = true;
			project.current_revision++;
			const plan = useCaseSnapshotFixture({
				snapshot_id: readyPlanId,
				version: 2,
				project_revision: project.current_revision,
				source_snapshot_id: project.current_snapshots.requirements.snapshot_id,
			});
			plan.payload.coverage_plan[0].scenarios[0].title = readyScenario;
			project.current_snapshots.use_cases = plan;
			project.stage_state.use_cases = { current_snapshot_id: readyPlanId, version: 2, approved: false, stale: false, metadata: {} };
			run = { ...run, state: "awaiting_user", project_revision: project.current_revision, result_snapshot_id: readyPlanId };
		},
	};
}

test.describe("Recoverable approval continuation", () => {
	test.skip(
		process.env.E2E_PROJECT_WORKSPACES_V2 !== "true" || process.env.E2E_APPROVAL_CONTINUATION_ENABLED !== "true",
		"Requires VITE_PROJECT_WORKSPACES_V2=true and VITE_APPROVAL_CONTINUATION_ENABLED=true"
	);

	test("recovers a lost approval acknowledgement and opens the exact prepared plan without another write", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const api = await fixture(page, { loseReply: true });
		await page.goto(`${root}/review`);
		await expect(approve(page)).toBeEnabled();
		expect(api.writes).toEqual([]);

		await approve(page).evaluate((element) => {
			element.click();
			element.click();
		});
		await expect(progress(page)).toContainText("Could not confirm the approval response. Check preparation status.");
		expect(api.approvalRequests).toHaveLength(1);
		expect(api.approvalRequests[0].input.base_project_revision).toBe(initialRevision);
		expect(api.writes).toEqual([`POST ${approvalPath}`]);

		const priorReads = api.reads.length;
		await page.reload();
		await expect(progress(page)).toContainText("Could not confirm the approval response. Check preparation status.");
		await expect(page.getByRole("status").filter({ hasText: /2\/2 approved in scope/ })).toHaveCount(1);
		await expect
			.poll(() => api.reads.slice(priorReads).some(({ requestId }) => requestId === api.approvalRequests[0].requestId))
			.toBe(true);

		const scenarioSection = page
			.getByRole("navigation", { name: "Workspace sections", exact: true })
			.getByRole("link", { name: "Scenarios", exact: true });
		await scenarioSection.press("Enter");
		await expect(page).toHaveURL(`${root}/review/scenarios`);
		await expect(page.getByRole("button", { name: "Regenerate Use Cases", exact: true })).toBeDisabled();
		await expect(page.locator("#main-content")).toBeFocused();

		await workspace(page, "Review").press("Enter");
		await expect(page.getByRole("heading", { name: "Review scope", exact: true })).toBeVisible();
		api.setVisible();
		await check(page).click();
		await expect(progress(page)).toContainText("Scope approved. Scenario preparation is queued.");
		expect(api.approvalRequests).toHaveLength(1);

		api.setRunning();
		await check(page).click();
		await expect(progress(page)).toContainText("Preparing scenarios from the approved scope.");
		await workspace(page, "Inputs").press("Enter");
		await expect(page).toHaveURL(`${root}/inputs`);
		api.savePreparedPlan();
		await workspace(page, "Review").press("Enter");
		await expect(progress(page)).toContainText("A scenario plan was saved. Check preparation status to load the current evidence.");
		const openPlan = progress(page).getByRole("link", { name: "Open prepared scenarios", exact: true });
		await expect.poll(api.failedPlanReads).toBe(1);
		await expect(openPlan).toHaveCount(0);
		await check(page).click();
		await expect(openPlan).toBeVisible();
		await expect(progress(page)).toContainText("Scenarios are ready for review.");
		await page.screenshot({ path: testInfo.outputPath("prepared-scenarios-ready.png") });
		await openPlan.press("Enter");
		await expect(page).toHaveURL(`${root}/review/scenarios`);
		await expect(page.getByText(readyScenario, { exact: true })).toBeVisible();
		await expect(page.getByText("Complete express checkout", { exact: true })).toHaveCount(0);
		await expect(page.getByLabel("Current human review status")).toHaveText("Awaiting human review");
		expect(api.writes).toEqual([`POST ${approvalPath}`]);
		expect(api.errors).toEqual([]);
	});

	test("cancellation retains the saved scope and is still visible after reload", async ({ page }) => {
		const api = await fixture(page);
		await page.goto(`${root}/review`);
		await approve(page).click();
		await expect(progress(page)).toContainText("Scope approved. Scenario preparation is queued.");
		api.setRunning();
		await check(page).click();
		await expect(progress(page)).toContainText("Preparing scenarios from the approved scope.");
		await progress(page).getByRole("button", { name: "Cancel preparation", exact: true }).click();
		await expect(progress(page)).toContainText("Scenario preparation was cancelled.");
		await page.reload();
		await expect(progress(page)).toContainText("Scenario preparation was cancelled.");
		await expect(page.getByRole("status").filter({ hasText: /2\/2 approved in scope/ })).toHaveCount(1);
		await expect(progress(page).getByRole("link", { name: "Open prepared scenarios" })).toHaveCount(0);
		expect(api.writes).toEqual([`POST ${approvalPath}`, `POST ${cancelPath}`]);
		expect(api.errors).toEqual([]);
	});

	test("runtime-disabled projects retain approval-only review", async ({ page }) => {
		const api = await fixture(page, { enabled: false });
		await page.goto(`${root}/review`);
		await expect.poll(() => api.reads.length).toBeGreaterThan(0);
		await expect(approve(page)).toHaveCount(0);
		await page.getByText("Review actions", { exact: true }).click();
		await page.getByRole("button", { name: "Approve eligible requirements", exact: true }).click();
		await expect(page.getByRole("status").filter({ hasText: /2\/2 approved in scope/ })).toHaveCount(1);
		expect(api.writes).toEqual([`PATCH ${root}/requirements/reviews`]);
		expect(api.errors).toEqual([]);
	});

	test("a late project status cannot replace the new project's Review", async ({ page }) => {
		const firstStatusGate = createDeferred();
		const api = await fixture(page, {
			firstStatusGate,
			priorRun: {
				run_id: "checkout-preparation",
				state: "running",
				stage: "prepare_scenarios",
				project_revision: initialRevision,
				request_id: "older-project-request",
				reason: null,
				result_snapshot_id: null,
			},
		});
		await page.goto(`${root}/review`);
		await expect.poll(() => api.reads.length).toBe(1);
		await page.getByRole("button", { name: "Open QA project menu" }).click();
		await page.getByRole("button", { name: `Open QA project ${api.other.name}`, exact: true }).click();
		await expect(page).toHaveURL(secondRoot);
		await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
		await expect(page.locator("#main-content")).toBeFocused();
		await workspace(page, "Review").press("Enter");
		await expect(page).toHaveURL(`${secondRoot}/review`);
		await expect.poll(() => api.reads.some(({ project }) => project === api.other.project_id)).toBe(true);
		firstStatusGate.resolve();
		await api.firstStatusSettled.promise;
		await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
		await expect(page.getByRole("heading", { name: "Requirement Review Workbench", exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: "Open QA project menu" })).toContainText(api.other.name);
		await expect(progress(page).getByText("Preparing scenarios from the approved scope.", { exact: true })).toHaveCount(0);
		expect(api.writes).toEqual([]);
		expect(api.errors).toEqual([]);
	});

	test("switching accounts cannot recover another reviewer's unconfirmed request", async ({ page }) => {
		const api = await fixture(page, { loseReply: true });
		await page.goto(`${root}/review`);
		await approve(page).click();
		await expect(progress(page)).toContainText("Could not confirm the approval response. Check preparation status.");

		const other = { sub: "another-continuation-reviewer", name: "Another continuation reviewer", email: "other@example.test" };
		await seedAuthenticatedSession(page, other);
		await page.route("**/auth/me", (route) => route.fulfill({ json: other }));
		const priorReads = api.reads.length;
		await page.reload();
		await expect(page.getByRole("button", { name: `Open account menu for ${other.name}`, exact: true })).toBeVisible();
		await expect.poll(() => api.reads.length).toBeGreaterThan(priorReads);
		await expect(
			progress(page).getByText("Could not confirm the approval response. Check preparation status.", { exact: true })
		).toHaveCount(0);
		expect(api.reads.slice(priorReads).every(({ requestId }) => requestId === null)).toBe(true);
		expect(api.approvalRequests).toHaveLength(1);
		expect(api.writes).toEqual([`POST ${approvalPath}`]);
		expect(api.errors).toEqual([]);
	});
});
