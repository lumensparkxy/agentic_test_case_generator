import { Alert, Badge, Disclosure } from "../ui/surfaces";
import { PROJECT_DESTINATIONS } from "../../app/workflowRoutes";
import ContextualTaskCard from "./ContextualTaskCard";
import { selectContextualTask } from "./contextualTask";

const PROJECT_WORKSPACES_V2 = import.meta.env.VITE_PROJECT_WORKSPACES_V2 === "true";
const DESIGN_DELIVERY_DESTINATIONS = [PROJECT_DESTINATIONS.OVERVIEW, PROJECT_DESTINATIONS.TEST_CASES, PROJECT_DESTINATIONS.REPORTS];

export default function OrchestratorCockpitPanel({
	currentProject,
	hasTestCases = false,
	status,
	currentDestination,
	isOverview = false,
	compact = false,
	hidden = false,
	hidePrimary = false,
	isLoading,
	error,
	authActionDisabled,
	actionBusy,
	actionDisabled,
	focusFallbackRef,
	onAction,
}) {
	if (!currentProject || hidden) {
		return null;
	}

	const { primaryAction: scopedPrimary, secondaryActions: originalSecondaryActions } = selectContextualTask(status, {
		destination: currentDestination,
		overview: isOverview,
	});
	const primaryAction = hidePrimary ? null : scopedPrimary;

	const hasBaseline = Boolean(
		hasTestCases || currentProject.current_snapshots?.test_cases || currentProject.stage_state?.test_cases?.current_snapshot_id
	);
	const secondaryActions = originalSecondaryActions.map((action) =>
		compact && action.action === "full_regenerate" && !hasBaseline
			? {
					...action,
					label: "Generate draft",
					reason: "Generate a first draft from approved requirements. Use Cases review remains required for the normal generation path.",
					requiresReplacement: false,
				}
			: action
	);
	const reviewedDesignAvailable =
		PROJECT_WORKSPACES_V2 &&
		DESIGN_DELIVERY_DESTINATIONS.includes(currentDestination) &&
		!isLoading &&
		!error &&
		status?.reviewed_design_available === true &&
		status.project_id === currentProject.project_id &&
		status.project_revision === currentProject.current_revision;
	if (!primaryAction && !secondaryActions.length && !error && !reviewedDesignAvailable) {
		return null;
	}

	const availability = reviewedDesignAvailable ? <Badge tone="info">Reviewed design available</Badge> : null;
	const showOptionalActions = !compact && !primaryAction && !error && secondaryActions.length > 0;
	const content = (
		<section className="contextual-task-region" aria-label={compact ? "Test suite actions" : "Contextual task"}>
			{showOptionalActions ? null : availability}
			{error ? (
				<Alert as="div" tone="danger" className="orchestrator-error" role="alert">
					{error}
				</Alert>
			) : null}
			{primaryAction || secondaryActions.length ? (
				<ContextualTaskCard
					action={primaryAction}
					secondaryActions={secondaryActions}
					status={status}
					busyMap={actionBusy || {}}
					disabled={authActionDisabled || isLoading || Boolean(error) || status?.project_revision !== currentProject.current_revision}
					disabledMap={isOverview ? {} : actionDisabled || {}}
					navigationOnly={isOverview}
					compact={compact}
					reviewedDesignAvailable={reviewedDesignAvailable}
					focusFallbackRef={focusFallbackRef}
					onAction={onAction}
				/>
			) : null}
		</section>
	);
	return showOptionalActions ? (
		<>
			{availability}
			<Disclosure className="optional-workflow-actions">
				<summary>More actions</summary>
				{content}
			</Disclosure>
		</>
	) : (
		content
	);
}
