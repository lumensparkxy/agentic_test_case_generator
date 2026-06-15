import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { chromium, test as base } from "@playwright/test";

import { AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT } from "./auth.js";

const PROJECT_PROFILE_ROOT = path.join(os.homedir(), ".e2e-browser-profiles", "agentic_test_case_generator");
const DEFAULT_EDGE_PROFILE_DIR = path.join(PROJECT_PROFILE_ROOT, "msedge");
const MAC_EDGE_EXECUTABLE = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge";

function expandHome(value) {
	if (!value) {
		return value;
	}
	if (value === "~") {
		return os.homedir();
	}
	if (value.startsWith("~/")) {
		return path.join(os.homedir(), value.slice(2));
	}
	return value;
}

export const edgeProfileDir = path.resolve(expandHome(process.env.E2E_EDGE_PROFILE_DIR) || DEFAULT_EDGE_PROFILE_DIR);

function canUseLocalJwtFallback() {
	return process.env.AUTH_TOKEN_MODE === AUTH_TOKEN_MODE_FIREBASE_OR_BACKEND_JWT && Boolean(process.env.JWT_SECRET_KEY);
}

function isProfileLockedError(error) {
	return /Opening in existing browser session|profile is already in use/i.test(error?.message || "");
}

async function launchEdgePersistentContext(profileDir) {
	await fs.mkdir(profileDir, { recursive: true });
	return chromium.launchPersistentContext(profileDir, {
		acceptDownloads: true,
		channel: "msedge",
		headless: false,
		ignoreHTTPSErrors: process.env.E2E_IGNORE_HTTPS_ERRORS === "1",
		viewport: { width: 1440, height: 950 },
	});
}

export async function getSystemEdgeVersion() {
	try {
		await fs.access(MAC_EDGE_EXECUTABLE);
		return execFileSync(MAC_EDGE_EXECUTABLE, ["--version"], { encoding: "utf8" }).trim();
	} catch (error) {
		throw new Error(`System Microsoft Edge is not available at ${MAC_EDGE_EXECUTABLE}: ${error.message}`, { cause: error });
	}
}

export const test = base.extend({
	context: async ({ browserName }, use) => {
		void browserName;
		const edgeVersion = await getSystemEdgeVersion();
		let activeProfileDir = edgeProfileDir;
		let profileMode = "saved-edge-profile";
		let temporaryProfileDir = null;
		let context;

		try {
			context = await launchEdgePersistentContext(activeProfileDir);
		} catch (error) {
			if (!isProfileLockedError(error) || !canUseLocalJwtFallback()) {
				throw error;
			}
			temporaryProfileDir = await fs.mkdtemp(path.join(os.tmpdir(), "agentic-tcg-edge-e2e-"));
			activeProfileDir = temporaryProfileDir;
			profileMode = "temporary-backend-jwt-fallback-profile";
			context = await launchEdgePersistentContext(activeProfileDir);
		}

		context.setDefaultTimeout(30_000);
		context.setDefaultNavigationTimeout(120_000);
		context.__edgeExecutionDetails = {
			browserUsed: edgeVersion,
			browserChannel: "msedge",
			headless: false,
			profileDir: activeProfileDir,
			profileMode,
		};

		try {
			await use(context);
		} finally {
			await context.close();
			if (temporaryProfileDir) {
				await fs.rm(temporaryProfileDir, { recursive: true, force: true });
			}
		}
	},
	page: async ({ context }, use) => {
		const page = context.pages()[0] || (await context.newPage());
		await use(page);
	},
});

export { expect } from "@playwright/test";
