import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installWorkspaceApi, projectDetailFixture, workspaceProjectFixture, workspaceSummaryFixture } from "./support/workspace.js";

const ID = "requirement-import-review";
const when = "2026-09-12T06:00:00Z";
const originals = Array.from({ length: 8 }, (_, i) => ({
	id: `REQ-${String(i + 1).padStart(3, "0")}`,
	requirement_uid: `original-${i + 1}`,
	text: `The system shall preserve task behavior ${i + 1}.`,
	review_status: "Approved",
	source_system: "file",
	source_path: "audit-requirements.md",
	content_version: 1,
	lifecycle_status: "active",
	quality_flags: [],
	sources: [
		{
			source_id: "document",
			source_version: "v1",
			import_id: "original",
			label: "audit-requirements.md",
			excerpt: `Task behavior ${i + 1}`,
		},
	],
}));
const incoming = ["The agricultural portal displays PDF resources.", "The reader presents hierarchical category navigation."].map(
	(text, i) => ({
		candidate_id: `incoming-${i + 1}`,
		requirement: { id: `REQ-00${i + 1}`, text, source_system: "jira", source_issue_key: "TX-6" },
		classification: "new",
		target_requirement_uid: null,
		reason: "No matching requirement found; add to the project.",
		suggestions: [],
	})
);

async function setup(page, { candidates = incoming, revision = 7, failedApply = false, pendingOnLoad = false } = {}) {
	await seedAuthenticatedSession(page);
	const summary = workspaceProjectFixture({ project_id: ID, name: "Audit project", project_revision: revision });
	let project = projectDetailFixture(summary, {
		current_revision: revision,
		stage_state: {
			requirements: { version: 1, approved: true, current_snapshot_id: "original" },
			test_cases: { version: 1, approved: true, current_snapshot_id: "tests", stale: false },
		},
		current_snapshots: {
			requirements: { snapshot_id: "original", payload: { requirements: structuredClone(originals) } },
			test_cases: {
				snapshot_id: "tests",
				payload: { test_cases: [{ id: "TC-001", title: "Existing task test", linked_requirement_ids: ["REQ-001"], steps: [] }] },
			},
		},
	});
	const preview = {
		import_id: "import-1",
		project_id: ID,
		base_project_revision: 7,
		source_name: "TX-6",
		operation: "requirements.import.jira",
		status: "pending",
		created_at: when,
		current_requirements: structuredClone(originals),
		candidates: structuredClone(candidates),
		suggested_update_scope: [],
		counts: {},
		warnings: [],
		recovery_snapshot_ids: [],
	};
	let pending = pendingOnLoad ? [preview] : [];
	const applies = [];
	const guidanceReads = [];
	const summaryReads = [];
	await installWorkspaceApi(page, { summary: workspaceSummaryFixture({ projects: [summary] }), projectDetails: { [ID]: project } });
	await page.route(`**/projects/${ID}/orchestrator/status`, (route) => {
		guidanceReads.push(project.current_revision);
		const pending = project.current_snapshots.requirements.payload.requirements.some((r) => r.review_status !== "Approved");
		return route.fulfill({
			json: {
				project_id: ID,
				project_revision: project.current_revision,
				stages: {},
				next_actions: pending
					? [
							{
								action: "approve",
								stage: "requirements",
								label: "Review Requirements",
								primary: true,
								enabled: true,
								reason: "Review incoming requirements before applying changes.",
							},
						]
					: [],
			},
		});
	});
	await page.route("**/workspace/summary?*", (route) => {
		summaryReads.push(project.current_revision);
		return route.fulfill({ json: workspaceSummaryFixture({ projects: [{ ...summary, project_revision: project.current_revision }] }) });
	});

	await page.route(`**/projects/${ID}`, (route) => route.fulfill({ json: project }));
	await page.route(`**/projects/${ID}/requirement-imports`, (route) => route.fulfill({ json: pending }));
	await page.route("**/requirements/parse", (route) => {
		expect(route.request().postData()).toContain('name="import_mode"');
		expect(route.request().postData()).toContain("review");
		pending = [preview];
		return route.fulfill({ json: preview });
	});
	await page.route(`**/projects/${ID}/requirement-imports/import-1`, (route) => {
		pending = [];
		return route.fulfill({ status: 204 });
	});
	await page.route(`**/projects/${ID}/requirement-imports/import-1/apply`, (route) => {
		const payload = route.request().postDataJSON();
		applies.push(payload);
		if (failedApply && applies.length === 1)
			return route.fulfill({ status: 503, json: { detail: "Temporary apply failure. Retry the same reviewed changes." } });
		let rows = structuredClone(originals);
		const targeted = new Set();
		let number = 9;
		const counts = { added: 0, updated: 0, retired: 0 };
		for (const choice of payload.decisions) {
			const c = candidates.find((item) => item.candidate_id === choice.candidate_id);
			if (choice.action === "add") {
				rows.push({
					...c.requirement,
					id: `REQ-${String(number++).padStart(3, "0")}`,
					requirement_uid: c.candidate_id,
					review_status: "Needs Review",
					content_version: 1,
				});
				counts.added++;
			}
			if (["update", "keep"].includes(choice.action)) {
				targeted.add(choice.target_requirement_uid);
				rows = rows.map((r) =>
					r.requirement_uid === choice.target_requirement_uid
						? {
								...r,
								text: c.requirement.text,
								review_status: choice.action === "update" ? "Needs Review" : r.review_status,
								content_version: choice.action === "update" ? 2 : 1,
								sources: [...r.sources, { source_id: "jira", source_version: "v1", label: "TX-6", excerpt: c.requirement.text }],
							}
						: r
				);
				if (choice.action === "update") counts.updated++;
			}
		}
		const retired = rows
			.filter((r) => payload.update_scope.includes(r.requirement_uid) && !targeted.has(r.requirement_uid))
			.map((r) => ({ ...r, lifecycle_status: "retired" }));
		rows = rows.filter((r) => !retired.some((old) => old.requirement_uid === r.requirement_uid));
		counts.retired = retired.length;
		counts.active = rows.length;
		project = {
			...project,
			current_revision: 8,
			stage_state: {
				...project.stage_state,
				requirements: {
					...project.stage_state.requirements,
					current_snapshot_id: "combined",
					approved: rows.every((r) => r.review_status === "Approved"),
				},
				test_cases: { ...project.stage_state.test_cases, stale: true },
			},
			current_snapshots: {
				...project.current_snapshots,
				requirements: { snapshot_id: "combined", payload: { requirements: rows, retired_requirements: retired, import_changes: counts } },
			},
		};
		pending = [];
		return route.fulfill({ json: project });
	});
	await page.goto(`/projects/${ID}/requirements`);
	await expect(page.getByRole("heading", { name: "Requirement Review Workbench" })).toBeVisible();
	return { applies, preview, guidanceReads, summaryReads, getProject: () => project };
}

