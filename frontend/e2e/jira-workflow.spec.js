import { expect, test } from "@playwright/test";

import { buildProjectPath } from "../src/app/workflowRoutes.js";
import { buildTestUser, seedAuthenticatedSession } from "./support/auth.js";
import { installWorkspaceApi, projectDetailFixture, workspaceProjectFixture, workspaceSummaryFixture } from "./support/workspace.js";

const PROJECT_ID = "jira-workflow-project";

function jsonResponse(route, payload, status = 200) {
	return route.fulfill({
		status,
		contentType: "application/json",
		body: JSON.stringify(payload),
	});
}

async function mockJiraWorkflow(page, user = buildTestUser()) {
	const state = {
		connected: false,
		previewPayloads: [],
		syncPayloads: [],
	};

	const connectionSummary = {
		base_url: "https://acme.atlassian.net",
		email: user.email,
		display_name: "Acme QA",
		api_token_hint: "••••c123",
	};
	const sharedIssue = {
		key: "PROJ-101",
		issue_id: "10001",
		summary: "Expense approvals epic",
		issue_type: "Epic",
		status: "In Progress",
		parent_key: null,
		issue_url: "https://acme.atlassian.net/browse/PROJ-101",
		description: "Original JIRA epic content",
		description_adf: {
			type: "doc",
			version: 1,
			content: [],
		},
		updated_at: "2026-04-22T09:30:00Z",
	};

	await page.route("**/auth/me", async (route) => jsonResponse(route, user));
	await page.route("**/reports/usage/me", async (route) => jsonResponse(route, { groups: [] }));
	await page.route("**/entitlements/me", async (route) =>
		jsonResponse(route, {
			account: {
				plan_tier: "premium",
				support_contact_email: "hello@spica-digital.eu",
			},
			requirements: {
				remaining: 500,
				exhausted: false,
			},
			test_cases: {
				remaining: 500,
				exhausted: false,
			},
			wallet: {
				balance_units: 5000,
				balance_token_display: "5000",
			},
			shadow_mode: false,
		})
	);

	await page.route("**/integrations/jira/connection", async (route) => {
		if (route.request().method() === "GET") {
			return jsonResponse(
				route,
				state.connected ? { connected: true, connection: connectionSummary } : { connected: false, connection: null }
			);
		}

		if (route.request().method() === "POST") {
			state.connected = true;
			return jsonResponse(route, { connected: true, connection: connectionSummary });
		}

		if (route.request().method() === "DELETE") {
			state.connected = false;
			return jsonResponse(route, { deleted: true, connected: false });
		}

		return jsonResponse(route, { detail: "Unsupported method" }, 405);
	});

	await page.route("**/integrations/jira/projects**", async (route) =>
		jsonResponse(route, {
			projects: [
				{
					project_id: "20001",
					key: "PROJ",
					name: "Platform Finance",
				},
			],
		})
	);

	await page.route("**/integrations/jira/issues/search**", async (route) =>
		jsonResponse(route, {
			issues: [sharedIssue],
		})
	);

	await page.route("**/integrations/jira/import", async (route) => {
		const body = route.request().postDataJSON();
		expect(body.epic_key).toBe("PROJ-101");
		expect(body.include_children).toBe(true);

		return jsonResponse(route, {
			source_name: sharedIssue.key,
			raw_text: "Imported from JIRA: expense approvals epic and child stories.",
			requirements: [
				{
					id: "REQ-101",
					text: "Employees can submit expense reports with receipts attached.",
					source_system: "jira",
					source_issue_key: sharedIssue.key,
					source_issue_type: sharedIssue.issue_type,
					source_parent_key: null,
					source_issue_url: sharedIssue.issue_url,
					source_issue_updated_at: sharedIssue.updated_at,
					sync_target_issue_key: sharedIssue.key,
					artifact_item_id: "jira:PROJ-101:REQ-101",
				},
				{
					id: "REQ-102",
					text: "Finance approvers can configure department-level approval thresholds.",
					source_system: "jira",
					source_issue_key: sharedIssue.key,
					source_issue_type: sharedIssue.issue_type,
					source_parent_key: null,
					source_issue_url: sharedIssue.issue_url,
					source_issue_updated_at: sharedIssue.updated_at,
					sync_target_issue_key: sharedIssue.key,
					artifact_item_id: "jira:PROJ-101:REQ-102",
				},
			],
			review: {
				approved: false,
				score: 7,
				threshold: 8,
				summary: "The import is solid but needs a clearer approvals rule.",
				blocking_issues: ["Clarify who receives approval notifications."],
			},
			coverage_metrics: {
				total_requirements: 2,
				unique_requirements: 2,
				duplicate_requirements: 0,
				shall_format_count: 1,
				requirements_per_document: 2,
			},
			workflow_diagnostics: {
				status: "completed",
				warnings: [],
				parser_failures: [],
			},
			iteration_history: [],
		});
	});

	await page.route("**/requirements/parse", async (route) => {
		expect(route.request().method()).toBe("POST");
		return jsonResponse(route, {
			raw_text: "Refined imported JIRA requirements.",
			requirements: [
				{
					id: "REQ-101",
					text: "Employees can submit expense reports with receipts attached and duplicate checks.",
					artifact_item_id: "jira:PROJ-101:REQ-101",
				},
				{
					id: "REQ-102",
					text: "Finance approvers can configure department-level approval thresholds and notification recipients.",
					artifact_item_id: "jira:PROJ-101:REQ-102",
				},
			],
			review: {
				approved: true,
				score: 9,
				threshold: 8,
				summary: "The requirements are now specific enough to sync back.",
				blocking_issues: [],
			},
			coverage_metrics: {
				total_requirements: 2,
				unique_requirements: 2,
				duplicate_requirements: 0,
				shall_format_count: 2,
				requirements_per_document: 2,
			},
			workflow_diagnostics: {
				status: "completed",
				warnings: [],
				parser_failures: [],
			},
			iteration_history: [],
		});
	});

	await page.route("**/integrations/jira/sync/preview", async (route) => {
		const payload = route.request().postDataJSON();
		state.previewPayloads.push(payload);

		return jsonResponse(route, {
			ready_issue_count: 1,
			conflict_count: 0,
			skipped_requirement_ids: [],
			warnings: [],
			issues: [
				{
					issue_key: sharedIssue.key,
					issue_type: sharedIssue.issue_type,
					status: "ready",
					requirement_ids: payload.requirements.map((requirement) => requirement.id),
					issue_url: sharedIssue.issue_url,
					existing_description_excerpt: "Current JIRA description with the previous managed block.",
					rendered_description_excerpt: `${payload.managed_section_title}\n- ${payload.requirements.map((requirement) => requirement.text).join("\n- ")}`,
					warning: null,
					conflict_reason: null,
				},
			],
		});
	});

	await page.route("**/integrations/jira/sync", async (route) => {
		const payload = route.request().postDataJSON();
		state.syncPayloads.push(payload);

		return jsonResponse(route, {
			updated_issue_count: 1,
			conflict_count: 0,
			results: [
				{
					issue_key: sharedIssue.key,
					status: "updated",
					message: "Managed requirements section updated.",
				},
			],
			requirements: payload.requirements.map((requirement) => ({
				...requirement,
				source_system: requirement.source_system || "jira",
				source_issue_key: requirement.source_issue_key || sharedIssue.key,
				source_issue_type: requirement.source_issue_type || sharedIssue.issue_type,
				source_parent_key: requirement.source_parent_key || null,
				source_issue_url: requirement.source_issue_url || sharedIssue.issue_url,
				source_issue_updated_at: "2026-04-22T10:30:00Z",
				sync_target_issue_key: requirement.sync_target_issue_key || sharedIssue.key,
			})),
		});
	});

	return state;
}

