import { buildProjectWorkspacePath } from "./workflowRoutes";

export function getCurrentDeclaredBrowserSetup(project) {
	const suite = project?.current_snapshots?.test_cases;
	const stage = project?.stage_state?.test_cases;
	const quality = project?.suite_quality;
	if (
		project?.status !== "active" ||
		!suite?.snapshot_id ||
		suite.project_id !== project.project_id ||
		suite.stage !== "test_cases" ||
		suite.snapshot_id !== stage?.current_snapshot_id ||
		stage.stale ||
		quality?.suite_snapshot_id !== suite.snapshot_id ||
		quality.source_freshness !== "current" ||
		quality.declared_setup !== "blocked"
	)
		return null;

	const blockers = (suite.payload?.test_cases || []).flatMap((testCase) =>
		testCase.id && testCase.status !== "Deprecated"
			? (testCase.execution_prerequisites || [])
					.filter((prerequisite) => prerequisite.name && prerequisite.status === "missing")
					.map((prerequisite) => ({ testCase, prerequisite }))
			: []
	);
	return { snapshotId: suite.snapshot_id, blockers };
}

export function buildDeclaredBrowserPrerequisitePath(projectId, snapshotId, caseId, name) {
	const query = new URLSearchParams({ snapshot: snapshotId, case: caseId, prerequisite: name, setup: "declared" });
	return `${buildProjectWorkspacePath(projectId, "inputs", "context")}?${query}#quality-feedback-heading`;
}
