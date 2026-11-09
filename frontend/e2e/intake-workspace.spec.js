import { expect, test } from "@playwright/test";
import { seedAuthenticatedSession } from "./support/auth.js";
import { expectNoDocumentOverflow } from "./support/layout.js";
import { installUseCaseReviewApi, USE_CASE_PROJECT_ID, useCaseProjectFixture } from "./support/use-case-review.js";

const root = `/projects/${USE_CASE_PROJECT_ID}`;
const rows = [
	{ id: "REQ-101", text: "Customers can complete express checkout with a saved payment method." },
	{ id: "REQ-202", text: "High-value purchases require approval from a checkout supervisor." },
].map((requirement, index) => ({
	...requirement,
	requirement_uid: `checkout-${index + 1}`,
	review_status: "Needs Review",
	content_version: 1,
	lifecycle_status: "active",
	source_system: "file",
	sources: [
		{
			source_id: "checkout-doc",
			source_version: "v1",
			label: "pasted-requirements.md",
			original_requirement_ids: [`CHECKOUT-${index + 1}`],
			excerpt: requirement.text,
			excerpt_verified: true,
		},
	],
}));

const source = rows.map(({ text }) => text).join("\n");
const notes = "  Use a synthetic saved card.\nKeep the checkout account isolated.  ";
const target = "https://checkout.example.test";
const objective = "Prioritize confirmation behavior after a saved-card retry.";
const question = "What purchase amount requires supervisor approval?";
const reason = "The policy owner needs to confirm the threshold.";
const workspace = (page, name) => page.getByRole("navigation", { name: "Project workspaces" }).getByRole("link", { name, exact: true });
const heading = (page, name) => page.getByRole("heading", { name, exact: true });
const button = (page, name) => page.getByRole("button", { name, exact: true });
const notesInput = (page) => page.getByRole("textbox", { name: "Context notes", exact: true });
const comparison = (page) => page.getByRole("region", { name: "Compare incoming requirements", exact: true });

function saveSnapshot(project, stage, payload, { approved = false, stale = false } = {}) {
	const version = (project.current_snapshots[stage]?.version || 0) + 1;
	const snapshotId = `checkout-${stage}-${version}`;
	project.current_snapshots[stage] = { snapshot_id: snapshotId, stage, version, payload, approved };
	project.stage_state[stage] = { current_snapshot_id: snapshotId, version, approved, stale };
	return snapshotId;
}

