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

const connectors = {
	jira: {
		slug: "jira",
		operation: "requirements.import.jira",
		label: "JIRA Cloud",
		item: "QA-416",
		selection: "Select JIRA issue QA-416",
		results: "Jira issue search results table",
		searchPath: "/integrations/jira/issues/search",
		input: { epic_key: "QA-416" },
		routes: {
			connection: { connected: true, connection: { site_url: "https://jira.example.test" } },
			"projects?*": { projects: [{ project_id: "123", key: "QA", name: "QA Project" }] },
			"projects/*/issue-types": { issue_types: [{ name: "Epic" }] },
			"issues/search?*": { issues: [{ key: "QA-416", issue_type: "Epic", summary: "Synthetic Jira source" }] },
		},
	},
	azure: {
		slug: "azure-devops",
		operation: "requirements.import.azure_devops",
		label: "Azure DevOps",
		item: "#416",
		selection: "Select Azure DevOps work item 416",
		results: "Azure DevOps work item search results table",
		searchPath: "/integrations/azure-devops/work-items/search",
		input: { project: "QA Project", work_item_id: 416 },
		routes: {
			connection: { connected: true, connection: { default_project: "QA Project" } },
			"projects?*": { projects: [{ project_id: "123", name: "QA Project" }] },
			"projects/*/work-item-types": { work_item_types: [{ name: "Feature" }] },
			"work-items/search?*": { work_items: [{ work_item_id: 416, work_item_type: "Feature", title: "Synthetic Azure DevOps source" }] },
		},
	},
};
const importPath = (connector) => `/integrations/${connector.slug}/import`;
const sourceSearch = (page) => page.locator("#requirement-import-setup").getByRole("button", { name: "Search", exact: true });

