import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import {
	installUseCaseReviewApi,
	useCaseOrchestratorFixture,
	useCaseProjectFixture,
	USE_CASE_PROJECT_ID,
} from "./support/use-case-review.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const changeId = "change_checkout_correction";
const operationId = `draft_${"b".repeat(64)}`;
const acceptPath = `${root}/changes/accept`;
const resumePath = `${root}/changes/resume`;
const admissionPath = `${root}/changes/admission`;
const releasePath = `${admissionPath}/${operationId}/release`;
const correction = "Purchases above 600 require supervisor approval.";
const status = (page) => page.getByRole("region", { name: "Project update status", exact: true });
const statusButton = (page, name) => status(page).getByRole("button", { name, exact: true });
const check = (page) => statusButton(page, "Check refresh progress");
const resume = (page) => statusButton(page, "Resume remaining stages");

async function fixture(page, { enabled = true, priorStatus, priorAttempt, failFirstStatus, rejectFirstRelease, loseReplies } = {}) {
	const project = useCaseProjectFixture();
	const finding = {
		id: "finding-checkout",
		status: "open",
		category: "ambiguous_requirement",
		summary: "Confirm supervisor threshold",
		expected_behavior: "Threshold is 600",
		requirement_ids: ["REQ-202"],
		test_case_ids: [],
		source_snapshot_id: project.current_snapshots.requirements.snapshot_id,
	};
	let attempt = priorAttempt || null;
	let anotherDraft = false;
	const writes = [];
	const reads = [];
	const acceptRequests = [];
	const resumeRequests = [];
	const releases = [];
	const errors = [];
	const setSync = (syncStatus) => {
		project.current_revision++;
		project.project_sync = {
			id: changeId,
			status: syncStatus,
			finding_id: finding.id,
			stages: { requirements: "draft", context: "draft", use_cases: "draft", test_cases: "pending", automation: "not_requested" },
		};
		finding.status = "accepted";
		finding.change_id = changeId;
		finding.decision = correction;
		project.project_decisions = [{ id: "saved-checkout-decision", requirement_ids: ["REQ-202"], text: correction }];
	};
	if (priorStatus) setSync(priorStatus);

	page.on("pageerror", (error) => errors.push(error.message));
	page.on("request", (request) => {
		if (!["fetch", "xhr"].includes(request.resourceType()) || ["GET", "HEAD", "OPTIONS"].includes(request.method())) return;
		writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		return route.fulfill({ status: 404, json: { detail: "No response for this optional endpoint." } });
	});
	await installUseCaseReviewApi(page, { initialProject: project });
	await page.route(`**${root}`, (route) => route.fulfill({ json: project }));
	await page.route(`**${root}/orchestrator/status`, (route) => route.fulfill({ json: useCaseOrchestratorFixture(project) }));
	await page.route(`**${root}/knowledge`, (route) => route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } }));
	await page.route(`**${root}/quality-findings`, (route) =>
		route.fulfill({ json: { findings: [finding], project_revision: project.current_revision } })
	);
	await page.route(`**${root}/requirement-imports`, (route) => route.fulfill({ json: [] }));
	await page.route(`**${root}/approval-continuations`, (route) =>
		route.fulfill({ json: { enabled: false, tests_enabled: false, runs: [] } })
	);
	await page.route(`**${admissionPath}`, (route) => {
		const request = route.request();
		expect(request.method()).toBe("GET");
		reads.push(request.url());
		if (failFirstStatus && reads.length === 1)
			return route.fulfill({ status: 503, json: { detail: "Status is temporarily unavailable." } });
		return route.fulfill({ json: { enabled, blocked: Boolean(attempt || anotherDraft), attempt } });
	});
	await page.route(`**${acceptPath}`, (route) => {
		const request = route.request();
		expect(request.method()).toBe("POST");
		const input = request.postDataJSON();
		const requestId = request.headers()["x-request-id"];
		expect(requestId).toBeTruthy();
		acceptRequests.push({ input, requestId });
		expect(input).toMatchObject({
			base_project_revision: project.current_revision,
			finding_id: finding.id,
			decision: correction,
			corrections: [{ requirement_id: "REQ-202", text: correction }],
		});
		setSync("partial");
		anotherDraft = true;
		if (loseReplies) return route.abort("failed");
		return route.fulfill({ json: project });
	});
	await page.route(`**${resumePath}`, (route) => {
		const request = route.request();
		expect(request.method()).toBe("POST");
		const requestId = request.headers()["x-request-id"];
		const input = request.postDataJSON();
		expect(input).toEqual({ change_id: changeId });
		expect(requestId).toBeTruthy();
		expect(anotherDraft).toBe(false);
		expect(attempt).toBeNull();
		resumeRequests.push({ input, requestId });
		if (loseReplies && resumeRequests.length === 1) {
			attempt = {
				operation_id: operationId,
				fence: 12,
				state: "running",
				change_id: changeId,
				stage: "test_cases",
				request_id: requestId,
			};
			return route.abort("failed");
		}
		project.current_revision++;
		project.project_sync = {
			...project.project_sync,
			status: "ready_for_review",
			stages: { ...project.project_sync.stages, test_cases: "draft", automation: "deferred" },
			review: { changed: 1, added: 0, preserved: 49 },
		};
		return route.fulfill({ json: project });
	});
	await page.route(`**${releasePath}`, (route) => {
		const request = route.request();
		expect(request.method()).toBe("POST");
		expect(attempt).toBeTruthy();
		const input = request.postDataJSON();
		expect(input).toEqual({ fence: attempt.fence });
		releases.push(input);
		if (rejectFirstRelease && releases.length === 1) {
			attempt = { ...attempt, fence: attempt.fence + 1 };
			return route.fulfill({ status: 409, json: { detail: "The attempt fence changed." } });
		}
		attempt = null;
		if (loseReplies) return route.abort("failed");
		return route.fulfill({ json: { released: true } });
	});

	await seedAuthenticatedSession(page);
	return {
		writes,
		reads,
		errors,
		acceptRequests,
		resumeRequests,
		releases,
		clearOtherDraft: () => {
			anotherDraft = false;
		},
		interrupt: () => {
			attempt = { ...attempt, state: "interrupted" };
		},
	};
}

