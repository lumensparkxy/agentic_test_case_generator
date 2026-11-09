import RouteLink from "../../app/RouteLink";
import { buildSavedCasePath } from "../../app/savedCaseLinks";
import { List, ListItem } from "../ui/collections";
import { Button } from "../ui/controls";
import { Disclosure, Surface } from "../ui/surfaces";

function statusMessage(application, revision) {
	if (application.checking) return "Checking application status…";
	if (application.attempt?.state === "interrupted")
		return "The application was interrupted. Provider requests may have been issued. Acknowledge and release this attempt before applying updates again.";
	if (application.attempt?.state === "running")
		return "Applying the selected direct updates. If you cancel, requests already issued can finish.";
	if (application.submitting) return "Applying the selected direct updates. The prior suite remains saved.";
	if (application.pending) return "Could not confirm the application response. Check application status to recover the exact result.";
	if (application.result) {
		if (application.resultCurrent) return "The previous and revised case versions are available as saved evidence.";
		return application.result.project_revision <= revision
			? "This application belongs to an earlier saved suite."
			: "The application was saved. Check application status to load the current suite.";
	}
	if (application.blocked) return "Another project operation is in progress. Check application status after it finishes.";
	return "Check application status for the current analysis.";
}

export default function ScopedImpactStatus({ application, projectId, analysisId, revision, navigate, disabled }) {
	if (!(application && (application.active || application.result || application.error || application.blocked || application.checking)))
		return null;
	const result = application.result;
	return (
		<Surface as="section" className="project-sync-summary" aria-label="Scoped impact application">
			<h2>Scoped impact application</h2>
			<p role="status">{statusMessage(application, revision)}</p>
			{application.attempt?.analysis_snapshot_id && application.attempt.analysis_snapshot_id !== analysisId && (
				<p>This attempt uses an earlier analysis.</p>
			)}
			{application.error && <p role="alert">{application.error}</p>}
			<div className="workbench-actions">
				<Button variant="secondary" size="compact" disabled={application.checking} onClick={() => void application.check()}>
					Check application status
				</Button>
				{application.attempt && (
					<Button
						variant="secondary"
						size="compact"
						disabled={application.checking || !application.statusFresh}
						onClick={() => void application.release()}
					>
						{application.attempt.state === "interrupted" ? "Acknowledge interrupted application" : "Cancel application"}
					</Button>
				)}
				{application.canRetry && (
					<Button size="compact" disabled={disabled} onClick={() => void application.retry()}>
						Retry scoped application
					</Button>
				)}
				{application.dismiss && (
					<Button variant="secondary" size="compact" disabled={application.checking} onClick={() => application.dismiss()}>
						Dismiss stale application request
					</Button>
				)}
			</div>
			{result && (
				<Disclosure>
					<summary>Previous and revised saved tests</summary>
					<List variant="grouped" aria-label="Saved case versions" className="scoped-impact-case-links">
						{result.changed_test_case_ids.map((caseId) => (
							<ListItem key={caseId}>
								<RouteLink to={buildSavedCasePath(projectId, result.source_snapshot_id, caseId)} navigate={navigate}>
									Original {caseId}
								</RouteLink>
								<RouteLink to={buildSavedCasePath(projectId, result.result_snapshot_id, caseId)} navigate={navigate}>
									Revised {caseId}
								</RouteLink>
							</ListItem>
						))}
					</List>
				</Disclosure>
			)}
		</Surface>
	);
}
