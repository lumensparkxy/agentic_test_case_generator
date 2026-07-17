import ContextualTaskCard from "./ContextualTaskCard";
import { selectContextualTask } from "./contextualTask";

export default function OrchestratorCockpitPanel({
	currentProject,
	status,
	currentDestination,
	isOverview = false,
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

	const { primaryAction, secondaryActions } = selectContextualTask(status, {
		destination: currentDestination,
		overview: isOverview,
	});

	if (!primaryAction && !secondaryActions.length && !error) {
		return null;
	}

	return (
		<section className="contextual-task-region" aria-label="Contextual task">
			{error ? (
				<div className="orchestrator-error" role="alert">
					{error}
				</div>
			) : null}
			{primaryAction || secondaryActions.length ? (
				<ContextualTaskCard
					action={primaryAction}
					secondaryActions={secondaryActions}
					status={status}
					busyMap={actionBusy || {}}
					disabled={authActionDisabled || isLoading}
					disabledMap={actionDisabled || {}}
					navigationOnly={isOverview}
					focusFallbackRef={focusFallbackRef}
					onAction={onAction}
				/>
			) : null}
		</section>
	);
}
