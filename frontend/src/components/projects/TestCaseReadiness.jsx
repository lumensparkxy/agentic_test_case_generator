import { Button } from "../ui/controls";

export default function TestCaseReadiness({ project, status, unavailable, caseCount, taskCount, onReview }) {
	const requirements = project?.stage_state?.requirements;
	const useCases = project?.stage_state?.use_cases;
	const humanReview = useCases?.metadata?.latest_human_review;
	const requirementsReady = Boolean(requirements?.current_snapshot_id && requirements.approved && !requirements.stale);
	const useCasesReady =
		!useCases?.current_snapshot_id ||
		(useCases.approved &&
			!useCases.stale &&
			humanReview?.snapshot_id === useCases.current_snapshot_id &&
			humanReview?.decision === "approve");
	const statusCurrent = Boolean(status && status.project_revision === project?.current_revision && !unavailable);
	const generateAction =
		statusCurrent && status.next_actions?.find((action) => action.action === "generate" && action.stage === "test_cases");
	const draftAction = statusCurrent && status.next_actions?.find((action) => action.action === "full_regenerate" && action.enabled);
	let title = "Generation readiness unavailable";
	let destination = null;
	let actionLabel = "";
	if (!requirementsReady) {
		title = "Requirements review needed";
		destination = 0;
		actionLabel = "Review requirements";
	} else if (!useCasesReady) {
		title = useCases.stale ? "Use Cases need refresh and review" : "Awaiting Use Cases review";
		destination = 6;
		actionLabel = "Review Use Cases";
	} else if (taskCount) {
		title = "Draft incomplete";
	} else if (statusCurrent && !caseCount && (generateAction?.enabled || status.next_actions?.length === 0)) {
		title = "Ready to generate test cases";
	} else if (statusCurrent && caseCount) {
		title = "Review the delivered suite";
	}
	return (
		<div className="generation-gate-card" role="region" aria-label="Test Cases readiness">
			<div>
				<strong>{title}</strong>
				<p>
					{caseCount} concrete test cases · {taskCount} unfinished generation tasks
				</p>
				{taskCount > 0 && title !== "Draft incomplete" && <p>Draft incomplete. Review the gaps below before approving the suite.</p>}
				{!statusCurrent && <p>Latest workflow readiness is unavailable. Reload the project before generating.</p>}
				{draftAction && !caseCount && !generateAction?.enabled && (
					<p>Draft generation is available under More actions. Use Cases review is still required for the normal generation path.</p>
				)}
			</div>
			{destination !== null && (
				<Button type="button" className="secondary small" onClick={() => onReview(destination)}>
					{actionLabel}
				</Button>
			)}
		</div>
	);
}
