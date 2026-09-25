import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { seedAuthenticatedSession } from "./support/auth.js";
import { installWorkspaceApi, projectDetailFixture, workspaceProjectFixture, workspaceSummaryFixture } from "./support/workspace.js";

const ID = "source-evidence";
const rows = [
	{
		id: "REQ-001",
		requirement_uid: "stable-room",
		text: "The system shall allow 1 to 8 attendees.",
		review_status: "Approved",
		content_version: 2,
		source_system: "file",
		quality_flags: [],
		sources: [
			{
				source_id: "document-one",
				source_version: "version-one",
				import_id: "import-one",
				label: "rooms.md",
				source_section: "Lines 6-6",
				original_requirement_ids: ["ROOM-101"],
				excerpt: "Attendees must be a whole number from 1 to 8.",
				excerpt_verified: true,
			},
			{
				source_id: "document-two",
				source_version: "version-two",
				import_id: "import-two",
				label: "policy.md",
				source_section: "Lines 3-3",
				original_requirement_ids: ["BOOK-205"],
				excerpt: "A reservation supports eight attendees.",
				excerpt_verified: true,
			},
		],
	},
	{
		id: "REQ-002",
		requirement_uid: "stable-legacy",
		text: "The system shall retain the legacy booking behavior.",
		review_status: "Needs Review",
		source_system: "file",
		quality_flags: [],
		sources: [
			{
				source_id: "old-document",
				source_version: "old-version",
				label: "old.md",
				original_requirement_ids: ["UNVERIFIED-999"],
				excerpt: "This reconstructed quotation must not be displayed as source evidence.",
				excerpt_verified: "false",
			},
		],
	},
];

async function setup(page) {
	await seedAuthenticatedSession(page);
	const summary = workspaceProjectFixture({ project_id: ID, name: "Room source mapping", project_revision: 3 });
	const project = projectDetailFixture(summary, {
		current_revision: 3,
		stage_state: { requirements: { version: 2, approved: false, current_snapshot_id: "source-snapshot" } },
		current_snapshots: { requirements: { snapshot_id: "source-snapshot", payload: { source_name: "rooms.md", requirements: rows } } },
	});
	await installWorkspaceApi(page, { summary: workspaceSummaryFixture({ projects: [summary] }), projectDetails: { [ID]: project } });
	await page.goto(`/projects/${ID}/requirements`);
	await expect(page.getByRole("heading", { name: "Requirement Review Workbench" })).toBeVisible();
}

test("verified source details and downloaded mapping preserve original and normalized identities", { tag: "@p1" }, async ({ page }) => {
	await setup(page);
	await page.getByText("Source evidence (2)", { exact: true }).click();
	await expect(page.getByText("Original IDs: ROOM-101", { exact: true })).toBeVisible();
	await expect(page.getByText("Original IDs: BOOK-205", { exact: true })).toBeVisible();
	await expect(page.locator("blockquote").filter({ hasText: "Attendees must be a whole number from 1 to 8." })).toBeVisible();
	await expect(page.getByText("Source version: version-one", { exact: true })).toBeVisible();
	await page.getByText("Source evidence (1)", { exact: true }).click();
	await expect(page.getByText(/Source excerpt unavailable/)).toBeVisible();
	await expect(page.getByText("UNVERIFIED-999", { exact: false })).toHaveCount(0);
	await expect(page.getByText("This reconstructed quotation", { exact: false })).toHaveCount(0);
	const downloaded = page.waitForEvent("download");
	await page.getByRole("button", { name: "Download source mapping (JSON)" }).click();
	const download = await downloaded;
	const mapping = JSON.parse(await readFile(await download.path(), "utf8"));
	expect(mapping.format).toBe("requirement_source_mapping_v1");
	expect(mapping.requirements[0].normalized_id).toBe("REQ-001");
	expect(mapping.requirements[0].requirement_uid).toBe("stable-room");
	expect(mapping.requirements[0].sources.map((source) => source.original_requirement_ids)).toEqual([["ROOM-101"], ["BOOK-205"]]);
	expect(mapping.requirements[0].sources[0].source_version).toBe("version-one");
	expect(mapping.requirements[1].sources[0].excerpt).toBeNull();
	expect(mapping.requirements[1].sources[0].original_requirement_ids).toEqual([]);
});

test("source evidence remains accessible on a narrow screen", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await setup(page);
	await page.getByText("Source evidence (2)", { exact: true }).click();
	await expect(page.getByText("Original IDs: ROOM-101", { exact: true })).toBeVisible();
	await expect(page.getByRole("button", { name: "Download source mapping (JSON)" })).toBeVisible();
	const width = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth }));
	expect(width.document).toBeLessThanOrEqual(width.viewport + 2);
	const results = await new AxeBuilder({ page }).include(".requirement-review-workbench").analyze();
	expect(results.violations.filter((item) => ["serious", "critical"].includes(item.impact))).toEqual([]);
});
