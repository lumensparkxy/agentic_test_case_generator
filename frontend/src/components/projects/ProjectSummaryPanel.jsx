const normalizeList = (value) => (Array.isArray(value) ? value : []);

const formatDateTime = (value) => {
	if (!value) return "";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "";
	return date.toLocaleString(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	});
};

export default function ProjectSummaryPanel({ currentProject, status, runsPayload }) {
	const runs = normalizeList(runsPayload?.runs);
	const latestRun = runs[0] || currentProject?.execution_runs?.[0] || null;
	const changedItemCount =
		status?.stages?.[status?.current_stage]?.summary?.changed_item_count ??
		currentProject?.stage_state?.impact_analysis?.metadata?.changed_item_count ??
		0;

	const items = [
		{
			label: "Baseline suite",
			value: status?.has_baseline_test_suite ? "Present" : "Not generated",
		},
		{
			label: "Changed items",
			value: changedItemCount,
		},
		{
			label: "Last run",
			value: latestRun
				? formatDateTime(latestRun.updated_at || latestRun.completed_at || latestRun.started_at) || latestRun.status
				: "None",
		},
	];

	return (
		<div className="orchestrator-summary-grid project-summary-panel" aria-label="Project summary">
			{items.map((item) => (
				<div key={item.label}>
					<span>{item.label}</span>
					<strong>{item.value}</strong>
				</div>
			))}
		</div>
	);
}
