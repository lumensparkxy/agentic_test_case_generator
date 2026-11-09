export function generationRequirementRows(rows) {
	return rows.filter(
		(row) =>
			row.lifecycle_status !== "retired" && row.review_status !== "Rejected" && !["deferred", "excluded"].includes(row.clarification_status)
	);
}

export function hasApprovedGenerationScope(project) {
	const snapshot = project?.current_snapshots?.requirements;
	const state = project?.stage_state?.requirements;
	if (!snapshot || state?.current_snapshot_id !== snapshot.snapshot_id || state.stale) return false;
	const rows = snapshot.payload?.requirements || [];
	const eligible = generationRequirementRows(rows);
	const explicitScope = rows.some((row) => ["deferred", "excluded"].includes(row.clarification_status));
	return Boolean(
		(state.approved || explicitScope) &&
		eligible.length &&
		eligible.every((row) => row.review_status === "Approved" && row.clarification_status !== "open")
	);
}