async function fixture(page, { existing = false } = {}) {
	const initialProject = useCaseProjectFixture({ current_revision: 0, current_snapshots: {}, stage_state: {} });
	if (existing) {
		saveSnapshot(
			initialProject,
			"requirements",
			{ requirements: rows.map((row) => ({ ...row, review_status: "Approved" })) },
			{ approved: true }
		);
		initialProject.current_revision++;
	}
	const writes = [];
	const errors = [];
	const contextInputs = [];
	let pending = null;
	let finding = null;
	let imports = 0;
	page.on("pageerror", (error) => errors.push(error.message));
	page.on("console", (message) => {
		if (message.type() === "error" && /same key|unique.*key/i.test(message.text())) errors.push(message.text());
	});
	page.on("request", (request) => {
		if (["fetch", "xhr"].includes(request.resourceType()) && !["GET", "OPTIONS", "HEAD"].includes(request.method()))
			writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
	});
	await page.route("**/*", (route) => {
		if (!["fetch", "xhr"].includes(route.request().resourceType())) return route.fallback();
		if (new URL(route.request().url()).pathname.endsWith("/knowledge")) {
			return route.fulfill({ json: { entries: [], skills: [], features: {}, revision: "0" } });
		}
		return route.fulfill({ status: 404, json: { detail: "No fixture for this optional endpoint." } });
	});
	const api = await installUseCaseReviewApi(page, { initialProject });
	const update = (change) => {
		const project = api.getProject();
		project.current_revision++;
		change(project);
		api.setProject(project);
		return project;
	};
	await page.route(`**${root}/requirement-imports`, (route) => route.fulfill({ json: pending ? [pending] : [] }));
	await page.route("**/requirements/parse", (route) => {
		const request = route.request();
		expect(request.postData()).toContain('filename="pasted-requirements.md"');
		expect(request.postData()).toContain('name="import_mode"');
		for (const row of rows) expect(request.postData()).toContain(row.text);
		const project = api.getProject();
		const baseline = project.current_snapshots.requirements?.payload.requirements || [];
		pending = {
			import_id: `import-${++imports}`,
			project_id: project.project_id,
			base_project_revision: project.current_revision,
			source_name: "pasted-requirements.md",
			operation: "requirements.parse",
			status: "pending",
			current_requirements: baseline,
			candidates: rows.map((requirement, index) => ({
				candidate_id: `candidate-${index + 1}`,
				requirement,
				classification: baseline.length ? "unchanged" : "new",
				target_requirement_uid: baseline.length ? requirement.requirement_uid : null,
				suggestions: [],
				reason: baseline.length ? "Same exact source and content." : "New requirement.",
			})),
			recovery_snapshot_ids: [],
			suggested_update_scope: [],
			counts: {},
			warnings: [],
		};
		return route.fulfill({ json: pending });
	});
	await page.route(`**${root}/requirement-imports/*/apply`, (route) => {
		const request = route.request().postDataJSON();
		const existingRows = pending.current_requirements;
		expect(request.base_project_revision).toBe(api.getProject().current_revision);
		expect(request.decisions.map(({ action }) => action)).toEqual(existingRows.length ? ["keep", "keep"] : ["add", "add"]);
		expect(request.update_scope).toEqual([]);
		expect(request.confirm_retirements).toBe(false);
		const project = update((current) => {
			saveSnapshot(current, "requirements", {
				requirements: existingRows.length ? existingRows : rows,
				import_changes: { added: existingRows.length ? 0 : 2, updated: 0, retired: 0, active: 2 },
			});
			if (current.stage_state.context) current.stage_state.context.stale = true;
		});
		pending = null;
		return route.fulfill({ json: project });
	});
	await page.route("**/requirements/enrich", (route) => {
		const input = route.request().postDataJSON();
		contextInputs.push(input);
		expect(input).toMatchObject({
			project_id: USE_CASE_PROJECT_ID,
			base_project_revision: api.getProject().current_revision,
			app_link: target,
			notes,
			grounded_context: null,
		});
		if (contextInputs.length === 1) {
			return route.fulfill({ status: 503, json: { detail: { code: "context_unavailable", message: "Context service unavailable." } } });
		}
		const artifact = { id: "ART-APP-01", source_type: "app", label: "Application link", url: target, status: "Unavailable" };
		const payload = { ...input, grounded_context: { artifact_sources: [artifact], ui_elements: [], workflows: [] } };
		update((current) => saveSnapshot(current, "context", payload, { approved: true }));
		return route.fulfill({ json: payload });
	});
	await page.route(`**${root}/requirements/reviews`, (route) => {
		const { base_project_revision: revision, reviews } = route.request().postDataJSON();
		expect(revision).toBe(api.getProject().current_revision);
		expect(reviews).toEqual([{ requirement_uid: rows[0].requirement_uid, review_status: "Approved", quality_flags: [] }]);
		const project = update((current) => {
			const prior = current.current_snapshots.requirements;
			saveSnapshot(current, "requirements", {
				...prior.payload,
				requirements: prior.payload.requirements.map((row) =>
					row.requirement_uid === rows[0].requirement_uid ? { ...row, ...reviews[0] } : row
				),
			});
		});
		return route.fulfill({ json: project });
	});
	await page.route(`**${root}`, (route) => {
		if (route.request().method() !== "PATCH") return route.fallback();
		const input = route.request().postDataJSON();
		expect(input).toEqual({
			base_project_revision: api.getProject().current_revision,
			testing_intent: { objective, risk_emphasis: "edge_cases" },
		});
		return route.fulfill({
			json: update((current) => {
				current.testing_intent = input.testing_intent;
			}),
		});
	});
	const markScope = (current, status) => {
		const prior = current.current_snapshots.requirements;
		return saveSnapshot(current, "requirements", {
			...prior.payload,
			requirements: prior.payload.requirements.map((row) =>
				row.id === rows[1].id
					? { ...row, clarification_status: status, review_status: status === "excluded" ? "Rejected" : "Needs Review" }
					: row
			),
		});
	};
	await page.route(`**${root}/quality-findings`, (route) => {
		if (route.request().method() === "GET") {
			return route.fulfill({ json: { findings: finding ? [finding] : [], project_revision: api.getProject().current_revision } });
		}
		const input = route.request().postDataJSON();
		const original = api.getProject().current_snapshots.requirements.snapshot_id;
		expect(input).toMatchObject({
			base_project_revision: api.getProject().current_revision,
			source_snapshot_id: original,
			category: "ambiguous_requirement",
			summary: question,
			expected_behavior: "Unresolved: a source-backed business decision is required.",
			requirement_ids: [rows[1].id],
		});
		const project = update((current) => {
			finding = {
				...input,
				id: "threshold-question",
				status: "open",
				original_source_snapshot_id: original,
				source_snapshot_id: markScope(current, "open"),
			};
		});
		return route.fulfill({ json: { finding, project_revision: project.current_revision } });
	});
	await page.route(`**${root}/quality-findings/threshold-question`, (route) => {
		const input = route.request().postDataJSON();
		expect(input.base_project_revision).toBe(api.getProject().current_revision);
		expect(input.reason).toBe(reason);
		expect(["defer", "exclude"]).toContain(input.action);
		const status = input.action === "defer" ? "deferred" : "excluded";
		const project = update((current) => {
			finding = { ...finding, status, disposition_reason: reason, source_snapshot_id: markScope(current, status) };
		});
		return route.fulfill({ json: { finding, project_revision: project.current_revision } });
	});
	await seedAuthenticatedSession(page);
	return { ...api, writes, errors, contextInputs };
}

