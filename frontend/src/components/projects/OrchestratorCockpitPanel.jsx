import { Alert, Disclosure } from "../ui/surfaces";
import ContextualTaskCard from "./ContextualTaskCard";
import { selectContextualTask } from "./contextualTask";

export default function OrchestratorCockpitPanel({
	currentProject,
	hasTestCases = false,
	status,
	currentDestination,
	isOverview = false,
	compact = false,
	hidden = false,
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

	const { primaryAction, secondaryActions: originalSecondaryActions } = selectContextualTask(status, {
		destination: currentDestination,
		overview: isOverview,
	});

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
	if (!primaryAction && !secondaryActions.length && !error) {
		return null;
	}

	const content = (
		<section className="contextual-task-region" aria-label={compact ? "Test suite actions" : "Contextual task"}>
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
					focusFallbackRef={focusFallbackRef}
					onAction={onAction}
				/>
			) : null}
		</section>
	);
	return !compact && !primaryAction && !error ? (
		<Disclosure className="optional-workflow-actions">
			<summary>More actions</summary>
			{content}
		</Disclosure>
	) : (
		content
	);
}