const previewFixture = (source = sourceName, operation = "requirements.parse") => ({
	import_id: "saved-booking-comparison",
	project_id: USE_CASE_PROJECT_ID,
	base_project_revision: revision,
	source_name: source,
	operation,
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

async function installExtraction(
	page,
	{ firstParse = "success", initialStatus = 200, holdFirstStatus = false, dropRelease = false, connector } = {}
) {
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
	const connectorRequests = [];
	const releases = [];
	const reads = [];
	const pageErrors = [];
	let pendingReads = 0;
	let attempt = null;
	let pending = null;
	let statusCode = initialStatus;
	const operation = connector?.operation || "requirements.parse";
	const attemptFor = (request_id, state) => ({
		operation_id: operationId,
		fence: 7,
		operation,
		request_id,
		state,
		source_snapshot_id: null,
		reason: state === "interrupted" ? "provider_outcome_unconfirmed" : null,
	});
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
	if (connector) {
		for (const [endpoint, payload] of Object.entries(connector.routes)) {
			await page.route(`**/integrations/${connector.slug}/${endpoint}`, (route) => route.fulfill({ json: payload }));
		}
	}
	await page.route(`**${connector ? importPath(connector) : parsePath}`, async (route) => {
		const requestId = route.request().headers()["x-request-id"];
		expect(requestId).toBeTruthy();
		if (connector) {
			const input = route.request().postDataJSON();
			expect(input).toMatchObject({
				...connector.input,
				project_id: USE_CASE_PROJECT_ID,
				base_project_revision: revision,
				import_mode: "review",
				include_children: true,
			});
			connectorRequests.push(input);
		} else {
			expect(route.request().postData()).toContain(sourceText);
		}
		attempts.push(requestId);
		if (attempts.length === 1 && firstParse === "interrupted") {
			attempt = attemptFor(requestId, "interrupted");
			return route.abort("connectionreset");
		}
		if (attempts.length === 1 && firstParse === "held") {
			attempt = attemptFor(requestId, "running");
			await parseReply.promise;
			return route.fulfill({
				status: 409,
				json: { detail: { code: "project_draft_recovery_required", message: "An earlier extraction ended." } },
			});
		}
		if (attempts.length === 1 && firstParse === "busy") {
			attempt = attemptFor("other-tabs-request", "running");
			return route.fulfill({ status: 409, json: { detail: { code: "project_draft_busy", message: "Another extraction is active." } } });
		}
		pending = previewFixture(connector?.item, operation);
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
		connectorRequests,
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
		startOtherAttempt() {
			attempt = attemptFor("other-tabs-request", "running");
		},
	};
}

test.describe("Requirement extraction admission", () => {
	test.skip(process.env.E2E_APPROVAL_CONTINUATION_ENABLED !== "true", "Requires VITE_APPROVAL_CONTINUATION_ENABLED=true");
	const sources = process.env.E2E_PROJECT_WORKSPACES_V2 === "true" ? "inputs" : "requirements";
	const selectFile = (page) =>
		fileInput(page).setInputFiles({ name: sourceName, mimeType: "text/markdown", buffer: Buffer.from(sourceText) });
	const selectConnector = async (page, connector) => {
		await page.getByRole("radio", { name: connector.label, exact: true }).check();
		await sourceSearch(page).click();
		await expect(page.getByRole("radio", { name: connector.selection, exact: true })).toBeChecked();
	};
	const checkConnectorSearchWithResults = async (page, connector) => {
		const results = page.getByRole("region", { name: connector.results, exact: true });
		for (const width of [1366, 1440]) {
			await page.setViewportSize({ width, height: 900 });
			if (process.env.E2E_PROJECT_WORKSPACES_V2 === "true") {
				const geometry = await results.evaluate((table) => ({
					resultsRight: table.getBoundingClientRect().right,
					cardRight: table.closest(".jira-card").getBoundingClientRect().right,
					contextLeft: document.querySelector(".project-intake-context").getBoundingClientRect().left,
					internalOverflow: table.scrollWidth - table.clientWidth,
					documentOverflow: document.documentElement.scrollWidth - window.innerWidth,
				}));
				expect(geometry.resultsRight).toBeLessThanOrEqual(geometry.cardRight);
				expect(geometry.cardRight).toBeLessThan(geometry.contextLeft);
				expect(geometry.internalOverflow).toBeGreaterThan(0);
				expect(geometry.documentOverflow).toBeLessThanOrEqual(0);
			}
			const searched = page.waitForRequest(
				(request) => request.method() === "GET" && new URL(request.url()).pathname === connector.searchPath
			);
			await sourceSearch(page).click();
			await searched;
			await expect(page.getByRole("radio", { name: connector.selection, exact: true })).toBeChecked();
		}
	};

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
			await expect(parse(page)).toBeDisabled();
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

	test("recovers a dropped Jira import and release without resubmitting or losing the selected issue", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const connector = connectors.jira;
		const api = await installExtraction(page, { connector, firstParse: "interrupted", dropRelease: true });
		const importSource = page.getByRole("button", { name: `Import ${connector.item}`, exact: true });
		const selection = page.getByRole("radio", { name: connector.selection, exact: true });
		await page.goto(`${root}/${sources}`);
		await selectConnector(page, connector);
		expect(api.writes).toEqual([]);
		await importSource.click();
		await expect(status(page)).toContainText("Jira requirements import was interrupted. Provider usage remains unconfirmed.");
		await expect(importSource).toBeDisabled();
		await checkConnectorSearchWithResults(page, connector);
		await expect(selection).toBeChecked();
		expect(api.writes).toEqual([`POST ${importPath(connector)}`]);
		await page.screenshot({ path: testInfo.outputPath("jira-requirements-import-interrupted.png"), fullPage: true });
		await release(page).click();
		await expect(status(page)).toContainText("The interrupted attempt was released. Provider usage remains unconfirmed.");
		await expect(selection).toBeChecked();
		await expect(importSource).toBeEnabled();
		expect(api.releases).toEqual([{ fence: 7 }]);
		expect(api.writes).toEqual([`POST ${importPath(connector)}`, `POST ${releasePath}`]);
		await importSource.click();
		await expect(comparison(page)).toContainText(sourceText);
		expect(new Set(api.attempts).size).toBe(2);
		expect(api.connectorRequests).toEqual([api.connectorRequests[0], api.connectorRequests[0]]);
		expect(api.writes).toEqual([`POST ${importPath(connector)}`, `POST ${releasePath}`, `POST ${importPath(connector)}`]);
		expect(api.pageErrors).toEqual([]);
	});

	test("gates only Azure DevOps import while another import is running", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const connector = connectors.azure;
		const api = await installExtraction(page, { connector });
		api.startOtherAttempt();
		const importSource = page.getByRole("button", { name: `Import ${connector.item}`, exact: true });
		await page.goto(`${root}/${sources}`);
		await expect(status(page)).toContainText("Azure DevOps requirements import is running.");
		await selectConnector(page, connector);
		await expect(importSource).toBeDisabled();
		await checkConnectorSearchWithResults(page, connector);
		await expect(release(page)).toHaveCount(0);
		expect(api.writes).toEqual([]);
		await page.screenshot({ path: testInfo.outputPath("azure-devops-requirements-import-running.png"), fullPage: true });
		api.finishAttempt();
		await check(page).click();
		await expect(importSource).toBeEnabled();
		expect(api.writes).toEqual([]);
		await importSource.click();
		await expect(comparison(page)).toContainText(sourceText);
		expect(api.writes).toEqual([`POST ${importPath(connector)}`]);
		expect(api.pageErrors).toEqual([]);
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
