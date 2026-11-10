import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";
import { buildProjectWorkspacePath } from "./workflowRoutes";

const SAVED_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$/;
const validId = (value) => typeof value === "string" && SAVED_ID.test(value);

export async function readExactSavedExecutionRun({ projectId, runRecordId, expectedSuiteSnapshotId, request, signal }) {
	const endpoint = API_CONTRACT_ENDPOINTS.projectExecutionRun;
	const path = endpoint.path
		.replace("{project_id}", encodeURIComponent(projectId))
		.replace("{run_record_id}", encodeURIComponent(runRecordId));
	const response = await request(path, { method: endpoint.method, signal });
	if (response.status === 404 || response.status === 403) return null;
	if (!response.ok) throw new Error("Run lookup failed.");
	const run = await response.json();
	if (
		run?.project_id !== projectId ||
		run?.run_record_id !== runRecordId ||
		!run?.run_id ||
		(expectedSuiteSnapshotId !== undefined && run.source_snapshot_id !== expectedSuiteSnapshotId)
	)
		throw new Error("The saved evidence did not match the selected run.");
	return run;
}

export function hasExecutionFindingEvidence(run) {
	return validId(run?.snapshot_id) && validId(run.run_record_id) && Array.isArray(run.results) && ["failed", "flaky"].includes(run.status);
}

export function buildExecutionFindingsPath(projectId, run) {
	const query = new URLSearchParams({ finding_source: run.snapshot_id, finding_run: run.run_record_id });
	return `${buildProjectWorkspacePath(projectId, "inputs", "context")}?${query}#quality-feedback-heading`;
}

export function readExecutionFindingsLink(search) {
	const query = new URLSearchParams(search);
	const sources = query.getAll("finding_source");
	const runs = query.getAll("finding_run");
	if (!sources.length && !runs.length) return null;
	const valid =
		sources.length === 1 &&
		runs.length === 1 &&
		validId(sources[0]) &&
		validId(runs[0]) &&
		!query.has("prerequisite") &&
		!query.has("setup");
	return { snapshotId: valid ? sources[0] : "", runRecordId: valid ? runs[0] : "" };
}
