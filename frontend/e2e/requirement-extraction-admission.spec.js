import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { USE_CASE_PROJECT_ID, useCaseProjectFixture } from "./support/use-case-review.js";
import { createDeferred, installWorkspaceApi } from "./support/workspace.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const otherRoot = "/projects/separate-extraction-project";
const importsPath = `${root}/requirement-imports`;
const statusPath = `${importsPath}/extraction-status`;
const parsePath = "/requirements/parse";
const operationId = `draft_${"b".repeat(64)}`;
const releasePath = `${importsPath}/extraction-attempts/${operationId}/release`;
const sourceName = "booking.md";
const sourceText = "The confirmed booking records the selected room.";
const status = (page) => page.getByRole("region", { name: "Requirement extraction", exact: true });
const check = (page) => status(page).getByRole("button", { name: "Check extraction status", exact: true });
const release = (page) => status(page).getByRole("button", { name: "Acknowledge and release", exact: true });
const parse = (page) => page.getByRole("button", { name: "Parse Requirements", exact: true });
const fileInput = (page) => page.locator("#requirement-import-setup input[type=file]");
const comparison = (page) => page.getByRole("region", { name: "Compare incoming requirements", exact: true });
const revision = 1;

const previewFixture = () => ({
	import_id: "saved-booking-comparison",
	project_id: USE_CASE_PROJECT_ID,
	base_project_revision: revision,
	source_name: sourceName,
	operation: "requirements.parse",
	status: "pending",
	current_requirements: [],
	candidates: [
		{
			candidate_id: "candidate-1",
			requirement: { id: "BOOK-01", text: sourceText, review_status: "Needs Review" },
			classification: "new",
			suggestions: [],
			reason: "New source requirement.",
		},
	],
	recovery_snapshot_ids: [],
	suggested_update_scope: [],
	counts: {},
	warnings: [],
});

async function installExtraction(page, { firstParse = "success", initialStatus = 200, holdFirstStatus = false, dropRelease = false } = {}) {
	const initial = useCaseProjectFixture({ current_revision: revision, current_snapshots: {}, stage_state: {} });
	const other = useCaseProjectFixture({
		project_id: "separate-extraction-project",
		name: "Separate extraction project",
		current_revision: revision,
		current_snapshots: {},
		stage_state: {},
	});
	const writes = [];
	const attempts = [];
	const releases = [];
	const reads = [];
	const pageErrors = [];
	let pendingReads = 0;
	let attempt = null;
	let pending = null;
	let statusCode = initialStatus;
	const parseReply = createDeferred();
	const statusReply = createDeferred();
	page.on("pageerror", (error) => pageErrors.push(error.message));
	page.on("request", (request) => {
		if (["POST", "PATCH", "PUT", "DELETE"].includes(request.method()))
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		return route.fulfill({ status: 404, json: { detail: "Optional endpoint unavailable in this fixture." } });
	});
	await installWorkspaceApi(page, { projectDetails: { [initial.project_id]: initial, [other.project_id]: other } });
	await page.route(`**${statusPath}`, async (route) => {
		expect(route.request().method()).toBe("GET");
		reads.push(new URL(route.request().url()).pathname);
		if (holdFirstStatus) {
			await statusReply.promise;
			return route.fulfill({ json: { enabled: true, blocked: true, attempt } }).catch(() => {});
		}
		return route.fulfill({ status: statusCode, json: { enabled: true, blocked: Boolean(attempt), attempt } });
	});
	await page.route(`**${otherRoot}/requirement-imports/extraction-status`, (route) => {
		reads.push(new URL(route.request().url()).pathname);
		return route.fulfill({ json: { enabled: true, blocked: true, attempt: null } });
	});
	await page.route(`**${importsPath}`, (route) => {
		expect(route.request().method()).toBe("GET");
		pendingReads++;
		return route.fulfill({ json: pending ? [pending] : [] });
	});
	await page.route(`**${parsePath}`, async (route) => {
		const requestId = route.request().headers()["x-request-id"];
		expect(requestId).toBeTruthy();
		expect(route.request().postData()).toContain(sourceText);
		attempts.push(requestId);
		if (attempts.length === 1 && firstParse === "held") {
			attempt = {
				operation_id: operationId,
				fence: 7,
				operation: "requirements.parse",
				request_id: requestId,
				state: "running",
				source_snapshot_id: null,
				reason: null,
			};
			await parseReply.promise;
			return route.fulfill({
				status: 409,
				json: { detail: { code: "project_draft_recovery_required", message: "An earlier extraction ended." } },
			});
		}
		if (attempts.length === 1 && firstParse === "busy") {
			attempt = {
				operation_id: operationId,
				fence: 7,
				operation: "requirements.parse",
				request_id: "other-tabs-request",
				state: "running",
				source_snapshot_id: null,
				reason: null,
			};
			return route.fulfill({ status: 409, json: { detail: { code: "project_draft_busy", message: "Another extraction is active." } } });
		}
		pending = previewFixture();
		if (attempts.length === 1 && firstParse === "lost") return route.abort("connectionreset");
		return route.fulfill({ json: pending });
	});
	await page.route(`**${releasePath}`, (route) => {
		expect(route.request().method()).toBe("POST");
		expect(attempt.state).toBe("interrupted");
		const input = route.request().postDataJSON();
		releases.push(input);
		expect(input).toEqual({ fence: attempt.fence });
		attempt = null;
		return dropRelease ? route.abort("connectionreset") : route.fulfill({ json: { released: true } });
	});
	await seedAuthenticatedSession(page);
	return {
		writes,
		attempts,
		releases,
		reads,
		parseReply,
		statusReply,
		pageErrors,
		pendingReads: () => pendingReads,
		interrupt(operation = "requirements.parse") {
			attempt = {
				operation_id: operationId,
				fence: 7,
				request_id: attempts[0] || "prior-request",
				operation,
				state: "interrupted",
				source_snapshot_id: null,
				reason: "provider_outcome_unconfirmed",
			};
		},
		setStatusCode(value) {
			statusCode = value;
		},
		finishAttempt() {
			attempt = null;
		},
	};
}