async function comparePastedSource(page) {
	const importToggle = button(page, "Import requirements");
	if (await importToggle.isVisible()) await importToggle.click();
	await page.getByRole("radio", { name: "Paste text", exact: true }).check();
	await page.getByLabel("Requirements text", { exact: true }).fill(source);
	await button(page, "Parse Requirements").click();
	await expect(comparison(page)).toBeVisible();
}

test.describe("Intake and scope in the 2.0 workspaces", () => {
	test.skip(process.env.E2E_PROJECT_WORKSPACES_V2 !== "true", "Requires VITE_PROJECT_WORKSPACES_V2=true on the frontend");

	test("pastes, compares and prepares context together, then records an explicit scope decision", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { getProject, writes, errors, contextInputs } = await fixture(page);
		await page.goto(`${root}/inputs`);
		await expect(heading(page, "Context Inputs")).toBeVisible();
		expect(writes).toEqual([]);

		await comparePastedSource(page);
		await expect(comparison(page)).toContainText("2 added");
		await workspace(page, "Review").press("Enter");
		const pendingNotice = page.getByRole("status").filter({ hasText: "A source comparison is open" });
		await expect(pendingNotice).toBeVisible();
		await expect(heading(page, "Requirement Review Workbench")).toHaveCount(0);
		await pendingNotice.getByRole("link", { name: "Inputs", exact: true }).press("Enter");
		await expect(comparison(page)).toBeVisible();
		expect(getProject().current_snapshots.requirements).toBeUndefined();
		await button(page, "Apply reviewed changes").click();
		await expect(comparison(page)).toHaveCount(0);
		await expect(button(page, "Review scope")).toBeFocused();
		await expect(page).toHaveURL(`${root}/inputs`);
		await expect(heading(page, "Requirement Review Workbench")).toHaveCount(0);

		await page.getByRole("textbox", { name: "Application link", exact: true }).fill(target);
		await notesInput(page).fill(notes);
		await page.reload();
		await expect(notesInput(page)).toHaveValue(notes);
		await expect(page.getByRole("status").filter({ hasText: "Unsaved context on this device." })).toBeVisible();
		await expect(page.getByRole("textbox", { name: "Application link", exact: true })).toHaveValue(target);
		expect(contextInputs).toHaveLength(0);
		await button(page, "Analyze Context").click();
		await expect(page.getByRole("alert").filter({ hasText: "Context service unavailable." })).toBeVisible();
		await expect(notesInput(page)).toHaveValue(notes);
		expect(getProject().current_snapshots.context).toBeUndefined();
		await button(page, "Analyze Context").click();
		await expect(heading(page, "Saved context evidence")).toBeVisible();
		expect(contextInputs).toHaveLength(2);
		expect(getProject().current_snapshots.context.payload).toMatchObject({ notes, grounded_context: { ui_elements: [] } });
		await expect(page.getByText(/app · Unavailable$/)).toBeVisible();
		await expect(page.getByRole("status").filter({ hasText: "Unsaved context on this device." })).toHaveCount(0);

		const intent = page.getByRole("region", { name: "Testing intent", exact: true });
		const objectiveInput = intent.getByRole("textbox", { name: "Testing objective", exact: true });
		const risk = intent.getByRole("combobox", { name: "Risk emphasis", exact: true });
		await objectiveInput.fill(objective);
		await risk.selectOption("edge_cases");
		await expect(intent).toContainText("Unsaved changes on this device.");
		await page.reload();
		await expect(objectiveInput).toHaveValue(objective);
		await expect(risk).toHaveValue("edge_cases");
		expect(getProject().testing_intent).toBeUndefined();
		await button(intent, "Save testing intent").click();
		await expect(intent).toContainText("Testing intent saved to the project.");
		const savedContext = getProject().current_snapshots.context;
		const savedContextState = getProject().stage_state.context;
		await expectNoDocumentOverflow(page, "Inputs");
		await page.screenshot({ path: testInfo.outputPath("inputs-context-and-intent.png"), fullPage: true });

		await button(page, "Review scope").press("Enter");
		await expect(page).toHaveURL(`${root}/review`);
		await expect(heading(page, "Requirement Review Workbench")).toBeVisible();
		await expect(notesInput(page)).toHaveCount(0);
		await expect(intent).toHaveCount(0);
		await expect(button(page, "Import requirements")).toHaveCount(0);
		expect(getProject().current_snapshots.requirements.payload.requirements.map(({ review_status }) => review_status)).toEqual([
			"Needs Review",
			"Needs Review",
		]);
		await page.getByRole("button", { name: /^Review REQ-101:/ }).click();
		await page.getByText("Source evidence (1)", { exact: true }).click();
		await expect(page.locator("blockquote").filter({ hasText: rows[0].text })).toBeVisible();
		await page.getByRole("combobox", { name: "Review status for REQ-101", exact: true }).selectOption("Approved");
		await expect(page.getByText(/1\/2 approved/)).toBeVisible();
		expect(getProject().current_snapshots.context).toEqual(savedContext);
		expect(getProject().stage_state.context).toEqual(savedContextState);

		await workspace(page, "Inputs").press("Enter");
		await expect(notesInput(page)).toHaveValue(notes);
		await comparePastedSource(page);
		await expect(comparison(page)).toContainText("Unchanged (2)");
		await button(page, "Apply reviewed changes").click();
		await expect(comparison(page)).toHaveCount(0);
		await workspace(page, "Review").press("Enter");
		await expect(page.getByText(/1\/2 approved/)).toBeVisible();
		await page.getByRole("button", { name: /^Review REQ-101:/ }).click();
		await page.getByText("Source evidence (1)", { exact: true }).click();
		await expect(page.getByText("Original IDs: CHECKOUT-1", { exact: true })).toBeVisible();
		await expect(page.locator("blockquote").filter({ hasText: rows[0].text })).toBeVisible();
		expect(writes).toEqual([
			"POST /requirements/parse",
			`POST ${root}/requirement-imports/import-1/apply`,
			"POST /requirements/enrich",
			"POST /requirements/enrich",
			`PATCH ${root}`,
			`PATCH ${root}/requirements/reviews`,
			"POST /requirements/parse",
			`POST ${root}/requirement-imports/import-2/apply`,
		]);
		expect(errors).toEqual([]);
	});

	test("a source-linked clarification removes only its affected requirement from eligible scope", async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { writes, errors } = await fixture(page, { existing: true });
		await page.goto(`${root}/review`);
		await expect(heading(page, "Requirement Review Workbench")).toBeVisible();
		const queue = page.getByRole("region", { name: "Business clarifications", exact: true });
		await expect(queue).toBeVisible();
		expect(writes).toEqual([]);
		await queue.getByText("Ask about a missing business rule", { exact: true }).click();
		await queue.getByLabel("Requirement needing clarification").selectOption(rows[1].id);
		await queue.getByLabel("What needs clarification?").fill(question);
		await button(queue, "Add clarification").click();
		await expect(queue).toContainText("REQ-202 · open");
		await queue.getByText("Source and decision history", { exact: true }).click();
		await expect(queue.locator("blockquote").filter({ hasText: rows[1].text })).toBeVisible();
		await expect(
			page.getByRole("status").filter({ hasText: /^1\/2 approved in scope · 1 open · 0 deferred · 0 excluded · 0 rejected$/ })
		).toHaveCount(1);
		await expect(button(page, "Review scenarios")).toBeDisabled();
		await page.getByRole("button", { name: /^Review REQ-202:/ }).click();
		await expect(
			page.getByRole("combobox", { name: "Review status for REQ-202", exact: true }).getByRole("option", { name: "Approved", exact: true })
		).toBeDisabled();
		const panel = page.getByRole("region", { name: "Shared project corrections", exact: true });
		for (const [action, status] of [
			["Defer", "deferred"],
			["Exclude", "excluded"],
		]) {
			await button(queue, "Answer REQ-202").click();
			await panel.getByText("Defer or exclude this scope", { exact: true }).click();
			await panel.getByLabel("Reason for the scope decision").fill(reason);
			await button(panel, `${action} affected scope`).click();
			await expect(queue).toContainText(`REQ-202 · ${status}`);
			const disposition = status === "deferred" ? "1 deferred · 0 excluded · 0 rejected" : "0 deferred · 1 excluded · 1 rejected";
			await expect(page.getByRole("status").filter({ hasText: `1/1 approved in scope · 0 open · ${disposition}` })).toHaveCount(1);
			await expect(button(page, "Review scenarios")).toBeEnabled();
		}
		await page.reload();
		await expect(queue).toContainText("REQ-202 · excluded");
		await expect(
			page.getByRole("status").filter({ hasText: "1/1 approved in scope · 0 open · 0 deferred · 1 excluded · 1 rejected" })
		).toHaveCount(1);
		await expect(page.getByText(/Loading project recommendations/)).toHaveCount(0);
		await queue.getByText("Source and decision history", { exact: true }).click();
		await expect(queue.getByText(reason, { exact: true })).toBeVisible();
		await expectNoDocumentOverflow(page, "Review scope and clarification");
		await page.screenshot({ path: testInfo.outputPath("review-scope-clarification.png"), fullPage: true });
		expect(writes).toEqual([
			`POST ${root}/quality-findings`,
			`POST ${root}/quality-findings/threshold-question`,
			`POST ${root}/quality-findings/threshold-question`,
		]);
		expect(errors).toEqual([]);
	});

	test("earlier context and testing intent drafts are visible for comparison and isolated by account", async ({ page }) => {
		const { getProject, setProject, writes, errors } = await fixture(page, { existing: true });
		const project = getProject();
		saveSnapshot(project, "context", { notes: "Original server context." }, { approved: true });
		setProject(project);
		await page.goto(`${root}/inputs`);
		await expect(notesInput(page)).toHaveValue("Original server context.");
		await notesInput(page).fill(notes);
		const intent = page.getByRole("region", { name: "Testing intent", exact: true });
		const objectiveInput = intent.getByRole("textbox", { name: "Testing objective", exact: true });
		await objectiveInput.fill(objective);
		await expect(page.getByRole("status").filter({ hasText: "Unsaved context on this device." })).toBeVisible();
		await expect(intent).toContainText("Unsaved changes on this device.");

		project.current_revision++;
		project.testing_intent = { objective: "Latest server objective.", risk_emphasis: "regression" };
		saveSnapshot(project, "context", { notes: "Latest server context." }, { approved: true });
		setProject(project);
		await page.reload();
		await expect(notesInput(page)).toHaveValue("Latest server context.");
		await expect(objectiveInput).toHaveValue("Latest server objective.");
		const earlierContext = page.getByText("Context draft from an earlier snapshot", { exact: true });
		const earlierIntent = intent.getByText("Draft from an earlier revision", { exact: true });
		await earlierContext.click();
		await expect(page.getByText(notes, { exact: true })).toBeVisible();
		await earlierIntent.click();
		await expect(intent.getByText(objective, { exact: true })).toBeVisible();

		const other = { sub: "another-intake-reviewer", name: "Another intake reviewer", email: "intake-reviewer@example.test" };
		await seedAuthenticatedSession(page, other);
		await page.route("**/auth/me", (route) => route.fulfill({ json: other }));
		await page.reload();
		await expect(notesInput(page)).toHaveValue("Latest server context.");
		await expect(objectiveInput).toHaveValue("Latest server objective.");
		await expect(earlierContext).toHaveCount(0);
		await expect(earlierIntent).toHaveCount(0);
		expect(writes).toEqual([]);
		expect(errors).toEqual([]);
	});
});