test.describe("JIRA requirements workflow", () => {
	test("authenticated user can connect, import, refine, preview, and sync JIRA requirements", async ({ page }) => {
		const user = buildTestUser();
		const workspaceProject = workspaceProjectFixture({
			project_id: PROJECT_ID,
			name: "JIRA Requirements Workspace",
			project_revision: 1,
			current_stage: "requirements",
			current_status: "ready",
		});
		await installWorkspaceApi(page, {
			summary: workspaceSummaryFixture({ projects: [workspaceProject] }),
			projectDetails: { [PROJECT_ID]: projectDetailFixture(workspaceProject) },
		});
		const jiraState = await mockJiraWorkflow(page, user);
		await seedAuthenticatedSession(page, user);

		await page.goto(buildProjectPath(PROJECT_ID, "requirements"));
		await expect(page.getByRole("button", { name: /open account menu/i })).toBeVisible({ timeout: 30_000 });

		await page.getByTestId("settings-open-button").click();
		await expect(page.getByRole("dialog", { name: /^settings$/i })).toBeVisible();
		await page.getByRole("button", { name: /integrations/i }).click();
		await expect(page.getByRole("button", { name: /integrations/i })).toHaveClass(/active/);
		await expect(page.getByRole("heading", { name: /^jira cloud$/i })).toBeVisible();

		await page.getByPlaceholder("https://your-team.atlassian.net").fill("https://acme.atlassian.net");
		await page.getByPlaceholder("qa@company.com").fill(user.email);
		await page.getByPlaceholder("Paste your Atlassian API token").fill("jira-api-token");
		await page.getByRole("button", { name: /connect jira/i }).click();

		await expect(
			page.locator(".settings-integration-card", { hasText: "JIRA Cloud" }).locator(".jira-status-badge.connected")
		).toBeVisible();
		await page.getByRole("button", { name: /close settings dialog/i }).click();
		await expect(page.getByRole("dialog", { name: /^settings$/i })).toBeHidden();

		await page.getByRole("radio", { name: /jira cloud/i }).check();
		await expect(page.getByRole("heading", { name: /import from jira/i })).toBeVisible();
		const projectSelect = page.locator(".jira-search-grid select").first();
		await expect(projectSelect).toContainText("PROJ — Platform Finance");
		await expect(projectSelect).toHaveValue("PROJ");

		await page.getByRole("button", { name: /^Search$/ }).click();
		await expect(page.getByRole("region", { name: "Jira issue search results table" })).toHaveAttribute("tabindex", "0");
		const jiraIssueOption = page.getByRole("radio", { name: /select jira issue proj-101/i });
		await expect(jiraIssueOption).toBeVisible();
		await jiraIssueOption.check();
		await page.getByRole("button", { name: /Import PROJ-101/i }).click();

		const requirementRows = page.locator(".requirement-review-table tbody tr");
		await expect(requirementRows).toHaveCount(2);
		await expect(requirementRows.nth(0)).toContainText("REQ-101");
		await expect(requirementRows.nth(1)).toContainText("REQ-102");
		await expect(page.getByRole("heading", { name: /jira sync preview/i })).toBeVisible();

		await page.getByPlaceholder(/Enter your feedback here/i).fill("Make the approval notifications explicit and tighten the language.");
		await page.getByRole("button", { name: /implement changes/i }).click();

		await expect(page.getByText(/duplicate checks/i)).toBeVisible();
		await expect(page.getByText(/notification recipients/i)).toBeVisible();
		await expect(requirementRows).toHaveCount(2);
		await expect(requirementRows.nth(0)).toContainText("PROJ-101");
		await expect(requirementRows.nth(1)).toContainText("PROJ-101");

		await page.getByRole("button", { name: /preview jira update/i }).click();
		await expect(page.getByText(/Ready 1/)).toBeVisible();
		await expect(page.locator(".jira-sync-preview-card.ready")).toContainText("PROJ-101");
		expect(jiraState.previewPayloads).toHaveLength(1);
		expect(jiraState.previewPayloads[0].managed_section_title).toBe("Agentic Requirements");
		expect(
			jiraState.previewPayloads[0].requirements.map((requirement) => requirement.source_issue_key || requirement.sync_target_issue_key)
		).toEqual(["PROJ-101", "PROJ-101"]);

		await page.getByRole("button", { name: /push ready updates/i }).click();
		await expect(page.getByText(/PROJ-101 — updated: Managed requirements section updated\./i)).toBeVisible();
		expect(jiraState.syncPayloads).toHaveLength(1);
		expect(
			jiraState.syncPayloads[0].requirements.map((requirement) => requirement.source_issue_key || requirement.sync_target_issue_key)
		).toEqual(["PROJ-101", "PROJ-101"]);
		await expect(page.locator(".jira-sync-preview-card.ready")).toContainText("Rendered update");
	});
});
