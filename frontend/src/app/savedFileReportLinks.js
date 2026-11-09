import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";
import { buildProjectPath, buildProjectWorkspacePath, PROJECT_DESTINATIONS } from "./workflowRoutes";

const PROJECT_WORKSPACES_V2 = import.meta.env.VITE_PROJECT_WORKSPACES_V2 === "true";
const FILE_OPERATIONS = new Set(["export.csv", "export.excel", "export.json"]);
const REPORT_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$/;

function validReportId(value) {
	return typeof value === "string" && REPORT_ID.test(value);
}

export function getSavedFileReportId(operation, snapshotId) {
	return FILE_OPERATIONS.has(operation) && validReportId(snapshotId) ? snapshotId : "";
}

export function buildSavedFileReportPath(projectId, reportId) {
	const path = PROJECT_WORKSPACES_V2
		? buildProjectWorkspacePath(projectId, "deliver")
		: buildProjectPath(projectId, PROJECT_DESTINATIONS.REPORTS);
	return reportId ? `${path}?${new URLSearchParams({ report: reportId })}` : path;
}

export function readSavedFileReportLink(search) {
	const query = new URLSearchParams(search);
	if (!query.has("report")) return null;
	const ids = query.getAll("report");
	return { reportId: ids.length === 1 && validReportId(ids[0]) && !query.has("run") ? ids[0] : "" };
}

export async function readExactSavedFileReport({ projectId, reportId, request, signal }) {
	const endpoint = API_CONTRACT_ENDPOINTS.projectSavedFileReport;
	const path = endpoint.path.replace("{project_id}", encodeURIComponent(projectId)).replace("{report_id}", encodeURIComponent(reportId));
	const response = await request(path, { method: endpoint.method, signal });
	if ([403, 404, 422].includes(response.status)) return null;
	if (!response.ok) throw new Error("Saved file report lookup failed.");
	const report = await response.json();
	return report?.project_id === projectId && report?.report_id === reportId && FILE_OPERATIONS.has(`export.${report.format}`)
		? report
		: null;
}