test.describe("Project Sync admission recovery", () => {
	test.skip(
		process.env.E2E_PROJECT_WORKSPACES_V2 !== "true" || process.env.E2E_APPROVAL_CONTINUATION_ENABLED !== "true",
		"Requires VITE_PROJECT_WORKSPACES_V2=true and VITE_APPROVAL_CONTINUATION_ENABLED=true"
	);

	test("recovers a saved correction, an interrupted refresh and a lost release without repeating a paid action", async ({
		page,
	}, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const api = await fixture(page, { loseReplies: true });
		await page.goto(`${root}/review`);
		const form = page.getByRole("region", { name: "Shared project corrections", exact: true });
		await form.getByLabel("Finding to resolve").selectOption("finding-checkout");
		await form.getByLabel("Accepted business decision").fill(correction);
		await form.getByLabel("Corrected source · REQ-202").fill(correction);
		await form.getByRole("button", { name: "Accept correction and refresh drafts", exact: true }).click();
		await expect(status(page)).toContainText("Correction saved. Another draft operation is active for this project.");
		await expect(form.getByRole("button", { name: "Accept correction and refresh drafts", exact: true })).toHaveCount(0);
		await form.getByText("Accepted project decisions (1)", { exact: true }).click();
		await expect(form).toContainText(correction);
		await expect(resume(page)).toBeDisabled();
		expect(api.acceptRequests).toHaveLength(1);
		expect(api.writes).toEqual([`POST ${acceptPath}`]);

		const readsBefore = api.reads.length;
		await check(page).click();
		await expect.poll(() => api.reads.length).toBeGreaterThan(readsBefore);
		await expect(resume(page)).toBeDisabled();
		expect(api.writes).toEqual([`POST ${acceptPath}`]);

		api.clearOtherDraft();
		await check(page).click();
		await expect(resume(page)).toBeEnabled();
		await resume(page).evaluate((button) => {
			button.click();
			button.click();
		});
		await expect(status(page)).toContainText("A draft refresh is running.");
		await expect(status(page)).toContainText("Updating project drafts");
		await expect(statusButton(page, "Cancel draft refresh")).toBeEnabled();
		await expect(resume(page)).toBeDisabled();
		expect(api.resumeRequests).toHaveLength(1);

		api.interrupt();
		await page.reload();
		await expect(status(page)).toContainText("The provider may already have received the request.");
		await status(page).getByText("Draft refresh details", { exact: true }).click();
		await expect(status(page).getByText(api.resumeRequests[0].requestId, { exact: true })).toBeVisible();
		await expect(status(page).getByText(changeId, { exact: true })).toBeVisible();
		await expect(page.getByRole("heading", { name: "Requirement Review Workbench", exact: true })).toBeVisible();
		await expect(page.getByText("Loading project recommendations...", { exact: true })).toHaveCount(0);
		await page.screenshot({ path: testInfo.outputPath("interrupted-project-sync.png") });
		expect(api.writes).toEqual([`POST ${acceptPath}`, `POST ${resumePath}`]);

		await statusButton(page, "Acknowledge and release").click();
		await expect(resume(page)).toBeEnabled();
		expect(api.releases).toEqual([{ fence: 12 }]);
		expect(api.writes).toEqual([`POST ${acceptPath}`, `POST ${resumePath}`, `POST ${releasePath}`]);
		await resume(page).click();
		await expect(status(page)).toContainText("Updated drafts are ready for review");
		await status(page).getByText("Updated drafts are ready for review", { exact: true }).click();
		await expect(status(page)).toContainText("1 changed · 0 added · 49 preserved");
		expect(api.resumeRequests).toHaveLength(2);
		expect(api.resumeRequests[1].requestId).not.toBe(api.resumeRequests[0].requestId);
		expect(api.writes).toEqual([`POST ${acceptPath}`, `POST ${resumePath}`, `POST ${releasePath}`, `POST ${resumePath}`]);
		expect(api.errors).toEqual([]);
	});

	test("a superseded correction keeps its exact attempt visible when status or release is unconfirmed", async ({ page }) => {
		const api = await fixture(page, {
			priorStatus: "superseded",
			failFirstStatus: true,
			rejectFirstRelease: true,
			priorAttempt: {
				operation_id: operationId,
				fence: 18,
				state: "interrupted",
				change_id: "change_older_checkout_correction",
				stage: "use_cases",
				request_id: "older-checkout-review",
			},
		});
		await page.goto(`${root}/review`);
		await expect(status(page).getByRole("alert")).toHaveText("Could not check draft refresh status. Check progress again.");
		await expect(resume(page)).toHaveCount(0);
		await check(page).click();
		await expect(status(page)).toContainText("This attempt uses an earlier correction or source revision.");
		const acknowledge = statusButton(page, "Acknowledge and release");
		await expect(acknowledge).toBeEnabled();
		expect(api.writes).toEqual([]);
		await acknowledge.click();
		await expect(status(page).getByRole("alert")).toHaveText("The release was not confirmed. Check the current attempt.");
		await expect(acknowledge).toBeDisabled();
		await check(page).click();
		await expect(acknowledge).toBeEnabled();
		await acknowledge.click();
		await expect(acknowledge).toHaveCount(0);
		await expect(status(page)).toContainText("Project sources have changed");
		await expect(resume(page)).toHaveCount(0);
		expect(api.releases).toEqual([{ fence: 18 }, { fence: 19 }]);
		expect(api.writes).toEqual([`POST ${releasePath}`, `POST ${releasePath}`]);
		expect(api.errors).toEqual([]);
	});

	test("a disabled admission capability keeps the existing deliberate resume", async ({ page }) => {
		const api = await fixture(page, { enabled: false, priorStatus: "partial" });
		await page.goto(`${root}/review`);
		await expect(resume(page)).toBeEnabled();
		await expect(check(page)).toHaveCount(0);
		expect(api.writes).toEqual([]);
		await resume(page).click();
		await expect(status(page)).toContainText("Updated drafts are ready for review");
		expect(api.writes).toEqual([`POST ${resumePath}`]);
		expect(api.errors).toEqual([]);
	});
});
