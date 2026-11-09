import { Disclosure, Surface } from "../ui/surfaces";
import "./SuiteQualitySummary.css";

const QUALITY_LABELS = {
	complete: "Complete",
	incomplete: "Incomplete",
	current: "Current",
	stale: "Stale",
	approved: "Approved",
	changes_requested: "Changes requested",
	unavailable: "Unavailable",
	blocked: "Blocked",
	not_assessed: "Not assessed",
};
const RUN_LABELS = {
	passed: "Passed",
	failed: "Failed",
	flaky: "Flaky",
	skipped: "Skipped",
	incomplete: "Incomplete",
	cancelled: "Cancelled",
	disabled: "Disabled",
	blocked: "Blocked",
};

export function findExactSuiteRun(project, suiteSnapshotId, liveRun) {
	const projectId = project?.project_id;
	if (!projectId || !suiteSnapshotId) return null;
	const matchesSuite = (run) => run?.run_id && run.source_snapshot_id === suiteSnapshotId;
	if (liveRun?.preview?.readiness?.project_id === projectId && matchesSuite(liveRun)) return liveRun;
	const snapshot = project?.current_snapshots?.execution;
	if (snapshot?.project_id === projectId && snapshot.source_snapshot_id === suiteSnapshotId && snapshot.payload?.run_id) {
		return { ...snapshot.payload, source_snapshot_id: snapshot.source_snapshot_id };
	}
	return project?.execution_runs?.find((run) => run.project_id === projectId && matchesSuite(run)) || null;
}

export default function SuiteQualitySummary({
	snapshotId,
	quality,
	sourceStale,
	designDeliveryEnabled,
	reviewedDesignAvailable,
	targetReadiness,
	executionRun,
}) {
	const fileDelivery = reviewedDesignAvailable
		? "Reviewed design ready"
		: designDeliveryEnabled === false
			? "Reviewed design disabled"
			: "Reviewed design unavailable";
	const rows = [
		["Independent design", QUALITY_LABELS[quality?.design_status] || "Unknown"],
		["Source freshness", sourceStale ? "Stale" : QUALITY_LABELS[quality?.source_freshness] || "Unknown"],
		["Scenario approval", QUALITY_LABELS[quality?.scenario_decision] || "Unavailable"],
		["Human suite acceptance", QUALITY_LABELS[quality?.human_acceptance] || "Unavailable"],
		["Declared setup", QUALITY_LABELS[quality?.declared_setup] || "Unknown"],
		["Target readiness", targetReadiness],
		["Execution result", executionRun ? RUN_LABELS[executionRun.status] || "Recorded" : "No matching run evidence"],
		["File delivery", fileDelivery],
	];

	return (
		<Surface as="section" className="project-sync-summary" aria-label="Saved suite quality">
			<h3 className="section-subtitle">Saved suite quality</h3>
			<dl className="suite-quality-evidence">
				{rows.map(([label, value]) => (
					<div key={label}>
						<dt>{label}</dt>
						<dd>{value}</dd>
					</div>
				))}
			</dl>
			<Disclosure className="suite-quality-references">
				<summary>Saved evidence references</summary>
				<p>
					Suite snapshot <code>{snapshotId}</code>
				</p>
				{quality?.scenario_snapshot_id && (
					<p>
						Scenario snapshot <code>{quality.scenario_snapshot_id}</code>
					</p>
				)}
				{quality?.scenario_review_id && (
					<p>
						Scenario decision <code>{quality.scenario_review_id}</code>
					</p>
				)}
				{executionRun?.run_id && (
					<p>
						Execution run <code>{executionRun.run_id}</code>
					</p>
				)}
			</Disclosure>
		</Surface>
	);
}