test.describe("Requirement extraction admission", () => {
	test.skip(process.env.E2E_APPROVAL_CONTINUATION_ENABLED !== "true", "Requires VITE_APPROVAL_CONTINUATION_ENABLED=true");
	const sources = process.env.E2E_PROJECT_WORKSPACES_V2 === "true" ? "inputs" : "requirements";
	const selectFile = (page) =>
		fileInput(page).setInputFiles({ name: sourceName, mimeType: "text/markdown", buffer: Buffer.from(sourceText) });

	test("checks a running parse, releases only interruption, and preserves the selected file for a new deliberate request", async ({
		page,
	}, testInfo) => {
		const api = await installExtraction(page, { firstParse: "held", dropRelease: true });
		await page.goto(`${root}/${sources}`);
		await selectFile(page);
		await expect(parse(page)).toBeEnabled();
		expect(api.writes).toEqual([]);
		try {
			await parse(page).click();
			await expect.poll(() => api.attempts.length).toBe(1);
			await check(page).click();
			await expect(status(page)).toContainText("Requirement extraction is running.");
			await expect(release(page)).toHaveCount(0);
			api.interrupt();
			await check(page).click();
			await expect(status(page)).toContainText("Requirement extraction was interrupted. Provider usage remains unconfirmed.");
			await page.screenshot({ path: testInfo.outputPath("interrupted-extraction.png") });
			await release(page).click();
			await expect(status(page)).toContainText("The interrupted attempt was released. Provider usage remains unconfirmed.");
			await expect(parse(page)).toBeEnabled();
			expect(await fileInput(page).evaluate((input) => input.files[0]?.name)).toBe(sourceName);
			expect(api.writes).toEqual([`POST ${parsePath}`, `POST ${releasePath}`]);
			await parse(page).click();
			await expect(comparison(page)).toBeVisible();
			const oldResponse = page.waitForResponse((response) => response.url().endsWith(parsePath) && response.status() === 409);
			api.parseReply.resolve();
			await oldResponse;
			await expect(comparison(page)).toContainText(sourceText);
			await expect(page.getByText("An earlier extraction ended.")).toHaveCount(0);
			expect(new Set(api.attempts).size).toBe(2);
			expect(api.releases).toEqual([{ fence: 7 }]);
			expect(api.writes).toEqual([`POST ${parsePath}`, `POST ${releasePath}`, `POST ${parsePath}`]);
			expect(api.pageErrors).toEqual([]);
		} finally {
			api.parseReply.resolve();
		}
	});

	test("finds a saved comparison after a lost parse response without a second submission", async ({ page }) => {
		const api = await installExtraction(page, { firstParse: "lost" });
		await page.goto(`${root}/${sources}`);
		await selectFile(page);
		await parse(page).click();
		await expect(status(page)).toContainText("Could not confirm the requirements preview.");
		const previousReads = api.pendingReads();
		await check(page).click();
		await expect.poll(api.pendingReads).toBeGreaterThan(previousReads);
		await expect(comparison(page)).toHaveCount(0);
		await page.getByRole("button", { name: `Review import: ${sourceName}`, exact: true }).click();
		await expect(comparison(page)).toContainText(sourceText);
		expect(api.writes).toEqual([`POST ${parsePath}`]);
		expect(api.pageErrors).toEqual([]);
	});

	test("keeps a previously enabled interrupted refinement blocked after a 404 in legacy Upload", async ({ page }) => {
		const api = await installExtraction(page);
		api.interrupt("requirements.refine");
		await page.goto(`${root}/requirements`);
		await expect(status(page)).toContainText("Requirement refinement was interrupted.");
		await expect(release(page)).toBeEnabled();
		await selectFile(page);
		api.setStatusCode(404);
		await check(page).click();
		await expect(status(page).getByRole("alert")).toContainText("Could not check extraction status.");
		await expect(parse(page)).toBeDisabled();
		await expect(release(page)).toBeDisabled();
		expect(api.writes).toEqual([]);
	});

	test("keeps the same request identity after busy and only resubmits on a deliberate retry", async ({ page }) => {
		const api = await installExtraction(page, { firstParse: "busy" });
		await page.goto(`${root}/${sources}`);
		await selectFile(page);
		await parse(page).click();
		await expect(status(page)).toContainText("Requirement extraction is running.");
		await expect(parse(page)).toBeDisabled();
		expect(api.writes).toEqual([`POST ${parsePath}`]);
		api.finishAttempt();
		await check(page).click();
		await expect(parse(page)).toBeEnabled();
		expect(api.writes).toEqual([`POST ${parsePath}`]);
		await parse(page).click();
		await expect(comparison(page)).toContainText(sourceText);
		expect(api.attempts).toEqual([api.attempts[0], api.attempts[0]]);
		expect(api.writes).toEqual([`POST ${parsePath}`, `POST ${parsePath}`]);
	});

	test("preserves ordinary intake if the initial status route is unavailable on an older API", async ({ page }) => {
		const api = await installExtraction(page, { initialStatus: 404 });
		await page.goto(`${root}/${sources}`);
		await selectFile(page);
		await expect(parse(page)).toBeEnabled();
		await expect(status(page)).toHaveCount(0);
		expect(api.writes).toEqual([]);
		await parse(page).click();
		await expect(comparison(page)).toContainText(sourceText);
		expect(api.writes).toEqual([`POST ${parsePath}`]);
	});

	test("drops a late owner status on project navigation and hides recovery from a different account", async ({ page }) => {
		test.skip(process.env.E2E_PROJECT_WORKSPACES_V2 !== "true", "Uses Inputs workspace navigation");
		const api = await installExtraction(page, { holdFirstStatus: true });
		api.interrupt("requirements.refine");
		await page.goto(`${root}/inputs`);
		try {
			await expect.poll(() => api.reads).toContain(statusPath);
			await page.getByRole("button", { name: "Open QA project menu" }).click();
			await page.getByRole("button", { name: "Open QA project Separate extraction project", exact: true }).click();
			await page
				.getByRole("navigation", { name: "Project workspaces", exact: true })
				.getByRole("link", { name: "Inputs", exact: true })
				.click();
			await expect(page).toHaveURL(`${otherRoot}/inputs`);
			await expect(status(page)).toContainText("Another project workflow is active or awaiting recovery.");
			api.statusReply.resolve();
			await expect(status(page)).toContainText("Another project workflow is active or awaiting recovery.");
			await expect(release(page)).toHaveCount(0);
			const otherUser = { sub: "other-extraction-viewer", name: "Other extraction viewer", email: "other@example.test" };
			await seedAuthenticatedSession(page, otherUser);
			await page.route("**/auth/me", (route) => route.fulfill({ json: otherUser }));
			const priorReads = api.reads.length;
			await page.reload();
			await expect(page.getByRole("button", { name: `Open account menu for ${otherUser.name}`, exact: true })).toBeVisible();
			await expect(page.getByRole("heading", { name: "Inputs", exact: true })).toBeVisible();
			await expect(status(page)).toHaveCount(0);
			expect(api.reads).toHaveLength(priorReads);
			expect(api.writes).toEqual([]);
			expect(api.pageErrors).toEqual([]);
		} finally {
			api.statusReply.resolve();
		}
	});
});
