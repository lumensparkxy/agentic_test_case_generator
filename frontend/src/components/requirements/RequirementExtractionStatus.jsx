import { Button } from "../ui/controls";
import { Surface } from "../ui/surfaces";

const OPERATION_LABELS = {
	"requirements.refine": "Requirement refinement",
	"requirements.import.jira": "Jira requirements import",
	"requirements.import.azure_devops": "Azure DevOps requirements import",
};

export default function RequirementExtractionStatus({ status, submitting, extractionError, requirementsSnapshotId }) {
	const { attempt, ready, managed, sharedBlocked, statusError, actionError, note, releaseNote, busy, canRelease, check, release } = status;
	if (
		!managed ||
		!(attempt || !ready || sharedBlocked || statusError || actionError || note || releaseNote || busy || submitting || extractionError)
	)
		return null;
	const operation = OPERATION_LABELS[attempt?.operation] || "Requirement extraction";
	const earlierSource = attempt?.source_snapshot_id && attempt.source_snapshot_id !== requirementsSnapshotId;
	let message = releaseNote || note || "No extraction or refinement is active.";
	if (attempt?.state === "interrupted") {
		message = `${operation} was interrupted. Provider usage remains unconfirmed.`;
	} else if (attempt?.state === "running") {
		message = `${operation} is running.`;
	} else if (sharedBlocked) {
		message = "Another project workflow is active or awaiting recovery.";
	} else if (submitting) {
		message = "Waiting for the requirements preview.";
	} else if (!ready) {
		message = statusError ? "Extraction status is unavailable." : "Checking extraction status.";
	}
	return (
		<Surface as="section" className="project-sync-summary" aria-label="Requirement extraction">
			<h2>Requirement extraction</h2>
			<p role="status">{message}</p>
			{releaseNote && message !== releaseNote && <p>{releaseNote}</p>}
			{earlierSource && <p>This attempt uses an earlier requirements snapshot.</p>}
			{(actionError || statusError) && <p role="alert">{actionError || statusError}</p>}
			{extractionError && <p role="alert">{extractionError}</p>}
			<div className="workbench-actions">
				<Button variant="secondary" size="compact" disabled={busy} onClick={check}>
					Check extraction status
				</Button>
				{attempt?.state === "interrupted" && (
					<Button variant="secondary" size="compact" disabled={!canRelease} onClick={release}>
						Acknowledge and release
					</Button>
				)}
			</div>
		</Surface>
	);
}
