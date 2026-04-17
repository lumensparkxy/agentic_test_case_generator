import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { sampleRequirementsFile, seedAuthenticatedSession } from "./support/auth.js";

const allowedPriorities = new Set(["Critical", "High", "Medium", "Low"]);
const allowedTypes = new Set([
	"Functional",
	"Integration",
	"E2E",
	"Regression",
	"Smoke",
	"Security",
	"Performance",
	"Usability",
	"UAT",
]);

const minimumStructuredCaseRatio = 0.8;

async function openGenerateTab(page) {
	await page.goto("/");
	await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible({ timeout: 30_000 });

	await page.locator('input[type="file"]').setInputFiles(sampleRequirementsFile);
	await page.getByRole("button", { name: /parse requirements/i }).click();

	await expect
		.poll(async () => page.locator(".requirements-list li").count(), {
			timeout: 120_000,
			message: "Expected parsed requirements to appear after uploading the sample file.",
		})
		.toBeGreaterThan(0);

	await expect(page.getByRole("heading", { name: /requirement workflow diagnostics/i })).toBeVisible();

	await page.getByRole("button", { name: /^Next$/ }).click();
	await page.locator('input[placeholder="https://your-app"]').fill("https://example.com/app");
	await page.getByRole("button", { name: /analyze context/i }).click();

	await expect(page.getByRole("heading", { name: /grounded context/i })).toBeVisible({ timeout: 120_000 });
	await expect
		.poll(async () => page.locator(".artifact-source-item").count(), {
			timeout: 30_000,
			message: "Expected analyzed context artifacts to appear in the Context tab.",
		})
		.toBeGreaterThan(0);

	await page.getByRole("button", { name: /^Next$/ }).click();
	await page.getByRole("button", { name: /^Next$/ }).click();
}

test.describe("Agentic Test Case Generator E2E", () => {
	test("anonymous portal opens provider chooser from sign-in", async ({ page }) => {
		await page.goto("/");
		await expect(page.getByRole("button", { name: /^sign in$/i })).toBeVisible();

		await page.getByRole("button", { name: /^sign in$/i }).click();
		await expect(page.getByRole("dialog", { name: /choose a sign-in method/i })).toBeVisible();
		await expect(page.getByRole("button", { name: /google/i })).toBeVisible();
		await expect(page.getByRole("button", { name: /microsoft/i })).toBeVisible();
		await expect(page.getByRole("button", { name: /apple/i })).toBeVisible();
	});

	test("authenticated user can parse, generate, and export high-quality test cases", async ({ page }) => {
		await seedAuthenticatedSession(page);
		await openGenerateTab(page);

		await page.getByRole("button", { name: /generate test cases/i }).click();

		await expect
			.poll(async () => {
				const tableRows = await page.locator(".test-cases-table tbody tr").count();
				const cards = await page.locator(".case-card").count();
				return tableRows + cards;
			}, {
				timeout: 360_000,
				message: "Expected generated test cases to appear in the UI.",
			})
			.toBeGreaterThan(0);

		await expect(page.locator(".collapsible-panel-title", { hasText: /requirement analysis/i })).toBeVisible();
		await expect(page.getByRole("heading", { name: /test-case workflow diagnostics/i })).toBeVisible();

		await page.getByRole("button", { name: /^Next$/ }).click();

		const jsonButton = page.getByRole("button", { name: /json/i }).first();
		const download = await Promise.all([
			page.waitForEvent("download"),
			jsonButton.click(),
		]).then(([item]) => item);

		const downloadPath = path.join(os.tmpdir(), `tcg-e2e-${Date.now()}.json`);
		await download.saveAs(downloadPath);
		const exported = JSON.parse(await fs.readFile(downloadPath, "utf8"));
		const testCases = exported.test_cases || [];

		const quality = {
			total: testCases.length,
			withDescriptions: testCases.filter((tc) => tc.description?.trim()).length,
			withExpectedResults: testCases.filter((tc) => tc.expected_result?.trim()).length,
			withRequirementTags: testCases.filter(
				(tc) => Array.isArray(tc.tags) && tc.tags.some((tag) => /^REQ-\d+/i.test(tag)),
			).length,
			withTwoOrMoreSteps: testCases.filter((tc) => Array.isArray(tc.steps) && tc.steps.length >= 2).length,
			charFragmentCases: testCases.filter((tc) => {
				if (!Array.isArray(tc.steps) || tc.steps.length <= 10) {
					return false;
				}
				const tinyActions = tc.steps.filter((step) => (step?.action?.trim()?.length || 0) <= 2).length;
				return tinyActions >= Math.ceil(tc.steps.length * 0.4);
			}).map((tc) => tc.id),
			invalidPriorities: testCases.filter((tc) => !allowedPriorities.has(tc.priority)).map((tc) => tc.id),
			invalidTypes: testCases.filter((tc) => !allowedTypes.has(tc.type)).map((tc) => tc.id),
			untitledCases: testCases.filter(
				(tc) => !tc.title?.trim() || /untitled/i.test(tc.title),
			).map((tc) => tc.id),
		};

		test.info().annotations.push({
			type: "quality-summary",
			description: JSON.stringify(quality),
		});
			console.log("Generated test case quality summary:", quality);

		expect(quality.total).toBeGreaterThan(0);
		expect(quality.withDescriptions).toBe(quality.total);
		expect(quality.withExpectedResults).toBe(quality.total);
		expect(quality.withRequirementTags).toBe(quality.total);
			expect(quality.withTwoOrMoreSteps).toBeGreaterThanOrEqual(
				Math.ceil(quality.total * minimumStructuredCaseRatio),
			);
			expect(quality.charFragmentCases).toEqual([]);
		expect(quality.invalidPriorities).toEqual([]);
		expect(quality.invalidTypes).toEqual([]);
		expect(quality.untitledCases).toEqual([]);
	});
});
