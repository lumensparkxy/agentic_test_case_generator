import RouteLink from "../../app/RouteLink";
import { Button } from "../ui/controls";
import { Surface } from "../ui/surfaces";

const STATUS = {
	queued: "Scope approved. Scenario preparation is queued.",
	running: "Preparing scenarios from the approved scope.",
	awaiting_user: "Scenarios are ready for review.",
	completed: "Scenario preparation completed.",
	failed: "Scenario preparation could not finish. Review the current sources before starting again.",
	interrupted: "Scenario preparation was interrupted. The provider outcome is unconfirmed.",
	cancelled: "Scenario preparation was cancelled.",
	superseded: "Project sources changed. Review the current scope.",
};

const BLOCKER = {
	project_inactive: "Restore the project before preparing scenarios.",
	requirements_not_current: "Review the current source before preparing scenarios.",
	requirements_not_eligible: "Resolve or defer the remaining scope questions before preparing scenarios.",
	context_not_current: "Analyze context again before preparing scenarios.",
};

export default function ApprovalContinuationPanel({ continuation, scenariosPath, navigate, onCheck, saving, resultCurrent }) {
	const { run, active, unresolved, blockedReason, error, cancelling, cancel } = continuation;
	if (!run && !unresolved && !blockedReason && !error && !saving) return null;

	const message = saving
		? "Saving scope approval…"
		: unresolved
			? "Could not confirm the approval response. Check preparation status."
			: blockedReason
				? `Scope approval saved. ${BLOCKER[blockedReason] || "Check the current project before preparing scenarios."}`
				: run?.state === "awaiting_user" && !resultCurrent
					? "A scenario plan was saved. Check preparation status to load the current evidence."
					: STATUS[run?.state];
	return (
		<Surface as="section" className="project-sync-summary" aria-label="Scenario preparation">
			<h2>Scenario preparation</h2>
			{message && <p role="status">{message}</p>}
			{error && <p role="alert">{error}</p>}
			<div className="workbench-actions">
				<Button variant="secondary" size="compact" disabled={saving || cancelling} onClick={onCheck}>
					Check preparation status
				</Button>
				{(active || run?.state === "interrupted") && (
					<Button variant="secondary" size="compact" disabled={cancelling} onClick={() => void cancel()}>
						Cancel preparation
					</Button>
				)}
				{run?.state === "awaiting_user" && run.result_snapshot_id && resultCurrent && (
					<RouteLink to={scenariosPath} navigate={navigate}>
						Open prepared scenarios
					</RouteLink>
				)}
			</div>
		</Surface>
	);
}