async function stage(page) {
	await page
		.locator('input[type="file"]')
		.setInputFiles({ name: "new.md", mimeType: "text/markdown", buffer: Buffer.from("Incoming requirements") });
	await page.getByRole("button", { name: "Parse Requirements", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Compare incoming requirements" })).toBeFocused();
}
const workbench = (page) => page.getByRole("region", { name: "Project requirements table", exact: true });

test("stages eight plus two, applies ten, and retains IDs and approvals after reload", async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	await expect(workbench(page).getByRole("row")).toHaveCount(9);
	await expect(page.getByText("2 added · 0 updated · 0 retired · 10 active requirements", { exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(workbench(page).getByRole("row")).toHaveCount(11);
	await expect(page.getByLabel("Review status for REQ-001", { exact: true })).toHaveValue("Approved");
	await expect(page.getByLabel("Review status for REQ-009", { exact: true })).toHaveValue("Needs Review");
	expect(state.applies[0].update_scope).toEqual([]);
	expect(state.getProject().stage_state.test_cases.stale).toBeTruthy();
	expect(state.getProject().current_snapshots.test_cases.payload.test_cases).toHaveLength(1);
	await expect.poll(() => state.guidanceReads.includes(8) && state.summaryReads.includes(8)).toBe(true);
	await page.getByRole("link", { name: "Overview", exact: true }).click();
	await expect(page.getByLabel("Contextual task").getByRole("heading", { name: "Review Requirements" })).toBeVisible();
	await page.getByRole("button", { name: "Open Requirements", exact: true }).click();

	await page.reload();
	await expect(workbench(page).getByRole("row")).toHaveCount(11);
});

test("cross-source update requires resolving ambiguity and retains requirement ID", async ({ page }) => {
	const candidates = [
		{
			...incoming[0],
			classification: "needs_decision",
			reason: "Possible cross-source revision",
			suggestions: [{ requirement_uid: "original-1", reason: "Same behavior with a changed constraint" }],
		},
	];
	await setup(page, { candidates });
	await stage(page);
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeDisabled();
	await page.getByLabel("Decision for incoming-1").selectOption("original-1");
	await expect(page.getByText("0 added · 1 updated · 0 retired · 8 active requirements", { exact: true })).toBeVisible();
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(page.getByLabel("Review status for REQ-001", { exact: true })).toHaveValue("Needs Review");
	await expect(workbench(page).getByRole("row")).toHaveCount(9);
});

test("retirement is confined to selected scope and requires confirming the list", async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	await page.getByText("Replacement scope (0 existing requirements)", { exact: true }).click();
	const scope = page.locator(".import-scope");
	await scope.getByRole("checkbox").nth(0).check();
	await scope.getByRole("checkbox").nth(1).check();
	await expect(page.getByRole("heading", { name: "Retiring (2)" })).toBeVisible();
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeDisabled();
	await page.getByRole("checkbox", { name: /Retire these 2 requirements/ }).check();
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(page.getByText("Retired requirements (2)", { exact: true })).toBeVisible();
	await expect(page.getByLabel("Review status for REQ-003", { exact: true })).toHaveValue("Approved");
	expect(state.applies[0].update_scope).toEqual(["original-1", "original-2"]);
});

test("cancel preserves baseline and removes pending import", async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	await page.getByRole("button", { name: "Cancel import" }).click();
	await expect(page.getByRole("heading", { name: "Compare incoming requirements" })).not.toBeVisible();
	await expect(workbench(page).getByRole("row")).toHaveCount(9);
	expect(state.applies).toHaveLength(0);
	await page.reload();
	await expect(page.getByRole("button", { name: /^Review import:/ })).toHaveCount(0);
});

test("failed apply keeps comparison and reuses retry identity", async ({ page }) => {
	const state = await setup(page, { failedApply: true });
	await stage(page);
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(page.getByRole("alert")).toContainText("Temporary apply failure");
	await expect(workbench(page).getByRole("row")).toHaveCount(9);
	await page.getByRole("button", { name: "Apply reviewed changes" }).click();
	await expect(workbench(page).getByRole("row")).toHaveCount(11);
	expect(state.applies[0].idempotency_key).toEqual(state.applies[1].idempotency_key);
});

test("stale saved previews cannot apply", async ({ page }) => {
	await setup(page, { revision: 8, pendingOnLoad: true });
	await page.getByRole("button", { name: "Review import: TX-6" }).click();
	await expect(page.getByRole("alert")).toContainText("Project changed");
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeDisabled();
});

test("renewed comparison refreshes the baseline revision after a conflict", async ({ page }) => {
	const state = await setup(page);
	await stage(page);
	await page.route(`**/projects/${ID}/requirement-imports/import-1/compare`, (route) => {
		state.getProject().current_revision = 8;
		return route.fulfill({ json: { ...state.preview, import_id: "import-2", base_project_revision: 8 } });
	});
	await page.getByRole("button", { name: "Compare again" }).click();
	await expect(page.getByText("TX-6 · Comparing with project revision 8", { exact: true })).toBeVisible();
	await expect(page.getByRole("button", { name: "Apply reviewed changes" })).toBeEnabled();
	await expect(page.getByText("Project changed after this comparison. Compare again before applying.", { exact: true })).toHaveCount(0);
});

test("comparison works on narrow screens and passes focused accessibility checks", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await setup(page);
	await stage(page);
	const comparison = page.getByRole("region", { name: "Compare incoming requirements" });
	const box = await comparison.boundingBox();
	expect(box.width).toBeLessThanOrEqual(390);
	await page.getByLabel("Filter import changes").selectOption("new");
	const results = await new AxeBuilder({ page }).include(".requirement-import-review").analyze();
	expect(results.violations).toEqual([]);
});
