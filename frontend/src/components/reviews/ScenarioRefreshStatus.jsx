import { Button } from "../ui/controls";
import { Surface } from "../ui/surfaces";

function messageFor(refresh, currentRevision, reviewApproved) {
	if (refresh.checking) return "Checking scenario refresh status…";
	if (refresh.attempt?.state === "interrupted")
		return "The refresh was interrupted. A provider request may have been issued. Acknowledge and release the attempt before starting another refresh.";
	if (refresh.attempt?.state === "running")
		return "Refreshing the selected scenario groups. If you cancel, requests already issued can finish.";
	if (refresh.submitting)
		return "Refreshing the selected scenario groups and reviewing the combined plan. The previous plan and suite remain saved.";
	if (refresh.pending) return "Could not confirm the refresh response. Check scenario refresh status to recover the exact result.";
	if (refresh.result) {
		if (refresh.currentResult) {
			const { refreshed_requirement_ids: refreshed, retained_requirement_ids: retained, removed_requirement_ids: removed } = refresh.result;
			const decision = reviewApproved
				? "The refreshed scenarios and human decision are saved."
				: "Refreshed scenarios are ready for human review.";
			return `${decision} ${refreshed.length} groups refreshed, ${retained.length} kept, and ${removed.length} removed.`;
		}
		return refresh.result.project_revision <= currentRevision
			? "The refresh is saved in an earlier Use Cases snapshot."
			: "The refresh was saved. Check scenario refresh status to load the current plan.";
	}
	if (refresh.blocked) return "Another project operation is in progress. Check scenario refresh status after it finishes.";
	if (refresh.statusFresh && !refresh.enabled) return "Scoped refresh is unavailable for the current project settings.";
	if (refresh.statusFresh && !refresh.eligible)
		return "Scoped refresh needs a verified prior plan and current reviewed Requirements and Context.";
	return "Check scenario refresh status to continue with the saved sources.";
}

export default function ScenarioRefreshStatus({ refresh, snapshotId, currentRevision, reviewApproved, disabled = false }) {
	if (!(
		refresh &&
		(refresh.attempt ||
			refresh.pending ||
			refresh.result ||
			refresh.error ||
			refresh.blocked ||
			refresh.checking ||
			(refresh.statusFresh && !refresh.eligible))
	))
		return null;
	return (
		<Surface as="section" className="project-sync-summary" aria-label="Scenario refresh status">
			<h2>Scenario refresh status</h2>
			<p role="status">{messageFor(refresh, currentRevision, reviewApproved)}</p>
			{refresh.attempt?.source_snapshot_id && refresh.attempt.source_snapshot_id !== snapshotId && (
				<p>This attempt uses an earlier saved plan.</p>
			)}
			{refresh.error && <p role="alert">{refresh.error}</p>}
			<div className="workbench-actions">
				<Button variant="secondary" size="compact" disabled={refresh.checking} onClick={() => void refresh.check()}>
					Check scenario refresh status
				</Button>
				{refresh.attempt && (
					<Button
						variant="secondary"
						size="compact"
						disabled={refresh.checking || !refresh.statusFresh}
						onClick={() => void refresh.release()}
					>
						{refresh.attempt.state === "interrupted" ? "Acknowledge interrupted scenario refresh" : "Cancel scenario refresh"}
					</Button>
				)}
				{refresh.canRetry && (
					<Button size="compact" disabled={disabled} onClick={() => void refresh.retry()}>
						Retry scoped refresh
					</Button>
				)}
				{refresh.dismiss && (
					<Button variant="secondary" size="compact" disabled={refresh.checking} onClick={() => refresh.dismiss()}>
						Dismiss stale refresh request
					</Button>
				)}
			</div>
		</Surface>
	);
}
