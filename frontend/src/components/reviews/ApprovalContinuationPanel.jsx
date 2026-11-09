import RouteLink from "../../app/RouteLink";
import { Button } from "../ui/controls";
import { Surface } from "../ui/surfaces";

const SCENARIO_STATUS = {
	queued: "Scope approved. Scenario preparation is queued.",
	running: "Preparing scenarios from the approved scope.",
	awaiting_user: "Scenarios are ready for review.",
	completed: "Scenario preparation completed.",
	failed: "Scenario preparation could not finish. Review the current sources before starting again.",
	interrupted: "Scenario preparation was interrupted. The provider outcome is unconfirmed.",
	cancelled: "Scenario preparation was cancelled.",
	superseded: "Project sources changed. Review the current scope.",
};

const TEST_STATUS = {
	queued: "Scenarios approved. Test case generation is queued.",
	running: "Generating test cases from the approved scenarios.",
	completed: "Test cases are saved for review.",
	failed: "Test case generation could not finish. Check the approved scenarios and current sources.",
	interrupted: "Test case generation was interrupted. The provider outcome is unconfirmed.",
	cancelled: "Test case generation was cancelled.",
	superseded: "Project sources changed. Review the current scenarios.",
};

const BLOCKER = {
	project_inactive: "Restore the project before preparing scenarios.",
	requirements_not_current: "Review the current source before preparing scenarios.",
	requirements_not_eligible: "Resolve or defer the remaining scope questions before preparing scenarios.",
	context_not_current: "Analyze context again before preparing scenarios.",
	journey_budget_missing: "Test generation requires a scenario plan prepared from approved scope.",
	journey_budget_exhausted: "The automatic request budget for this scenario plan has been allocated.",
};

export default function ApprovalContinuationPanel({ continuation, scenariosPath, testsPath, navigate, onCheck, saving, resultCurrent }) {
	const { run, active, unresolved, blockedReason, error, cancelling, cancel, stage } = continuation;
	if (!run && !unresolved && !blockedReason && !error && !saving) return null;

	const generatingTests = stage === "generate_tests";
	const title = generatingTests ? "Test case generation" : "Scenario preparation";
	const action = generatingTests ? "test generation" : "preparation";
	const resultState = generatingTests ? "completed" : "awaiting_user";
	const resultReady = run?.state === resultState && run.result_snapshot_id && !blockedReason;
	const status = generatingTests ? TEST_STATUS : SCENARIO_STATUS;
	let message = status[run?.state];
	if (saving) message = "Saving scope approval…";
	else if (unresolved) {
		message = generatingTests
			? "Could not confirm the scenario approval response. Check test generation status."
			: "Could not confirm the approval response. Check preparation status.";
	} else if (blockedReason) {
		const approved = generatingTests ? "Scenario approval saved." : "Scope approval saved.";
		const next = generatingTests
			? "Check the current project before generating tests."
			: "Check the current project before preparing scenarios.";
		message = [approved, BLOCKER[blockedReason] || next].join(" ");
	} else if (resultReady && !resultCurrent) {
		message = generatingTests
			? "A test suite was saved. Check test generation status to load the current evidence."
			: "A scenario plan was saved. Check preparation status to load the current evidence.";
	}
	return (
		<Surface as="section" className="project-sync-summary" aria-label={title}>
			<h2>{title}</h2>
			{message && <p role="status">{message}</p>}
			{error && <p role="alert">{error}</p>}
			<div className="workbench-actions">
				<Button variant="secondary" size="compact" disabled={saving || cancelling} onClick={onCheck}>
					Check {action} status
				</Button>
				{(active || run?.state === "interrupted") && (
					<Button variant="secondary" size="compact" disabled={cancelling} onClick={() => void cancel()}>
						Cancel {action}
					</Button>
				)}
				{resultReady && resultCurrent && (
					<RouteLink to={generatingTests ? testsPath : scenariosPath} navigate={navigate}>
						{generatingTests ? "Open generated tests" : "Open prepared scenarios"}
					</RouteLink>
				)}
			</div>
		</Surface>
	);
}
