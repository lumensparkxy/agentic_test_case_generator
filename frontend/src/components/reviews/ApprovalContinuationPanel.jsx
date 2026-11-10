import RouteLink from "../../app/RouteLink";
import { buildSavedCasePath } from "../../app/savedCaseLinks";
import { List, ListItem } from "../ui/collections";
import { Button } from "../ui/controls";
import { Surface } from "../ui/surfaces";
import "./ApprovalContinuationPanel.css";

const SCENARIO_STATUS = {
	queued: "Scope approved. Scenario preparation is queued.",
	running: "Preparing scenarios from the approved scope.",
	awaiting_user: "Scenarios are ready for review.",
	completed: "Scenario preparation completed.",
	failed: "Scenario preparation could not finish. Review the current sources before starting again.",
	interrupted: "Scenario preparation was interrupted.",
	cancelled: "Scenario preparation was cancelled.",
	superseded: "Project sources changed. Review the current scope.",
};

const TEST_STATUS = {
	queued: "Scenarios approved. Test case generation is queued.",
	running: "Generating test cases from the approved scenarios.",
	completed: "Test cases are saved for review.",
	failed: "Test case generation could not finish. Check the approved scenarios and current sources.",
	interrupted: "Test case generation was interrupted.",
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

export default function ApprovalContinuationPanel({
	continuation,
	scenariosPath,
	testsPath,
	navigate,
	onCheck,
	saving,
	resultCurrent,
	retryAvailable,
	savedPlanRequest,
	savedPlanGeneration,
	projectId,
	priorityCases = [],
	onOpenPriorityCase,
}) {
	const { active, unresolved, error, cancelling, cancel, retrying, retry, stage } = continuation;
	const savedPlanSurface = Boolean(savedPlanGeneration);
	const showRun =
		!savedPlanSurface || (stage === "generate_tests" && continuation.run?.source_snapshot_id === savedPlanGeneration.snapshotId);
	const run = showRun ? continuation.run : null;
	const blockedReason = showRun ? continuation.blockedReason : "";
	if (!run && !unresolved && !blockedReason && !error && !saving && !savedPlanSurface) return null;

	const generatingTests = stage === "generate_tests" || savedPlanSurface;
	const title = generatingTests ? "Test case generation" : "Scenario preparation";
	const action = generatingTests ? "test generation" : "preparation";
	const interrupted = run?.state === "interrupted";
	const resultState = generatingTests ? "completed" : "awaiting_user";
	const resultReady = run?.state === resultState && run.result_snapshot_id && !blockedReason;
	const status = generatingTests ? TEST_STATUS : SCENARIO_STATUS;
	let message = status[run?.state];
	if (savedPlanGeneration?.submitting) message = "Queueing test generation from the saved approval…";
	else if (saving) message = "Saving scope approval…";
	else if (unresolved) {
		message = generatingTests
			? savedPlanRequest
				? "Could not confirm the generation request. Check test generation status."
				: "Could not confirm the scenario approval response. Check test generation status."
			: "Could not confirm the approval response. Check preparation status.";
	} else if (blockedReason) {
		const approved = generatingTests ? "Scenario approval saved." : "Scope approval saved.";
		const next = generatingTests
			? "Check the current project before generating tests."
			: "Check the current project before preparing scenarios.";
		message = [approved, BLOCKER[blockedReason] || next].join(" ");
	} else if (showRun && retryAvailable) {
		message = generatingTests
			? "Scenario approval saved. Test case generation stopped before contacting the provider."
			: "Scope approval saved. Scenario preparation stopped before contacting the provider.";
	} else if (resultReady && !resultCurrent) {
		message = generatingTests
			? "A test suite was saved. Check test generation status to load the current evidence."
			: "A scenario plan was saved. Check preparation status to load the current evidence.";
	} else if (run?.provider_usage_unconfirmed) {
		message += " Provider usage remains unconfirmed.";
	} else if (interrupted) {
		message += " The provider outcome is unconfirmed.";
	} else if (!run && savedPlanGeneration) {
		message = savedPlanGeneration.start
			? "Generate the first test suite from the current saved scenario approval."
			: savedPlanGeneration.statusRead
				? "Test generation is unavailable for this saved approval. Check the current status."
				: "Checking test generation availability for the saved approval.";
	}
	return (
		<Surface as="section" className="project-sync-summary" aria-label={title}>
			<h2>{title}</h2>
			{message && <p role="status">{message}</p>}
			{error && <p role="alert">{error}</p>}
			{savedPlanGeneration?.error && <p role="alert">{savedPlanGeneration.error}</p>}
			<div className="workbench-actions">
				{savedPlanGeneration?.start && (
					<Button
						size="compact"
						disabled={savedPlanGeneration.disabled || savedPlanGeneration.submitting}
						onClick={savedPlanGeneration.start}
					>
						{unresolved ? "Retry generation request" : "Generate tests from saved approval"}
					</Button>
				)}
				<Button variant="secondary" size="compact" disabled={saving || cancelling || retrying} onClick={onCheck}>
					Check {action} status
				</Button>
				{run && (active || interrupted) && (
					<Button variant="secondary" size="compact" disabled={cancelling || retrying} onClick={() => void cancel()}>
						{interrupted ? "Acknowledge and release" : "Cancel"} {action}
					</Button>
				)}
				{showRun && retryAvailable && (
					<Button variant="secondary" size="compact" disabled={retrying} onClick={() => void retry()}>
						{retrying ? "Checking retry…" : generatingTests ? "Retry test generation" : "Retry scenario preparation"}
					</Button>
				)}
				{resultReady && resultCurrent && (
					<RouteLink to={generatingTests ? testsPath : scenariosPath} navigate={navigate}>
						{generatingTests ? "Open generated tests" : "Open prepared scenarios"}
					</RouteLink>
				)}
			</div>
			{generatingTests && resultReady && resultCurrent && priorityCases.length > 0 && (
				<div>
					<h3 className="section-subtitle">Priority saved tests</h3>
					<List className="priority-saved-tests" variant="grouped" role="list" aria-label="Priority saved tests">
						{priorityCases.map((item) => (
							<ListItem key={item.id}>
								<RouteLink
									to={buildSavedCasePath(projectId, run.result_snapshot_id, item.id)}
									navigate={() => onOpenPriorityCase(item.id)}
									aria-label={`${item.id} ${item.title}`}
								>
									<strong>{item.id}</strong>
									{item.title}
								</RouteLink>
							</ListItem>
						))}
					</List>
				</div>
			)}
		</Surface>
	);
}
