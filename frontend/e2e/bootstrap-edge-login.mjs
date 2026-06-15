import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const PROJECT_PROFILE_ROOT = path.join(os.homedir(), ".e2e-browser-profiles", "agentic_test_case_generator");
const DEFAULT_EDGE_PROFILE_DIR = path.join(PROJECT_PROFILE_ROOT, "msedge");
const EDGE_APP_NAME = "Microsoft Edge";

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

const profileDir = path.resolve(expandHome(process.env.E2E_EDGE_PROFILE_DIR) || DEFAULT_EDGE_PROFILE_DIR);
const baseURL = process.env.E2E_BASE_URL || "http://127.0.0.1:5173";

await fs.mkdir(profileDir, { recursive: true });

const child = spawn("open", ["-na", EDGE_APP_NAME, "--args", `--user-data-dir=${profileDir}`, "--no-first-run", baseURL], {
	detached: true,
	stdio: "ignore",
});

child.unref();

console.log(`Opened system Microsoft Edge for manual E2E login.`);
console.log(`URL: ${baseURL}`);
console.log(`Profile path: ${profileDir}`);
console.log(
	"Complete the provider login in that Edge window, verify the app shows Sign Out, then close the window before running the E2E spec."
);
