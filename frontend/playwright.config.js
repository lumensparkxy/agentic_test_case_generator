import { defineConfig } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL || "http://localhost:5173";

export default defineConfig({
	testDir: "./e2e",
	timeout: 10 * 60 * 1000,
	expect: {
		timeout: 30 * 1000,
	},
	retries: 1,
	forbidOnly: Boolean(process.env.CI),
	workers: process.env.CI ? 1 : undefined,
	reporter: [["list"], ["html", { open: "never" }]],
	use: {
		baseURL,
		headless: true,
		trace: "on-first-retry",
		screenshot: "only-on-failure",
		video: "retain-on-failure",
	},
});
